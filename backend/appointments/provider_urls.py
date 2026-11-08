from django.urls import path

from . import provider_views as v

urlpatterns = [
    path("interventions/", v.ProviderAppointmentListView.as_view(), name="provider_appointments"),
    path("interventions/<int:id>/action/", v.ProviderAppointmentActionView.as_view(),
         name="provider_appointment_action"),
]