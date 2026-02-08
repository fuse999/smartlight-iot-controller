from django.urls import path
from . import views

urlpatterns = [
    path("control/", views.control_page, name="bulb-control"),
    path("api/set-power/", views.set_power_api, name="bulb-set-power"),
    path("api/set-brightness/", views.set_brightness_api, name="bulb-set-brightness"),
    path("schedules/", views.schedules_page, name="bulb_schedules"),
]
