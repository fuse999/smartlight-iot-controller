from django.urls import path
from . import views

urlpatterns = [
    path("control/", views.control_page, name="bulb-control"),
    path("api/set-power/", views.set_power_api, name="bulb-set-power"),
]
