from django.urls import path
from . import views
from . import api_views
from django.views.generic import TemplateView
from django.urls import path, include

urlpatterns = [
    path("dashboard/", views.dashboard_page, name="bulb_dashboard"),
    path("control/", views.control_page, name="bulb-control"),
    path("api/set-power/", views.set_power_api, name="bulb-set-power"),
    path("api/set-brightness/", views.set_brightness_api, name="bulb-set-brightness"),
    path("schedules/", views.schedules_page, name="bulb_schedules"),
    path("schedules/<int:schedule_id>/toggle/", views.toggle_schedule, name="toggle_schedule"),
    path("schedules/<int:schedule_id>/delete/", views.delete_schedule, name="delete_schedule"),
    path("api/light/desired/", api_views.desired_state, name="api_desired_state"),
    path("api/light/report/", api_views.report_state, name="api_report_state"),
    path("home/", TemplateView.as_view(template_name="home.html"), name="home"),
    path("api/set-timezone/", views.set_timezone_api, name="set_timezone_api"),
    path("accounts/register/", views.register_view, name="register"),
    path("api/light-state/", views.light_state_api, name="light_state_api"),
    path("accounts/", include("django.contrib.auth.urls")),
    path("register/", views.register_view, name="register"),
]
