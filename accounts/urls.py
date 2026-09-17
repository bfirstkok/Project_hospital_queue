from django.urls import path
from . import views
from .access import Capability, capability_required

urlpatterns = [
    path("dashboard/", capability_required(Capability.VIEW_DASHBOARD)(views.dashboard), name="dashboard"),
    path("landing/", views.role_landing, name="role_landing"),
    path("permissions/", views.my_permissions, name="my_permissions"),
    path("settings/", views.account_settings, name="account_settings"),
]
