from django.urls import path
from . import views

urlpatterns = [
    path("", views.ServiceListView.as_view(), name="service_list"),
    path("espace-prestataire/", views.ProviderDashboardView.as_view(), name="provider_dashboard"),
    path("nouveau/", views.ServiceCreateView.as_view(), name="service_create"),
    path("<int:id>/modifier/", views.ServiceUpdateView.as_view(), name="service_update"),
    path("<int:id>/supprimer/", views.ServiceDeleteView.as_view(), name="service_delete"),
    path("disponibilites/", views.AvailabilityListView.as_view(), name="availability_list"),
    path("disponibilites/ajouter/", views.AvailabilityCreateView.as_view(), name="availability_create"),
    path("disponibilites/<int:id>/modifier/", views.AvailabilityUpdateView.as_view(), name="availability_update"),
    path("disponibilites/<int:id>/supprimer/", views.AvailabilityDeleteView.as_view(), name="availability_delete"),
]