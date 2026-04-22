from django.urls import path
from . import views
from . import api_views

urlpatterns = [
    path("", views.home_view, name="home"),
    path("dashboard/", views.dashboard_page, name="bulb_dashboard"),
    path("bulbs/", views.my_bulbs_page, name="my_bulbs"),
    path("bulbs/claim/", views.claim_bulb_page, name="claim_bulb"),
    path("bulbs/claim/<int:bulb_id>/", views.claim_bulb_action, name="claim_bulb_action"),
    path("bulbs/unclaim/<int:bulb_id>/", views.unclaim_bulb_action, name="unclaim_bulb_action"),
    path("schedules/", views.schedules_page, name="bulb_schedules"),

    path("api/set-power/", views.set_power_api, name="set_power"),
    path("api/set-brightness/", views.set_brightness_api, name="set_brightness"),
    path("api/light-state/", views.light_state_api, name="light_state"),
    path("api/schedule-status/", views.schedule_status_api, name="schedule_status"),
    path("api/bulb-status/", views.bulb_status_api, name="bulb_status"),
    path("api/set-timezone/", views.set_timezone_api, name="set_timezone"),
    path("schedules/toggle/<int:schedule_id>/", views.toggle_schedule, name="toggle_schedule"),
    path("schedules/delete/<int:schedule_id>/", views.delete_schedule, name="delete_schedule"),

    path("accounts/register/", views.register_view, name="register"),

    path("api/device/register/", api_views.register_device, name="device_register"),
    path("api/device/desired/", api_views.desired_state, name="device_desired"),
    path("api/device/report/", api_views.report_state, name="device_report"),
]