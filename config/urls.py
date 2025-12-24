from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
<<<<<<< HEAD
=======
from queues import views
>>>>>>> 936b9684b626c1ca84d26585058c55021c2e1a16

urlpatterns = [
    path("admin/", admin.site.urls),

<<<<<<< HEAD
    # auth
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),

    # app urls
    path("accounts/", include("accounts.urls")),
    path("dashboard/", include("dashboard.urls")),  # ✅ เพิ่มบรรทัดนี้

    path("", include("queues.urls")),
    path("patients/", include("patients.urls")),
=======
    # 1. หน้าแรกสุดคือ Login
    path("", auth_views.LoginView.as_view(template_name='registration/login.html'), name="login"),

    # 2. ย้าย queues ไปไว้ที่พาร์ท /queues/
    path("queues/", include("queues.urls")), 
    

    path("accounts/login/", auth_views.LoginView.as_view(), name="login_alt"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("dashboard/", include("dashboard.urls")),
    path("patients/", include("patients.urls")),
    path("opd/", include("opd.urls")),

    path("api/iot/telemetry/", views.iot_telemetry, name="iot_telemetry"),

>>>>>>> 936b9684b626c1ca84d26585058c55021c2e1a16
]
