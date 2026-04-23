from datetime import datetime, timedelta
from pathlib import Path
import csv
import json

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, JsonResponse, HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Avg, Count, Max, Q
from django.db.models.functions import TruncDay, TruncHour
from django.utils import timezone
from django.conf import settings
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from .forms import AccessShareForm, LightScheduleForm, RegisterForm
from .models import Bulb, BulbAccess, ConflictEvent, ControlActivity, LightSchedule, PowerReading
from .services import (
    DEVICE_OFFLINE_AFTER_SECONDS,
    execute_control_request,
    get_account_power_summary,
    get_bulb_power_range_summary,
    get_bulb_power_summary,
    _format_energy_display,
    _format_power_display,
    get_user_role_for_bulb,
    grant_bulb_access,
    find_user_by_identifier,
    log_permission_denied_attempt,
    refresh_bulb_online_statuses,
    refresh_next_run,
    revoke_bulb_access,
    user_can_control_bulb,
    user_can_create_schedule,
    user_can_manage_access,
    user_can_manage_bulb,
    user_can_manage_schedule,
    user_can_view_bulb,
)

User = get_user_model()


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCS_ROOT = PROJECT_ROOT / "docs"
DOCUMENT_LIBRARY = [
    {
        "slug": "readme",
        "title": "Project Guide",
        "kind": "documentation",
        "path": PROJECT_ROOT / "README.md",
        "description": "Overview, architecture, setup steps, and the main project features.",
    },
    {
        "slug": "evaluation-django-postgresql",
        "title": "Django and PostgreSQL Evaluation",
        "kind": "document",
        "path": DOCS_ROOT / "Evaluation of using Django and PostgreSQL.docx",
        "description": "Research notes behind the backend technology choice for the project.",
    },
    {
        "slug": "project-task-checklist",
        "title": "Project Task Checklist",
        "kind": "document",
        "path": DOCS_ROOT / "Project Task checklist - IoT Bulb.docx",
        "description": "Working task checklist used to track design and implementation progress.",
    },
    {
        "slug": "hardware-wiring-diagrams",
        "title": "Hardware Wiring and Connections",
        "kind": "document",
        "path": DOCS_ROOT / "Collection of hardware connections and wiring diagrams_schematics.docx",
        "description": "Collected hardware diagrams, wiring notes, and connection planning for the build.",
    },
    {
        "slug": "data-logging-parameters",
        "title": "Data Logging Parameters",
        "kind": "document",
        "path": DOCS_ROOT / "Data Logging Paramaters.pdf",
        "description": "Reference document for power and telemetry logging assumptions.",
    },
]




def _project_resource_links():
    return {
        "repository_url": getattr(settings, "PROJECT_REPOSITORY_URL", "") or "",
        "documentation_url": getattr(settings, "PROJECT_DOCUMENTATION_URL", "") or "",
        "video_demo_url": getattr(settings, "PROJECT_VIDEO_DEMO_URL", "") or "",
    }


def _available_documents():
    docs = []
    for item in DOCUMENT_LIBRARY:
        docs.append({**item, "exists": item["path"].exists(), "filename": item["path"].name})
    return docs



def _resource_object_from_slug(slug: str):
    for item in DOCUMENT_LIBRARY:
        if item["slug"] == slug:
            return item
    return None


# ---------------------------------------------------------
# Helper: Return bulbs this user is allowed to access
# ---------------------------------------------------------
def _accessible_bulbs_for_user(user):
    refresh_bulb_online_statuses()

    if not user.is_authenticated:
        return Bulb.objects.none()

    if user.is_superuser:
        return Bulb.objects.all().order_by("name", "id")

    owned = Bulb.objects.filter(owner=user)
    shared = Bulb.objects.filter(
        user_access__user=user,
        user_access__role__in=[
            BulbAccess.ROLE_OWNER,
            BulbAccess.ROLE_ADMIN,
            BulbAccess.ROLE_CONTROLLER,
            BulbAccess.ROLE_VIEWER,
        ],
    )

    return (owned | shared).distinct().select_related("owner").order_by("name", "id")


# ---------------------------------------------------------
# Helper: Pick selected bulb from query string or default
# ---------------------------------------------------------
def _selected_bulb_for_request(request):
    bulbs = _accessible_bulbs_for_user(request.user)
    if not request.user.is_authenticated or not bulbs.exists():
        return None

    bulb_id = request.GET.get("bulb") or request.POST.get("bulb_id")
    if bulb_id:
        try:
            return bulbs.get(id=bulb_id)
        except Bulb.DoesNotExist:
            pass

    return bulbs.first()



def _selected_bulb_role_for_request(request, bulb: Bulb | None):
    if bulb is None:
        return None
    return get_user_role_for_bulb(request.user, bulb)


# ---------------------------------------------------------
# Helper: Return schedules for one bulb
# ---------------------------------------------------------
def _schedule_queryset_for_bulb(bulb):
    if bulb is None:
        return LightSchedule.objects.none()
    return bulb.schedules.select_related("created_by").order_by("next_run_at", "id")


# ---------------------------------------------------------
# Helpers for filters / forms
# ---------------------------------------------------------
def _build_schedule_form(request, bulb: Bulb | None, data=None, instance=None):
    request_tzname = request.session.get("django_timezone") or timezone.get_current_timezone_name()
    return LightScheduleForm(
        data=data,
        instance=instance,
        bulb=bulb,
        request_tzname=request_tzname,
    )



def _prepare_schedule_for_save(schedule: LightSchedule, *, bulb: Bulb, user, is_new: bool) -> LightSchedule:
    schedule.bulb = bulb
    schedule.created_by = user if user.is_authenticated else None
    schedule.claimed_at = None
    if is_new:
        schedule.last_run_at = None
    if not schedule.timezone_name:
        schedule.timezone_name = timezone.get_current_timezone_name()

    role = get_user_role_for_bulb(user, bulb) or ""
    schedule.created_by_role = role
    schedule.created_by_role_priority = BulbAccess.role_priority(role)
    return schedule



def _allowed_share_roles(user, bulb: Bulb) -> list[str]:
    actor_role = get_user_role_for_bulb(user, bulb)
    if actor_role == BulbAccess.ROLE_OWNER:
        return [BulbAccess.ROLE_ADMIN, BulbAccess.ROLE_CONTROLLER, BulbAccess.ROLE_VIEWER]
    if actor_role == BulbAccess.ROLE_ADMIN:
        return [BulbAccess.ROLE_CONTROLLER, BulbAccess.ROLE_VIEWER]
    return []



def _build_access_form(user, bulb: Bulb | None, data=None):
    return AccessShareForm(data=data, allowed_roles=_allowed_share_roles(user, bulb) if bulb else [])


def _resolve_target_user(identifier: str):
    return find_user_by_identifier(identifier)


def _access_entries_for_bulb(bulb: Bulb):
    return bulb.user_access.select_related("user").order_by("user__username")



def _parse_optional_local_datetime(value: str | None):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None

    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed



def _filter_activities_for_bulb(request, bulb: Bulb):
    start_raw = request.GET.get("activity_start", "")
    end_raw = request.GET.get("activity_end", "")
    outcome = request.GET.get("activity_outcome", "")

    start_dt = _parse_optional_local_datetime(start_raw)
    end_dt = _parse_optional_local_datetime(end_raw)

    qs = bulb.activities.select_related("user", "source_schedule", "overridden_activity").order_by("-created_at")
    if start_dt:
        qs = qs.filter(created_at__gte=start_dt)
    if end_dt:
        qs = qs.filter(created_at__lte=end_dt)
    if outcome:
        qs = qs.filter(outcome=outcome)

    return {
        "rows": qs[:50],
        "start_raw": start_raw,
        "end_raw": end_raw,
        "outcome": outcome,
    }



def _filter_power_readings_for_bulb(request, bulb: Bulb):
    start_raw = request.GET.get("power_start", "")
    end_raw = request.GET.get("power_end", "")
    start_dt = _parse_optional_local_datetime(start_raw)
    end_dt = _parse_optional_local_datetime(end_raw)

    qs = bulb.power_readings.order_by("-created_at")
    if start_dt:
        qs = qs.filter(created_at__gte=start_dt)
    if end_dt:
        qs = qs.filter(created_at__lte=end_dt)

    first_reading = bulb.power_readings.order_by("created_at").first()
    effective_start = start_dt or (first_reading.created_at if first_reading else timezone.now())
    effective_end = end_dt or timezone.now()
    summary = get_bulb_power_range_summary(bulb, effective_start, effective_end)

    return {
        "rows": qs[:100],
        "start_raw": start_raw,
        "end_raw": end_raw,
        "summary": summary,
    }


# ---------------------------------------------------------
# Page: Landing page
# ---------------------------------------------------------
@ensure_csrf_cookie
def home_view(request):
    bulbs = list(_accessible_bulbs_for_user(request.user)) if request.user.is_authenticated else []
    selected_bulb = _selected_bulb_for_request(request) if request.user.is_authenticated else None
    context = {
        "selected_bulb": selected_bulb,
        "selected_bulb_role": _selected_bulb_role_for_request(request, selected_bulb),
        "bulb_count": len(bulbs),
        "online_bulb_count": sum(1 for bulb in bulbs if bulb.is_online),
        "active_schedule_count": LightSchedule.objects.filter(bulb__in=bulbs, enabled=True).count() if bulbs else 0,
        "project_links": _project_resource_links(),
        "can_view_dashboard": selected_bulb is not None and user_can_view_bulb(request.user, selected_bulb),
    }
    return render(request, "home.html", context)



def project_file_download(request, slug):
    item = _resource_object_from_slug(slug)
    if not item or not item["path"].exists():
        raise Http404("Requested project file was not found.")
    return FileResponse(open(item["path"], "rb"), as_attachment=False, filename=item["path"].name)


# ---------------------------------------------------------
# Page: Main bulb dashboard
# ---------------------------------------------------------
@ensure_csrf_cookie
def dashboard_page(request):
    bulbs = list(_accessible_bulbs_for_user(request.user))
    bulb = _selected_bulb_for_request(request)

    can_view = bulb is not None and user_can_view_bulb(request.user, bulb)
    can_control = bulb is not None and user_can_control_bulb(request.user, bulb)
    can_manage = bulb is not None and user_can_manage_bulb(request.user, bulb)
    can_create_schedule = bulb is not None and user_can_create_schedule(request.user, bulb)
    can_manage_sharing = bulb is not None and user_can_manage_access(request.user, bulb)

    schedule_form = _build_schedule_form(request, bulb)
    access_form = _build_access_form(request.user, bulb)

    if request.method == "POST" and bulb is not None and request.user.is_authenticated:
        form_type = request.POST.get("form_type", "schedule")

        if form_type == "schedule":
            if not can_create_schedule:
                return HttpResponseForbidden("You do not have permission to create schedules for this bulb.")

            schedule_form = _build_schedule_form(request, bulb, data=request.POST)
            access_form = _build_access_form(request.user, bulb)
            if schedule_form.is_valid():
                schedule = schedule_form.save(commit=False)
                schedule = _prepare_schedule_for_save(schedule, bulb=bulb, user=request.user, is_new=True)
                schedule.save()
                refresh_next_run(schedule)
                messages.success(request, "Schedule created.")
                return redirect(f"{request.path}?bulb={bulb.id}")

        elif form_type == "share_access":
            if not can_manage_sharing:
                return HttpResponseForbidden("You do not have permission to manage sharing for this bulb.")

            access_form = _build_access_form(request.user, bulb, data=request.POST)
            if access_form.is_valid():
                identifier = access_form.cleaned_data["identifier"]
                role = access_form.cleaned_data["role"]
                try:
                    target_user = _resolve_target_user(identifier)
                    if target_user is None:
                        access_form.add_error("identifier", "User not found.")
                    else:
                        grant_bulb_access(bulb=bulb, target_user=target_user, role=role, granted_by=request.user)
                        messages.success(request, f"Access updated for {target_user.username}.")
                        return redirect(f"{request.path}?bulb={bulb.id}")
                except User.DoesNotExist:
                    access_form.add_error("identifier", "User not found.")
                except PermissionDenied as exc:
                    access_form.add_error(None, str(exc))

        elif form_type == "revoke_access":
            if not can_manage_sharing:
                return HttpResponseForbidden("You do not have permission to manage sharing for this bulb.")

            identifier = (request.POST.get("identifier") or request.POST.get("username") or "").strip()
            if identifier:
                try:
                    target_user = _resolve_target_user(identifier)
                    if target_user is None:
                        messages.error(request, "User not found.")
                    else:
                        revoke_bulb_access(bulb=bulb, target_user=target_user, revoked_by=request.user)
                        messages.success(request, f"Access revoked for {target_user.username}.")
                    return redirect(f"{request.path}?bulb={bulb.id}")
                except User.DoesNotExist:
                    messages.error(request, "User not found.")
                except PermissionDenied as exc:
                    messages.error(request, str(exc))

    selected_bulb_power_summary = get_bulb_power_summary(bulb) if bulb is not None and can_view else None
    schedules = _schedule_queryset_for_bulb(bulb) if can_view else LightSchedule.objects.none()
    access_entries = _access_entries_for_bulb(bulb) if bulb and can_manage_sharing else []
    activity_report = _filter_activities_for_bulb(request, bulb) if bulb is not None and can_view else None
    power_report = _filter_power_readings_for_bulb(request, bulb) if bulb is not None and can_view else None

    return render(
        request,
        "bulb/dashboard.html",
        {
            "state": bulb,
            "selected_bulb": bulb,
            "selected_bulb_role": _selected_bulb_role_for_request(request, bulb),
            "selected_bulb_power_summary": selected_bulb_power_summary,
            "bulbs": bulbs,
            "form": schedule_form,
            "access_form": access_form,
            "access_entries": access_entries,
            "schedules": schedules,
            "register_form": RegisterForm(),
            "activity_report": activity_report,
            "power_report": power_report,
            "can_control": can_control,
            "can_manage": can_manage,
            "can_manage_sharing": can_manage_sharing,
            "can_create_schedule": can_create_schedule,
            "can_view": can_view,
        },
    )


# ---------------------------------------------------------
# Page: Manage access for a single bulb
# ---------------------------------------------------------
@login_required
@ensure_csrf_cookie
def manage_access_page(request, bulb_id):
    bulb = get_object_or_404(Bulb, id=bulb_id)
    if not user_can_manage_access(request.user, bulb):
        return HttpResponseForbidden("You do not have permission to manage sharing for this bulb.")

    access_form = _build_access_form(request.user, bulb)

    if request.method == "POST":
        form_type = request.POST.get("form_type", "share_access")

        if form_type == "share_access":
            access_form = _build_access_form(request.user, bulb, data=request.POST)
            if access_form.is_valid():
                identifier = access_form.cleaned_data["identifier"]
                role = access_form.cleaned_data["role"]
                target_user = _resolve_target_user(identifier)
                if target_user is None:
                    access_form.add_error("identifier", "User not found.")
                else:
                    try:
                        grant_bulb_access(bulb=bulb, target_user=target_user, role=role, granted_by=request.user)
                        messages.success(request, f"Access updated for {target_user.username}.")
                        return redirect("manage_bulb_access", bulb_id=bulb.id)
                    except PermissionDenied as exc:
                        access_form.add_error(None, str(exc))

        elif form_type == "revoke_access":
            identifier = (request.POST.get("identifier") or "").strip()
            target_user = _resolve_target_user(identifier)
            if target_user is None:
                messages.error(request, "User not found.")
            else:
                try:
                    revoke_bulb_access(bulb=bulb, target_user=target_user, revoked_by=request.user)
                    messages.success(request, f"Access revoked for {target_user.username}.")
                    return redirect("manage_bulb_access", bulb_id=bulb.id)
                except PermissionDenied as exc:
                    messages.error(request, str(exc))

    access_entries = _access_entries_for_bulb(bulb)
    allowed_roles = _allowed_share_roles(request.user, bulb)
    return render(
        request,
        "bulb/manage_access.html",
        {
            "selected_bulb": bulb,
            "selected_bulb_role": _selected_bulb_role_for_request(request, bulb),
            "access_form": access_form,
            "access_entries": access_entries,
            "allowed_roles": allowed_roles,
            "owner_role": get_user_role_for_bulb(request.user, bulb),
        },
    )


# ---------------------------------------------------------
# Page: Show bulbs linked to the current user
# ---------------------------------------------------------
@ensure_csrf_cookie
def my_bulbs_page(request):
    bulbs = list(_accessible_bulbs_for_user(request.user))
    bulb = _selected_bulb_for_request(request)

    for bulb_item in bulbs:
        bulb_item.power_summary = get_bulb_power_summary(bulb_item)
        bulb_item.user_role = get_user_role_for_bulb(request.user, bulb_item)

    account_power_summary = get_account_power_summary(bulbs)

    return render(
        request,
        "bulb/my_bulbs.html",
        {
            "selected_bulb": bulb,
            "selected_bulb_role": _selected_bulb_role_for_request(request, bulb),
            "bulbs": bulbs,
            "account_power_summary": account_power_summary,
            "can_view": bulb is not None and user_can_view_bulb(request.user, bulb),
            "can_control": bulb is not None and user_can_control_bulb(request.user, bulb),
            "can_manage": bulb is not None and user_can_manage_bulb(request.user, bulb),
        },
    )


# ---------------------------------------------------------
# Page: Show claimable bulbs still in pairing mode
# ---------------------------------------------------------
@login_required
def claim_bulb_page(request):
    refresh_bulb_online_statuses()

    now = timezone.now()
    online_cutoff = now - timezone.timedelta(seconds=DEVICE_OFFLINE_AFTER_SECONDS)

    claimable_bulbs = Bulb.objects.filter(
        owner__isnull=True,
        pairing_mode_enabled=True,
        pairing_expires_at__gt=now,
        last_seen_at__gte=online_cutoff,
        is_active=True,
        is_online=True,
    ).order_by("-last_seen_at", "name")

    return render(request, "bulb/claim_bulb.html", {"claimable_bulbs": claimable_bulbs})


# ---------------------------------------------------------
# Action: Claim a bulb for the current user
# ---------------------------------------------------------
@login_required
@require_POST
def claim_bulb_action(request, bulb_id):
    bulb = get_object_or_404(
        Bulb,
        id=bulb_id,
        owner__isnull=True,
        pairing_mode_enabled=True,
        pairing_expires_at__gt=timezone.now(),
        is_active=True,
    )

    bulb.owner = request.user
    bulb.pairing_mode_enabled = False
    bulb.pairing_expires_at = None
    bulb.claimed_at = timezone.now()
    bulb.save(
        update_fields=[
            "owner",
            "pairing_mode_enabled",
            "pairing_expires_at",
            "claimed_at",
        ]
    )

    BulbAccess.objects.update_or_create(
        user=request.user,
        bulb=bulb,
        defaults={"role": BulbAccess.ROLE_OWNER},
    )

    messages.success(request, f'"{bulb.name}" is now linked to your account.')
    return redirect(f"/dashboard/?bulb={bulb.id}")


# ---------------------------------------------------------
# Action: Remove bulb from current user account
# ---------------------------------------------------------
@login_required
@require_POST
def unclaim_bulb_action(request, bulb_id):
    bulb = get_object_or_404(Bulb, id=bulb_id, is_active=True)

    if bulb.owner_id != request.user.id and not request.user.is_superuser:
        return HttpResponseForbidden("You do not have permission to unclaim this device.")

    bulb.owner = None
    bulb.claimed_at = None
    bulb.pairing_mode_enabled = True
    bulb.pairing_expires_at = timezone.now() + timezone.timedelta(minutes=2)
    bulb.save(
        update_fields=[
            "owner",
            "claimed_at",
            "pairing_mode_enabled",
            "pairing_expires_at",
        ]
    )

    BulbAccess.objects.filter(bulb=bulb).delete()

    messages.success(
        request,
        f'"{bulb.name}" has been removed from your account and is claimable again.',
    )
    return redirect("my_bulbs")


# ---------------------------------------------------------
# Page: Manage schedules for a selected bulb
# ---------------------------------------------------------
@ensure_csrf_cookie
def schedules_page(request):
    bulbs = list(_accessible_bulbs_for_user(request.user))
    bulb = _selected_bulb_for_request(request)

    can_view = bulb is not None and user_can_view_bulb(request.user, bulb)
    can_manage = bulb is not None and user_can_manage_bulb(request.user, bulb)
    can_create_schedule = bulb is not None and user_can_create_schedule(request.user, bulb)

    editing_schedule = None
    edit_schedule_id = request.GET.get("edit")
    if bulb is not None and edit_schedule_id:
        editing_schedule = get_object_or_404(LightSchedule, id=edit_schedule_id, bulb=bulb)
        if not user_can_manage_schedule(request.user, editing_schedule):
            return HttpResponseForbidden("You do not have permission to edit this schedule.")

    if request.method == "POST":
        if bulb is None:
            return redirect("bulb_schedules")

        schedule_id = request.POST.get("schedule_id")
        if schedule_id:
            editing_schedule = get_object_or_404(LightSchedule, id=schedule_id, bulb=bulb)
            if not user_can_manage_schedule(request.user, editing_schedule):
                return HttpResponseForbidden("You do not have permission to edit this schedule.")
        elif not can_create_schedule:
            return redirect("bulb_schedules")

        form = _build_schedule_form(request, bulb, data=request.POST, instance=editing_schedule)
        if form.is_valid():
            schedule = form.save(commit=False)
            schedule = _prepare_schedule_for_save(
                schedule,
                bulb=bulb,
                user=request.user,
                is_new=editing_schedule is None,
            )
            schedule.save()
            refresh_next_run(schedule)
            messages.success(request, "Schedule updated." if editing_schedule else "Schedule created.")
            return redirect(f"{request.path}?bulb={bulb.id}")
    else:
        form = _build_schedule_form(request, bulb, instance=editing_schedule)

    schedules = _schedule_queryset_for_bulb(bulb) if can_view else LightSchedule.objects.none()

    return render(
        request,
        "bulb/schedules.html",
        {
            "form": form,
            "schedules": schedules,
            "selected_bulb": bulb,
            "selected_bulb_role": _selected_bulb_role_for_request(request, bulb),
            "bulbs": bulbs,
            "editing_schedule": editing_schedule,
            "can_control": can_manage,
            "can_manage": can_manage,
            "can_create_schedule": can_create_schedule,
            "can_view": can_view,
        },
    )


# ---------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------
CONFLICT_REASON_CODES = [
    ControlActivity.REASON_ACTIVE_HIGHER_PRIORITY_CONTROL,
    ControlActivity.REASON_SUPERSEDED_BY_LATER_COMMAND,
    ControlActivity.REASON_OVERRIDDEN_BY_OWNER_ADMIN,
]


def _default_report_range(days: int):
    end_dt = timezone.now()
    start_dt = end_dt - timedelta(days=days)
    return start_dt, end_dt


def _parse_report_range(request, *, start_key='start', end_key='end', default_days=7):
    default_start, default_end = _default_report_range(default_days)
    start_raw = request.GET.get(start_key, '')
    end_raw = request.GET.get(end_key, '')
    start_dt = _parse_optional_local_datetime(start_raw) or default_start
    end_dt = _parse_optional_local_datetime(end_raw) or default_end

    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    return {
        'start_raw': start_raw or timezone.localtime(start_dt).strftime('%Y-%m-%dT%H:%M'),
        'end_raw': end_raw or timezone.localtime(end_dt).strftime('%Y-%m-%dT%H:%M'),
        'start_dt': start_dt,
        'end_dt': end_dt,
    }


def _resolve_report_bulb(request, bulbs, param='bulb'):
    bulb_id = (request.GET.get(param) or '').strip()
    if not bulb_id:
        return None
    try:
        return bulbs.get(id=int(bulb_id))
    except (ValueError, Bulb.DoesNotExist):
        return None


def _bucket_kind_for_range(start_dt, end_dt):
    delta = end_dt - start_dt
    return 'hour' if delta <= timedelta(days=3) else 'day'


def _build_power_chart(readings_qs, start_dt, end_dt):
    bucket_kind = _bucket_kind_for_range(start_dt, end_dt)
    trunc = TruncHour('created_at') if bucket_kind == 'hour' else TruncDay('created_at')
    rows = list(
        readings_qs.annotate(bucket=trunc)
        .values('bucket')
        .annotate(avg_power=Avg('estimated_power_w'), peak_power=Max('estimated_power_w'), readings=Count('id'))
        .order_by('bucket')
    )

    labels = []
    avg_values = []
    peak_values = []
    for row in rows:
        bucket = timezone.localtime(row['bucket']) if row['bucket'] else None
        if bucket_kind == 'hour':
            labels.append(bucket.strftime('%m-%d %H:%M') if bucket else '')
        else:
            labels.append(bucket.strftime('%Y-%m-%d') if bucket else '')
        avg_values.append(round(float(row['avg_power'] or 0.0), 2))
        peak_values.append(round(float(row['peak_power'] or 0.0), 2))

    return {
        'bucket_kind': bucket_kind,
        'labels_json': json.dumps(labels),
        'avg_values_json': json.dumps(avg_values),
        'peak_values_json': json.dumps(peak_values),
        'points_count': len(labels),
    }


def _build_power_report_context(request):
    bulbs_qs = _accessible_bulbs_for_user(request.user)
    bulbs = list(bulbs_qs)
    selected_bulb = _resolve_report_bulb(request, bulbs_qs)
    date_range = _parse_report_range(request, start_key='start', end_key='end', default_days=7)

    readings_qs = PowerReading.objects.filter(
        bulb__in=bulbs_qs,
        created_at__gte=date_range['start_dt'],
        created_at__lte=date_range['end_dt'],
    ).select_related('bulb').order_by('created_at')

    if selected_bulb is not None:
        readings_qs = readings_qs.filter(bulb=selected_bulb)
        summary = get_bulb_power_range_summary(selected_bulb, date_range['start_dt'], date_range['end_dt'])
        bulb_count = 1
    else:
        energy_wh = sum(
            get_bulb_power_range_summary(bulb, date_range['start_dt'], date_range['end_dt'])['energy_wh']
            for bulb in bulbs
        )
        aggregate = readings_qs.aggregate(
            avg_power=Avg('estimated_power_w'),
            peak_power=Max('estimated_power_w'),
            readings=Count('id'),
        )
        bulb_count = len(bulbs)
        summary = {
            'start_dt': date_range['start_dt'],
            'end_dt': date_range['end_dt'],
            'reading_count': int(aggregate['readings'] or 0),
            'energy_wh': energy_wh,
            'energy_display': _format_energy_display(energy_wh),
            'average_power_w': float(aggregate['avg_power'] or 0.0),
            'average_power_display': _format_power_display(float(aggregate['avg_power'] or 0.0)),
            'peak_power_w': float(aggregate['peak_power'] or 0.0),
            'peak_power_display': _format_power_display(float(aggregate['peak_power'] or 0.0)),
        }

    export_readings_qs = readings_qs.order_by('-created_at')
    readings = list(export_readings_qs[:250])
    chart = _build_power_chart(readings_qs, date_range['start_dt'], date_range['end_dt'])

    return {
        'bulbs': bulbs,
        'selected_bulb': selected_bulb,
        'selected_bulb_role': _selected_bulb_role_for_request(request, selected_bulb),
        'summary': summary,
        'readings': readings,
        'export_readings_qs': export_readings_qs,
        'chart': chart,
        'filters': date_range,
        'bulb_count': bulb_count,
    }


def _build_activity_report_context(request):
    bulbs_qs = _accessible_bulbs_for_user(request.user)
    bulbs = list(bulbs_qs)
    date_range = _parse_report_range(request, start_key='start', end_key='end', default_days=30)

    activities = ControlActivity.objects.filter(
        bulb__in=bulbs_qs,
        created_at__gte=date_range['start_dt'],
        created_at__lte=date_range['end_dt'],
    ).select_related('bulb', 'user', 'source_schedule', 'overridden_activity').order_by('-created_at')

    selected_bulb = _resolve_report_bulb(request, bulbs_qs)
    if selected_bulb is not None:
        activities = activities.filter(bulb=selected_bulb)

    user_options = list(
        User.objects.filter(bulb_activities__in=activities).distinct().order_by('username')
    )
    selected_user_id = (request.GET.get('user') or '').strip()
    if selected_user_id:
        try:
            activities = activities.filter(user_id=int(selected_user_id))
        except ValueError:
            selected_user_id = ''

    action_filter = (request.GET.get('action') or '').strip()
    if action_filter:
        activities = activities.filter(action=action_filter)

    status_filter = (request.GET.get('status') or '').strip()
    if status_filter == 'accepted':
        activities = activities.filter(outcome=ControlActivity.OUTCOME_ACCEPTED)
    elif status_filter == 'rejected':
        activities = activities.filter(outcome=ControlActivity.OUTCOME_REJECTED)
    elif status_filter == 'conflict':
        activities = activities.filter(
            Q(reason_code__in=CONFLICT_REASON_CODES)
            | Q(won_conflict_events__isnull=False)
            | Q(lost_conflict_events__isnull=False)
        ).distinct()
    elif status_filter == 'reported':
        activities = activities.filter(outcome=ControlActivity.OUTCOME_REPORTED)

    export_activities_qs = activities
    activity_rows = list(export_activities_qs[:250])

    summary_base = activities.aggregate(
        total=Count('id'),
        accepted=Count('id', filter=Q(outcome=ControlActivity.OUTCOME_ACCEPTED)),
        rejected=Count('id', filter=Q(outcome=ControlActivity.OUTCOME_REJECTED)),
        reported=Count('id', filter=Q(outcome=ControlActivity.OUTCOME_REPORTED)),
    )
    conflict_count = activities.filter(
        Q(reason_code__in=CONFLICT_REASON_CODES)
        | Q(won_conflict_events__isnull=False)
        | Q(lost_conflict_events__isnull=False)
    ).distinct().count()

    return {
        'bulbs': bulbs,
        'selected_bulb': selected_bulb,
        'selected_bulb_role': _selected_bulb_role_for_request(request, selected_bulb),
        'user_options': user_options,
        'rows': activity_rows,
        'export_activities_qs': export_activities_qs,
        'filters': {
            **date_range,
            'selected_user_id': selected_user_id,
            'action': action_filter,
            'status': status_filter,
        },
        'summary': {
            'total': int(summary_base['total'] or 0),
            'accepted': int(summary_base['accepted'] or 0),
            'rejected': int(summary_base['rejected'] or 0),
            'reported': int(summary_base['reported'] or 0),
            'conflict': conflict_count,
        },
        'action_choices': ControlActivity.ACTION_CHOICES,
    }


def _csv_response(filename: str):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# ---------------------------------------------------------
# Page: Power reporting
# ---------------------------------------------------------
@login_required
@require_GET
def power_report_page(request):
    context = _build_power_report_context(request)

    if request.GET.get('export') == 'csv':
        response = _csv_response('power_report.csv')
        writer = csv.writer(response)
        writer.writerow(['Bulb', 'Timestamp', 'Current RMS (A)', 'Estimated Voltage (V)', 'Estimated Power (W)', 'Cumulative Energy (Wh)'])
        for reading in context['export_readings_qs']:
            writer.writerow([
                reading.bulb.name if reading.bulb_id else '',
                timezone.localtime(reading.created_at).isoformat(),
                reading.current_rms,
                reading.estimated_voltage,
                reading.estimated_power_w,
                reading.cumulative_energy_wh,
            ])
        return response

    return render(request, 'bulb/reports_power.html', context)


# ---------------------------------------------------------
# Page: Activity reporting
# ---------------------------------------------------------
@login_required
@require_GET
def activity_report_page(request):
    context = _build_activity_report_context(request)

    if request.GET.get('export') == 'csv':
        response = _csv_response('activity_report.csv')
        writer = csv.writer(response)
        writer.writerow(['Timestamp', 'Bulb', 'User', 'Source', 'Action', 'Outcome', 'Reason Code', 'Reason', 'Resulting Is On', 'Resulting Brightness'])
        for row in context['export_activities_qs']:
            writer.writerow([
                timezone.localtime(row.created_at).isoformat(),
                row.bulb.name if row.bulb_id else '',
                row.user.username if row.user_id else '',
                row.source_type,
                row.action,
                row.outcome,
                row.reason_code,
                row.reason,
                row.resulting_is_on,
                row.resulting_brightness,
            ])
        return response

    return render(request, 'bulb/reports_activity.html', context)


# ---------------------------------------------------------
# API: Turn selected bulb on or off
# ---------------------------------------------------------
@require_POST
def set_power_api(request):
    bulb = _selected_bulb_for_request(request)
    if bulb is None:
        return HttpResponseForbidden("No bulb available.")

    if not user_can_control_bulb(request.user, bulb):
        activity = log_permission_denied_attempt(
            bulb=bulb,
            action=ControlActivity.ACTION_ON,
            source_type=ControlActivity.SOURCE_MANUAL,
            acted_by=request.user,
            notes="Manual power request from web UI.",
        )
        return JsonResponse({"ok": False, "reason": activity.reason, "reason_code": activity.reason_code}, status=403)

    try:
        payload = json.loads(request.body.decode("utf-8"))
        on = bool(payload["on"])
    except Exception:
        return HttpResponseBadRequest("Invalid JSON payload. Expected: {'on': true/false}")

    decision = execute_control_request(
        bulb=bulb,
        action=ControlActivity.ACTION_ON if on else ControlActivity.ACTION_OFF,
        requested_is_on=on,
        acted_by=request.user,
        source_type=ControlActivity.SOURCE_MANUAL,
        notes="Manual power request from web UI.",
    )

    status = 200 if decision.accepted else 409
    return JsonResponse(
        {
            "ok": decision.accepted,
            "bulb_id": decision.bulb.id,
            "is_on": decision.bulb.is_on,
            "brightness": decision.bulb.brightness,
            "outcome": decision.activity.outcome,
            "activity_id": decision.activity.id,
            "reason": decision.reason,
            "reason_code": decision.activity.reason_code,
            "overrode_existing": decision.activity.overrode_existing,
            "override_until": decision.override_until.isoformat() if decision.override_until else None,
            "updated_at": decision.bulb.updated_at.isoformat() if decision.bulb.updated_at else None,
        },
        status=status,
    )


# ---------------------------------------------------------
# API: Set selected bulb brightness
# ---------------------------------------------------------
@require_POST
def set_brightness_api(request):
    bulb = _selected_bulb_for_request(request)
    if bulb is None:
        return HttpResponseForbidden("No bulb available.")

    if not user_can_control_bulb(request.user, bulb):
        activity = log_permission_denied_attempt(
            bulb=bulb,
            action=ControlActivity.ACTION_BRIGHTNESS,
            source_type=ControlActivity.SOURCE_MANUAL,
            acted_by=request.user,
            notes="Manual brightness request from web UI.",
        )
        return JsonResponse({"ok": False, "reason": activity.reason, "reason_code": activity.reason_code}, status=403)

    try:
        payload = json.loads(request.body.decode("utf-8"))
        brightness = int(payload["brightness"])
    except Exception:
        return HttpResponseBadRequest("Invalid JSON payload. Expected: {'brightness': 0-100}")

    decision = execute_control_request(
        bulb=bulb,
        action=ControlActivity.ACTION_BRIGHTNESS,
        requested_is_on=brightness > 0,
        requested_brightness=brightness,
        acted_by=request.user,
        source_type=ControlActivity.SOURCE_MANUAL,
        notes="Manual brightness request from web UI.",
    )

    status = 200 if decision.accepted else 409
    return JsonResponse(
        {
            "ok": decision.accepted,
            "bulb_id": decision.bulb.id,
            "is_on": decision.bulb.is_on,
            "brightness": decision.bulb.brightness,
            "outcome": decision.activity.outcome,
            "activity_id": decision.activity.id,
            "reason": decision.reason,
            "reason_code": decision.activity.reason_code,
            "overrode_existing": decision.activity.overrode_existing,
            "override_until": decision.override_until.isoformat() if decision.override_until else None,
            "updated_at": decision.bulb.updated_at.isoformat() if decision.bulb.updated_at else None,
        },
        status=status,
    )


# ---------------------------------------------------------
# API: Return current state of selected bulb
# ---------------------------------------------------------
def light_state_api(request):
    bulb = _selected_bulb_for_request(request)
    if bulb is None:
        return JsonResponse({"is_on": False, "brightness": 100, "updated_at": None, "bulb_id": None})

    if not user_can_view_bulb(request.user, bulb):
        return HttpResponseForbidden("You do not have permission to view this bulb.")

    return JsonResponse(
        {
            "bulb_id": bulb.id,
            "is_on": bulb.is_on,
            "brightness": bulb.brightness,
            "updated_at": bulb.updated_at.isoformat() if bulb.updated_at else None,
        }
    )


# ---------------------------------------------------------
# API: Return schedule information for selected bulb
# ---------------------------------------------------------
def schedule_status_api(request):
    bulb = _selected_bulb_for_request(request)

    if bulb is None or not user_can_view_bulb(request.user, bulb):
        return JsonResponse({"schedules": []})

    schedules = _schedule_queryset_for_bulb(bulb)

    data = []
    for s in schedules:
        schedule_display = (
            f"{s.days_display()} @ {s.time_of_day.strftime('%I:%M %p').lstrip('0')}"
            if s.repeat and s.time_of_day
            else (timezone.localtime(s.scheduled_for).strftime("%Y-%m-%d %I:%M %p") if s.scheduled_for else "—")
        )
        data.append(
            {
                "id": s.id,
                "name": s.name or "—",
                "enabled": s.enabled,
                "type": "Repeating" if s.repeat else "One-time",
                "days_display": s.days_display() if s.repeat else "One time",
                "time_of_day": s.time_of_day.strftime("%I:%M %p").lstrip("0") if s.time_of_day else "—",
                "schedule_display": schedule_display,
                "timezone_name": s.timezone_name or "—",
                "target_is_on": s.target_is_on,
                "target_brightness": s.target_brightness,
                "created_by_role": s.created_by_role or "—",
                "next_run_at": s.next_run_at.isoformat() if s.next_run_at else None,
                "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None,
            }
        )

    return JsonResponse({"schedules": data})


@require_GET
def bulb_status_api(request):
    bulbs = _accessible_bulbs_for_user(request.user)

    data = []
    for bulb in bulbs:
        data.append(
            {
                "id": bulb.id,
                "name": bulb.name,
                "is_online": bulb.is_online,
                "last_seen_display": bulb.last_seen_at.strftime("%Y-%m-%d %I:%M %p") if bulb.last_seen_at else "Never",
            }
        )

    return JsonResponse({"bulbs": data})


# ---------------------------------------------------------
# API: View sharing settings for a bulb
# ---------------------------------------------------------
@login_required
@require_GET
def bulb_access_list_api(request):
    bulb = _selected_bulb_for_request(request)
    if bulb is None or not user_can_manage_access(request.user, bulb):
        return HttpResponseForbidden("You do not have permission to manage sharing for this bulb.")

    access_rows = []
    for access in bulb.user_access.select_related("user").order_by("user__username"):
        access_rows.append(
            {
                "username": access.user.username,
                "email": access.user.email,
                "role": access.role,
                "created_at": access.created_at.isoformat(),
            }
        )

    return JsonResponse(
        {
            "bulb_id": bulb.id,
            "owner": bulb.owner.username if bulb.owner_id else None,
            "access": access_rows,
        }
    )


# ---------------------------------------------------------
# API: Grant or update sharing access for a bulb
# ---------------------------------------------------------
@login_required
@require_POST
def bulb_access_upsert_api(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
        bulb_id = int(payload["bulb_id"])
        identifier = str(payload.get("identifier") or payload.get("username") or payload.get("email") or "").strip()
        role = str(payload["role"]).strip().lower()
    except Exception:
        return HttpResponseBadRequest("Invalid JSON payload. Expected bulb_id, identifier, and role.")

    bulb = get_object_or_404(Bulb, id=bulb_id)
    if not user_can_manage_access(request.user, bulb):
        return HttpResponseForbidden("You do not have permission to manage sharing for this bulb.")

    target_user = _resolve_target_user(identifier)
    if target_user is None:
        return JsonResponse({"ok": False, "reason": "User not found."}, status=404)

    try:
        access = grant_bulb_access(bulb=bulb, target_user=target_user, role=role, granted_by=request.user)
    except PermissionDenied as exc:
        return JsonResponse({"ok": False, "reason": str(exc)}, status=403)

    return JsonResponse({"ok": True, "bulb_id": bulb.id, "username": access.user.username, "email": access.user.email, "role": access.role})


# ---------------------------------------------------------
# API: Revoke sharing access for a bulb
# ---------------------------------------------------------
@login_required
@require_POST
def bulb_access_revoke_api(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
        bulb_id = int(payload["bulb_id"])
        identifier = str(payload.get("identifier") or payload.get("username") or payload.get("email") or "").strip()
    except Exception:
        return HttpResponseBadRequest("Invalid JSON payload. Expected bulb_id and identifier.")

    bulb = get_object_or_404(Bulb, id=bulb_id)
    target_user = _resolve_target_user(identifier)
    if target_user is None:
        return JsonResponse({"ok": False, "reason": "User not found."}, status=404)

    try:
        revoke_bulb_access(bulb=bulb, target_user=target_user, revoked_by=request.user)
    except PermissionDenied as exc:
        return JsonResponse({"ok": False, "reason": str(exc)}, status=403)

    return JsonResponse({"ok": True, "bulb_id": bulb.id, "username": target_user.username})


# ---------------------------------------------------------
# Action: Enable or disable a schedule
# ---------------------------------------------------------
@require_POST
def toggle_schedule(request, schedule_id):
    schedule = get_object_or_404(LightSchedule, id=schedule_id)
    bulb = schedule.bulb

    if bulb is None or not user_can_manage_schedule(request.user, schedule):
        return HttpResponseForbidden("You do not have permission to manage this schedule.")

    schedule.enabled = not schedule.enabled

    if schedule.enabled:
        schedule.claimed_at = None
        schedule.save(update_fields=["enabled", "claimed_at"])
        refresh_next_run(schedule)
    else:
        schedule.claimed_at = None
        schedule.save(update_fields=["enabled", "claimed_at"])

    next_url = request.POST.get("next")
    if next_url:
        return redirect(next_url)
    return redirect(f"/schedules/?bulb={bulb.id}")


# ---------------------------------------------------------
# Action: Delete a schedule
# ---------------------------------------------------------
@require_POST
def delete_schedule(request, schedule_id):
    schedule = get_object_or_404(LightSchedule, id=schedule_id)
    bulb = schedule.bulb

    if bulb is None or not user_can_manage_schedule(request.user, schedule):
        return HttpResponseForbidden("You do not have permission to manage this schedule.")

    schedule.delete()

    next_url = request.POST.get("next")
    if next_url:
        return redirect(next_url)
    return redirect(f"/schedules/?bulb={bulb.id}")


# ---------------------------------------------------------
# API: Save browser timezone into session
# ---------------------------------------------------------
@require_POST
def set_timezone_api(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
        tzname = payload["timezone"]
    except Exception:
        return HttpResponseBadRequest("Invalid JSON payload. Expected: {'timezone': 'Area/City'}")

    request.session["django_timezone"] = tzname

    return JsonResponse({"ok": True, "timezone": tzname})


# ---------------------------------------------------------
# Page: Register a new user account
# ---------------------------------------------------------
def register_view(request):
    if request.method == "POST":
        register_form = RegisterForm(request.POST)

        if register_form.is_valid():
            user = register_form.save()
            login(request, user)

            next_url = request.POST.get("next") or "/dashboard/"
            return redirect(next_url)
    else:
        register_form = RegisterForm()

    return render(request, "registration/register.html", {"register_form": register_form})
