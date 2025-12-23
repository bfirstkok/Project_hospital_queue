from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views

urlpatterns = [
    path("admin/", admin.site.urls),

    # 1. หน้าแรกสุดคือ Login
    path("", auth_views.LoginView.as_view(template_name='registration/login.html'), name="login"),

    # 2. ย้าย queues ไปไว้ที่พาร์ท /queues/
    path("queues/", include("queues.urls")), 

    path("accounts/login/", auth_views.LoginView.as_view(), name="login_alt"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("dashboard/", include("dashboard.urls")),
    path("patients/", include("patients.urls")),
]
