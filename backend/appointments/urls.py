from django.urls import path
from . import views
from .matching_urls import urlpatterns as matching_urlpatterns


urlpatterns = [
    path("", views.ClientDashboardView.as_view(), name="client_dashboard"),
    path("reserver/<int:service_id>/", views.AppointmentCreateView.as_view(), name="appointment_create"),
    path("reservations/<int:id>/annuler/", views.AppointmentCancelView.as_view(), name="appointment_cancel"),
    path("service/<int:service_id>/creneaux/", views.FreeSlotsView.as_view(), name="free_slots"),
]

urlpatterns += matching_urlpatterns