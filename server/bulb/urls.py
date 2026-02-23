from django.urls import path
from . import views
from . import api_views
from django.views.generic import TemplateView

urlpatterns = [
    path("control/", views.control_page, name="bulb-control"),
    path("api/set-power/", views.set_power_api, name="bulb-set-power"),
    path("api/set-brightness/", views.set_brightness_api, name="bulb-set-brightness"),
    path("schedules/", views.schedules_page, name="bulb_schedules"),
    path("api/light/desired/", api_views.desired_state, name="api_desired_state"),
    path("api/light/report/", api_views.report_state, name="api_report_state"),
    path("home/", TemplateView.as_view(template_name="home.html"), name="home"),
]
