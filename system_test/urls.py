from django.urls import path

from . import views

app_name = "system_test"

urlpatterns = [
    path("", views.index, name="index"),
    path("patient/create-random/", views.create_random_registered_patient, name="create_random_registered_patient"),
    path("sensor/send/", views.send_sensor_packet, name="send_sensor_packet"),
    path("scenario/create/", views.create_scenario, name="create_scenario"),
    path("scenario/<int:run_id>/telemetry/", views.push_telemetry, name="push_telemetry"),
    path("scenario/<int:run_id>/delete/", views.delete_scenario, name="delete_scenario"),
    path("database/<str:app_label>/<str:model_name>/<path:object_id>/edit/", views.database_record_edit, name="database_record_edit"),
    path("database/<str:app_label>/<str:model_name>/", views.database_table, name="database_table"),
]
