from django.urls import path
from . import views

urlpatterns = [
    path("", views.ClientDashboardView.as_view(), name="client_dashboard"),
    path("reserver/<int:service_id>/", views.AppointmentCreateView.as_view(), name="appointment_create"),
    path("reservations/<int:id>/annuler/", views.AppointmentCancelView.as_view(), name="appointment_cancel"),
]