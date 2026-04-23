from datetime import datetime, timedelta
import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import AccessShareForm, LightScheduleForm
from .models import ActiveBulbOverride, Bulb, BulbAccess, ConflictEvent, ControlActivity, LightSchedule, PowerReading
from .services import apply_schedule, execute_control_request, get_bulb_power_range_summary, request_control_action, user_can_manage_schedule

User = get_user_model()


class SmartLightRuleEngineTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="pass12345")
        self.admin = User.objects.create_user(username="admin", password="pass12345")
        self.controller = User.objects.create_user(username="controller", password="pass12345")
        self.viewer = User.objects.create_user(username="viewer", password="pass12345")

        self.bulb = Bulb.objects.create(name="Desk Lamp", owner=self.owner)
        BulbAccess.objects.create(user=self.owner, bulb=self.bulb, role=BulbAccess.ROLE_OWNER)
        BulbAccess.objects.create(user=self.admin, bulb=self.bulb, role=BulbAccess.ROLE_ADMIN)
        BulbAccess.objects.create(user=self.controller, bulb=self.bulb, role=BulbAccess.ROLE_CONTROLLER)
        BulbAccess.objects.create(user=self.viewer, bulb=self.bulb, role=BulbAccess.ROLE_VIEWER)

    def test_higher_role_manual_override_blocks_lower_role_manual_command(self):
        owner_decision = execute_control_request(
            bulb=self.bulb,
            action=ControlActivity.ACTION_ON,
            requested_is_on=True,
            acted_by=self.owner,
            source_type=ControlActivity.SOURCE_MANUAL,
        )
        self.assertTrue(owner_decision.accepted)
        self.assertTrue(ActiveBulbOverride.objects.filter(bulb=self.bulb, is_active=True).exists())

        controller_decision = execute_control_request(
            bulb=self.bulb,
            action=ControlActivity.ACTION_OFF,
            requested_is_on=False,
            acted_by=self.controller,
            source_type=ControlActivity.SOURCE_MANUAL,
        )
        self.assertFalse(controller_decision.accepted)
        self.bulb.refresh_from_db()
        self.assertTrue(self.bulb.is_on)

        latest = ControlActivity.objects.order_by("-created_at").first()
        self.assertEqual(latest.outcome, ControlActivity.OUTCOME_REJECTED)
        self.assertIn("higher-priority manual override", latest.reason)


    def test_owner_override_creates_conflict_event_and_reason_code(self):
        first = execute_control_request(
            bulb=self.bulb,
            action=ControlActivity.ACTION_ON,
            requested_is_on=True,
            acted_by=self.controller,
            source_type=ControlActivity.SOURCE_MANUAL,
        )
        self.assertTrue(first.accepted)

        second = execute_control_request(
            bulb=self.bulb,
            action=ControlActivity.ACTION_OFF,
            requested_is_on=False,
            acted_by=self.owner,
            source_type=ControlActivity.SOURCE_MANUAL,
        )
        self.assertTrue(second.accepted)
        self.assertEqual(second.activity.reason_code, ControlActivity.REASON_OVERRIDDEN_BY_OWNER_ADMIN)
        self.assertTrue(second.activity.overrode_existing)
        self.assertTrue(ConflictEvent.objects.filter(
            bulb=self.bulb,
            conflict_type=ConflictEvent.TYPE_MANUAL_VS_MANUAL,
            reason_code=ControlActivity.REASON_OVERRIDDEN_BY_OWNER_ADMIN,
            winning_activity=second.activity,
        ).exists())

    def test_controller_can_manage_only_own_schedule(self):
        owner_schedule = LightSchedule.objects.create(
            bulb=self.bulb,
            created_by=self.owner,
            created_by_role=BulbAccess.ROLE_OWNER,
            created_by_role_priority=BulbAccess.role_priority(BulbAccess.ROLE_OWNER),
            target_is_on=True,
            repeat=False,
            scheduled_for=timezone.now() + timedelta(hours=1),
        )
        controller_schedule = LightSchedule.objects.create(
            bulb=self.bulb,
            created_by=self.controller,
            created_by_role=BulbAccess.ROLE_CONTROLLER,
            created_by_role_priority=BulbAccess.role_priority(BulbAccess.ROLE_CONTROLLER),
            target_is_on=True,
            repeat=False,
            scheduled_for=timezone.now() + timedelta(hours=2),
        )

        self.assertFalse(user_can_manage_schedule(self.controller, owner_schedule))
        self.assertTrue(user_can_manage_schedule(self.controller, controller_schedule))
        self.assertTrue(user_can_manage_schedule(self.admin, owner_schedule))

    def test_conflicting_schedules_use_higher_role_priority(self):
        due_at = timezone.now() - timedelta(seconds=1)
        controller_schedule = LightSchedule.objects.create(
            bulb=self.bulb,
            created_by=self.controller,
            created_by_role=BulbAccess.ROLE_CONTROLLER,
            created_by_role_priority=BulbAccess.role_priority(BulbAccess.ROLE_CONTROLLER),
            target_is_on=False,
            repeat=False,
            scheduled_for=due_at,
            next_run_at=due_at,
            enabled=True,
        )
        owner_schedule = LightSchedule.objects.create(
            bulb=self.bulb,
            created_by=self.owner,
            created_by_role=BulbAccess.ROLE_OWNER,
            created_by_role_priority=BulbAccess.role_priority(BulbAccess.ROLE_OWNER),
            target_is_on=True,
            target_brightness=75,
            repeat=False,
            scheduled_for=due_at,
            next_run_at=due_at,
            enabled=True,
        )

        apply_schedule(controller_schedule)
        apply_schedule(owner_schedule)

        self.bulb.refresh_from_db()
        self.assertTrue(self.bulb.is_on)
        self.assertEqual(self.bulb.brightness, 75)

    def test_schedule_is_rejected_while_manual_override_is_active(self):
        execute_control_request(
            bulb=self.bulb,
            action=ControlActivity.ACTION_ON,
            requested_is_on=True,
            requested_brightness=80,
            acted_by=self.owner,
            source_type=ControlActivity.SOURCE_MANUAL,
        )

        schedule = LightSchedule.objects.create(
            bulb=self.bulb,
            created_by=self.controller,
            created_by_role=BulbAccess.ROLE_CONTROLLER,
            created_by_role_priority=BulbAccess.role_priority(BulbAccess.ROLE_CONTROLLER),
            target_is_on=False,
            repeat=False,
            scheduled_for=timezone.now() - timedelta(minutes=1),
            next_run_at=timezone.now() - timedelta(minutes=1),
            enabled=True,
        )

        apply_schedule(schedule)
        self.bulb.refresh_from_db()
        self.assertTrue(self.bulb.is_on)

        latest = ControlActivity.objects.order_by("-created_at").first()
        self.assertEqual(latest.outcome, ControlActivity.OUTCOME_REJECTED)
        self.assertIn("manual override", latest.reason)



    def test_rejected_manual_command_records_reason_code_and_conflict_event(self):
        execute_control_request(
            bulb=self.bulb,
            action=ControlActivity.ACTION_ON,
            requested_is_on=True,
            acted_by=self.owner,
            source_type=ControlActivity.SOURCE_MANUAL,
        )

        decision = execute_control_request(
            bulb=self.bulb,
            action=ControlActivity.ACTION_OFF,
            requested_is_on=False,
            acted_by=self.controller,
            source_type=ControlActivity.SOURCE_MANUAL,
        )
        self.assertFalse(decision.accepted)
        self.assertIn(decision.activity.reason_code, {
            ControlActivity.REASON_ACTIVE_HIGHER_PRIORITY_CONTROL,
            ControlActivity.REASON_OVERRIDDEN_BY_OWNER_ADMIN,
        })
        self.assertTrue(ConflictEvent.objects.filter(losing_activity=decision.activity).exists())

class AccessManagementApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user(username="owner", password="pass12345")
        self.admin = User.objects.create_user(username="admin", password="pass12345")
        self.target = User.objects.create_user(username="target", password="pass12345")
        self.bulb = Bulb.objects.create(name="Hall Lamp", owner=self.owner)
        BulbAccess.objects.create(user=self.owner, bulb=self.bulb, role=BulbAccess.ROLE_OWNER)
        BulbAccess.objects.create(user=self.admin, bulb=self.bulb, role=BulbAccess.ROLE_ADMIN)

    def test_admin_cannot_grant_admin_but_owner_can(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("bulb_access_upsert"),
            data=json.dumps({"bulb_id": self.bulb.id, "username": self.target.username, "role": "admin"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("bulb_access_upsert"),
            data=json.dumps({"bulb_id": self.bulb.id, "username": self.target.username, "role": "admin"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(BulbAccess.objects.filter(bulb=self.bulb, user=self.target, role="admin").exists())



    def test_permission_denied_manual_api_is_logged_with_reason_code(self):
        viewer = User.objects.create_user(username="viewer2", password="pass12345")
        BulbAccess.objects.create(user=viewer, bulb=self.bulb, role=BulbAccess.ROLE_VIEWER)

        self.client.force_login(viewer)
        response = self.client.post(
            reverse("set_power") + f"?bulb={self.bulb.id}",
            data=json.dumps({"on": True}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        payload = response.json()
        self.assertEqual(payload["reason_code"], ControlActivity.REASON_PERMISSION_DENIED)
        self.assertTrue(ControlActivity.objects.filter(
            bulb=self.bulb,
            reason_code=ControlActivity.REASON_PERMISSION_DENIED,
        ).exists())
        self.assertTrue(ConflictEvent.objects.filter(
            bulb=self.bulb,
            conflict_type=ConflictEvent.TYPE_PERMISSION,
            reason_code=ControlActivity.REASON_PERMISSION_DENIED,
        ).exists())

class FormAndDashboardTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user(username="owner", password="pass12345")
        self.controller = User.objects.create_user(username="controller", password="pass12345")
        self.target = User.objects.create_user(username="target", password="pass12345")
        self.bulb = Bulb.objects.create(name="Test Lamp", owner=self.owner)
        BulbAccess.objects.create(user=self.owner, bulb=self.bulb, role=BulbAccess.ROLE_OWNER)
        BulbAccess.objects.create(user=self.controller, bulb=self.bulb, role=BulbAccess.ROLE_CONTROLLER)

    def test_schedule_form_accepts_one_time_schedule_without_weekdays(self):
        form = LightScheduleForm(
            data={
                "name": "One time",
                "schedule_kind": LightScheduleForm.SCHEDULE_KIND_ONE_TIME,
                "timezone_name": "America/Los_Angeles",
                "target_is_on": True,
                "target_brightness": 55,
                "scheduled_for": "2030-04-22T18:30",
                "time_of_day": "",
                "monday": False,
                "tuesday": False,
                "wednesday": False,
                "thursday": False,
                "friday": False,
                "saturday": False,
                "sunday": False,
                "enabled": True,
            },
            bulb=self.bulb,
            request_tzname="America/Los_Angeles",
        )
        self.assertTrue(form.is_valid(), form.errors)
        schedule = form.save(commit=False)
        self.assertFalse(schedule.repeat)

    def test_schedule_form_requires_brightness_only_for_on_actions(self):
        form = LightScheduleForm(
            data={
                "name": "Bad On",
                "schedule_kind": LightScheduleForm.SCHEDULE_KIND_ONE_TIME,
                "timezone_name": "America/Los_Angeles",
                "target_is_on": True,
                "target_brightness": "",
                "scheduled_for": "2030-04-23T18:30",
                "enabled": True,
            },
            bulb=self.bulb,
            request_tzname="America/Los_Angeles",
        )
        self.assertFalse(form.is_valid())
        self.assertIn("target_brightness", form.errors)

        form = LightScheduleForm(
            data={
                "name": "Bad Off",
                "schedule_kind": LightScheduleForm.SCHEDULE_KIND_ONE_TIME,
                "timezone_name": "America/Los_Angeles",
                "target_is_on": False,
                "target_brightness": 22,
                "scheduled_for": "2030-04-23T18:30",
                "enabled": True,
            },
            bulb=self.bulb,
            request_tzname="America/Los_Angeles",
        )
        self.assertFalse(form.is_valid())
        self.assertIn("target_brightness", form.errors)

    def test_schedule_form_rejects_conflicting_exact_times(self):
        LightSchedule.objects.create(
            bulb=self.bulb,
            created_by=self.owner,
            created_by_role=BulbAccess.ROLE_OWNER,
            created_by_role_priority=BulbAccess.role_priority(BulbAccess.ROLE_OWNER),
            name="Existing",
            target_is_on=True,
            target_brightness=70,
            repeat=False,
            scheduled_for=timezone.make_aware(datetime(2030, 4, 24, 18, 30)),
            timezone_name="UTC",
            enabled=True,
        )
        form = LightScheduleForm(
            data={
                "name": "Conflict",
                "schedule_kind": LightScheduleForm.SCHEDULE_KIND_ONE_TIME,
                "timezone_name": "UTC",
                "target_is_on": True,
                "target_brightness": 50,
                "scheduled_for": "2030-04-24T18:30",
                "enabled": True,
            },
            bulb=self.bulb,
            request_tzname="UTC",
        )
        self.assertFalse(form.is_valid())
        self.assertIn("Another enabled schedule", form.non_field_errors()[0])

    def test_owner_can_edit_schedule_from_schedules_page(self):
        sched = LightSchedule.objects.create(
            bulb=self.bulb,
            created_by=self.owner,
            created_by_role=BulbAccess.ROLE_OWNER,
            created_by_role_priority=BulbAccess.role_priority(BulbAccess.ROLE_OWNER),
            name="Before Edit",
            target_is_on=True,
            target_brightness=60,
            repeat=False,
            scheduled_for=timezone.now() + timedelta(days=1),
            timezone_name="UTC",
            enabled=True,
        )
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("bulb_schedules") + f"?bulb={self.bulb.id}",
            data={
                "schedule_id": sched.id,
                "name": "After Edit",
                "schedule_kind": LightScheduleForm.SCHEDULE_KIND_WEEKLY,
                "timezone_name": "America/Los_Angeles",
                "target_is_on": True,
                "target_brightness": 80,
                "scheduled_for": "",
                "time_of_day": "19:15",
                "monday": True,
                "tuesday": False,
                "wednesday": False,
                "thursday": False,
                "friday": False,
                "saturday": False,
                "sunday": False,
                "enabled": True,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        sched.refresh_from_db()
        self.assertEqual(sched.name, "After Edit")
        self.assertTrue(sched.repeat)
        self.assertEqual(sched.timezone_name, "America/Los_Angeles")
        self.assertEqual(sched.target_brightness, 80)

    def test_access_share_form_limits_choices(self):
        form = AccessShareForm(allowed_roles=[BulbAccess.ROLE_CONTROLLER, BulbAccess.ROLE_VIEWER])
        self.assertEqual(form.fields["role"].choices, [("controller", "Controller"), ("viewer", "Viewer")])
        self.assertEqual(form.fields["identifier"].label, "Username or Email")

    def test_dashboard_share_post_grants_access(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("bulb_dashboard") + f"?bulb={self.bulb.id}",
            data={
                "form_type": "share_access",
                "bulb_id": self.bulb.id,
                "identifier": self.target.username,
                "role": BulbAccess.ROLE_VIEWER,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(BulbAccess.objects.filter(bulb=self.bulb, user=self.target, role=BulbAccess.ROLE_VIEWER).exists())

    def test_power_range_summary_reports_energy(self):
        now = timezone.now()
        PowerReading.objects.create(bulb=self.bulb, created_at=now - timedelta(hours=2), current_rms=0.1, estimated_voltage=120, estimated_power_w=12, cumulative_energy_wh=1)
        PowerReading.objects.create(bulb=self.bulb, created_at=now - timedelta(hours=1), current_rms=0.1, estimated_voltage=120, estimated_power_w=12, cumulative_energy_wh=13)
        summary = get_bulb_power_range_summary(self.bulb, now - timedelta(hours=2), now - timedelta(hours=1))
        self.assertGreater(summary["energy_wh"], 0)
        self.assertEqual(summary["reading_count"], 2)


class DeviceSyncControlPathTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.bulb = Bulb.objects.create(name="Device Lamp")

    def test_request_control_action_accepts_device_sync_and_logs_it(self):
        decision = request_control_action(
            bulb=self.bulb,
            action=ControlActivity.ACTION_DEVICE_REPORTED,
            requested_is_on=True,
            requested_brightness=61,
            source_type=ControlActivity.SOURCE_DEVICE_SYNC,
            notes="Device sync test",
            current_rms=0.12,
            estimated_voltage=120,
            estimated_power_w=14.4,
            cumulative_energy_wh=2.5,
        )

        self.assertTrue(decision.accepted)
        self.bulb.refresh_from_db()
        self.assertTrue(self.bulb.is_on)
        self.assertEqual(self.bulb.brightness, 61)
        self.assertTrue(self.bulb.is_online)
        self.assertEqual(ControlActivity.objects.filter(bulb=self.bulb, source_type=ControlActivity.SOURCE_DEVICE_SYNC).count(), 1)
        self.assertEqual(PowerReading.objects.filter(bulb=self.bulb).count(), 1)

    def test_device_report_api_uses_unified_control_path(self):
        response = self.client.post(
            reverse("device_report"),
            data=json.dumps({
                "is_on": True,
                "brightness": 72,
                "current_rms": 0.11,
                "estimated_voltage": 120,
                "estimated_power_w": 13.2,
                "cumulative_energy_wh": 3.1,
            }),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.bulb.device_token}",
        )

        self.assertEqual(response.status_code, 200)
        self.bulb.refresh_from_db()
        self.assertTrue(self.bulb.is_on)
        self.assertEqual(self.bulb.brightness, 72)

        activity = ControlActivity.objects.filter(bulb=self.bulb).latest("created_at")
        self.assertEqual(activity.source_type, ControlActivity.SOURCE_DEVICE_SYNC)
        self.assertEqual(activity.outcome, ControlActivity.OUTCOME_REPORTED)
        self.assertEqual(PowerReading.objects.filter(bulb=self.bulb).count(), 1)


class ManageAccessPageTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user(username="owner2", password="pass", email="owner2@example.com")
        self.admin = User.objects.create_user(username="admin2", password="pass", email="admin2@example.com")
        self.controller = User.objects.create_user(username="controller2", password="pass", email="controller2@example.com")
        self.viewer = User.objects.create_user(username="viewer2", password="pass", email="viewer2@example.com")
        self.target = User.objects.create_user(username="target2", password="pass", email="target2@example.com")
        self.bulb = Bulb.objects.create(name="Hall Lamp", owner=self.owner)
        BulbAccess.objects.create(user=self.admin, bulb=self.bulb, role=BulbAccess.ROLE_ADMIN)
        BulbAccess.objects.create(user=self.controller, bulb=self.bulb, role=BulbAccess.ROLE_CONTROLLER)
        BulbAccess.objects.create(user=self.viewer, bulb=self.bulb, role=BulbAccess.ROLE_VIEWER)

    def test_manage_access_page_owner_can_load(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("manage_bulb_access", args=[self.bulb.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Manage Access")

    def test_manage_access_page_blocks_controller(self):
        self.client.force_login(self.controller)
        response = self.client.get(reverse("manage_bulb_access", args=[self.bulb.id]))
        self.assertEqual(response.status_code, 403)

    def test_owner_can_grant_access_by_email(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("manage_bulb_access", args=[self.bulb.id]),
            data={"form_type": "share_access", "identifier": self.target.email, "role": BulbAccess.ROLE_VIEWER},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(BulbAccess.objects.filter(bulb=self.bulb, user=self.target, role=BulbAccess.ROLE_VIEWER).exists())

    def test_owner_can_edit_existing_role(self):
        BulbAccess.objects.create(user=self.target, bulb=self.bulb, role=BulbAccess.ROLE_VIEWER)
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("manage_bulb_access", args=[self.bulb.id]),
            data={"form_type": "share_access", "identifier": self.target.username, "role": BulbAccess.ROLE_CONTROLLER},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(BulbAccess.objects.filter(bulb=self.bulb, user=self.target, role=BulbAccess.ROLE_CONTROLLER).exists())

    def test_owner_can_revoke_access(self):
        BulbAccess.objects.create(user=self.target, bulb=self.bulb, role=BulbAccess.ROLE_VIEWER)
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("manage_bulb_access", args=[self.bulb.id]),
            data={"form_type": "revoke_access", "identifier": self.target.username},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(BulbAccess.objects.filter(bulb=self.bulb, user=self.target).exists())

    def test_admin_cannot_assign_admin_role(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("manage_bulb_access", args=[self.bulb.id]),
            data={"form_type": "share_access", "identifier": self.target.username, "role": BulbAccess.ROLE_ADMIN},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(BulbAccess.objects.filter(bulb=self.bulb, user=self.target, role=BulbAccess.ROLE_ADMIN).exists())


    def test_admin_cannot_downgrade_existing_admin(self):
        second_admin = User.objects.create_user(username="secondadmin", password="pass", email="secondadmin@example.com")
        BulbAccess.objects.create(user=second_admin, bulb=self.bulb, role=BulbAccess.ROLE_ADMIN)
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("manage_bulb_access", args=[self.bulb.id]),
            data={"form_type": "share_access", "identifier": second_admin.username, "role": BulbAccess.ROLE_VIEWER},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(BulbAccess.objects.filter(bulb=self.bulb, user=second_admin, role=BulbAccess.ROLE_ADMIN).exists())

    def test_access_api_accepts_email_identifier(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("bulb_access_upsert"),
            data=json.dumps({"bulb_id": self.bulb.id, "identifier": self.target.email, "role": BulbAccess.ROLE_VIEWER}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(BulbAccess.objects.filter(bulb=self.bulb, user=self.target, role=BulbAccess.ROLE_VIEWER).exists())


class ReportingPagesTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user(username="ownerreport", password="pass12345", email="ownerreport@example.com")
        self.controller = User.objects.create_user(username="controllerreport", password="pass12345", email="controllerreport@example.com")
        self.viewer = User.objects.create_user(username="viewerreport", password="pass12345", email="viewerreport@example.com")

        self.bulb1 = Bulb.objects.create(name="Desk Lamp", owner=self.owner)
        self.bulb2 = Bulb.objects.create(name="Porch Light", owner=self.owner)
        BulbAccess.objects.create(user=self.owner, bulb=self.bulb1, role=BulbAccess.ROLE_OWNER)
        BulbAccess.objects.create(user=self.owner, bulb=self.bulb2, role=BulbAccess.ROLE_OWNER)
        BulbAccess.objects.create(user=self.controller, bulb=self.bulb1, role=BulbAccess.ROLE_CONTROLLER)
        BulbAccess.objects.create(user=self.viewer, bulb=self.bulb1, role=BulbAccess.ROLE_VIEWER)

        now = timezone.now()
        PowerReading.objects.create(bulb=self.bulb1, created_at=now - timedelta(hours=2), current_rms=0.05, estimated_voltage=120, estimated_power_w=6.0, cumulative_energy_wh=3.0)
        PowerReading.objects.create(bulb=self.bulb1, created_at=now - timedelta(hours=1), current_rms=0.06, estimated_voltage=120, estimated_power_w=7.2, cumulative_energy_wh=5.0)
        PowerReading.objects.create(bulb=self.bulb2, created_at=now - timedelta(hours=1), current_rms=0.10, estimated_voltage=120, estimated_power_w=12.0, cumulative_energy_wh=8.0)

        self.accepted = request_control_action(
            bulb=self.bulb1,
            action=ControlActivity.ACTION_ON,
            requested_is_on=True,
            requested_by_user=self.owner,
            source_type=ControlActivity.SOURCE_MANUAL,
            notes="Owner turned bulb on.",
        ).activity
        self.rejected = request_control_action(
            bulb=self.bulb1,
            action=ControlActivity.ACTION_OFF,
            requested_is_on=False,
            requested_by_user=self.controller,
            source_type=ControlActivity.SOURCE_MANUAL,
            notes="Controller tried to override owner.",
        ).activity

    def test_power_report_page_loads_for_viewer_with_accessible_bulb(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("power_report"), {"bulb": self.bulb1.id})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Power Usage Reports")
        self.assertContains(response, self.bulb1.name)
        self.assertNotContains(response, self.bulb2.name)

    def test_power_report_csv_export_uses_filters(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("power_report"), {"bulb": self.bulb1.id, "export": "csv"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        body = response.content.decode("utf-8")
        self.assertIn("Desk Lamp", body)
        self.assertNotIn("Porch Light", body)

    def test_activity_report_page_can_filter_conflicts(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("activity_report"), {"bulb": self.bulb1.id, "status": "conflict"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Conflict-related records")
        self.assertContains(response, self.rejected.reason_code)

    def test_activity_report_csv_export_includes_reason_code(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("activity_report"), {"bulb": self.bulb1.id, "export": "csv"})
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Reason Code", body)
        self.assertIn(self.rejected.reason_code, body)


class PhaseSevenInterfacePolishTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user(username="ownerui", password="pass12345", email="ownerui@example.com")
        self.controller = User.objects.create_user(username="controllerui", password="pass12345", email="controllerui@example.com")
        self.bulb = Bulb.objects.create(name="Office Lamp", owner=self.owner, is_online=True)
        BulbAccess.objects.create(user=self.owner, bulb=self.bulb, role=BulbAccess.ROLE_OWNER)
        BulbAccess.objects.create(user=self.controller, bulb=self.bulb, role=BulbAccess.ROLE_CONTROLLER)

    def test_selected_bulb_context_is_visible_on_schedules_page(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("bulb_schedules"), {"bulb": self.bulb.id})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Selected bulb: {self.bulb.name}")
        self.assertContains(response, reverse("manage_bulb_access", args=[self.bulb.id]))

    def test_selected_bulb_context_is_visible_on_power_report_page(self):
        self.client.force_login(self.controller)
        response = self.client.get(reverse("power_report"), {"bulb": self.bulb.id})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Selected bulb: {self.bulb.name}")
        self.assertContains(response, "Role: Controller")

    def test_my_bulbs_page_marks_selected_bulb(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("my_bulbs"), {"bulb": self.bulb.id})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Currently selected")
        self.assertContains(response, reverse("bulb_dashboard") + f"?bulb={self.bulb.id}")


class PhaseEightHomePageTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user(username="phase8owner", password="pass12345", email="phase8owner@example.com")
        self.bulb = Bulb.objects.create(name="Phase 8 Lamp", owner=self.owner, is_online=True)
        BulbAccess.objects.create(user=self.owner, bulb=self.bulb, role=BulbAccess.ROLE_OWNER)

    def test_home_page_uses_product_focused_copy_and_repository_link(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "GitHub Repository")
        self.assertContains(response, "https://github.com/fuse999/smartlight-iot-controller")
        self.assertContains(response, "Smart light control and monitoring in one place")
        self.assertNotContains(response, "Project Documents")
        self.assertNotContains(response, "Document hub")
        self.assertNotContains(response, "Main file")
        self.assertNotContains(response, "presentation-ready web app")

    def test_project_document_routes_are_removed(self):
        home = self.client.get(reverse("home"))
        self.assertNotContains(home, "/project/")
