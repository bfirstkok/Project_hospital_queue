from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views

urlpatterns = [
    path("admin/", admin.site.urls),

    # auth
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),

    # app urls
    path("dashboard/", include("dashboard.urls")),  # ✅ เพิ่มบรรทัดนี้

    path("", include("queues.urls")),
    path("patients/", include("patients.urls")),
]
