from django.urls import path
from . import views

urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("landing/", views.role_landing, name="role_landing"),
    path("permissions/", views.my_permissions, name="my_permissions"),
]
