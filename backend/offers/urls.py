from django.urls import path

from . import views as v

urlpatterns = [
    # Espace client : besoins publiés
    path("espace-client/demandes/", v.ClientRequestListView.as_view(), name="request_list"),
    path("espace-client/demandes/nouvelle/", v.ClientRequestCreateView.as_view(), name="request_create"),
    path("espace-client/demandes/<int:id>/", v.ClientRequestDetailView.as_view(), name="request_detail"),
    path("espace-client/demandes/<int:id>/annuler/", v.ClientRequestCancelView.as_view(), name="request_cancel"),
    path("espace-client/propositions/<int:id>/decision/", v.OfferDecisionView.as_view(), name="offer_decision"),

    # Espace prestataire : répondre aux besoins
    path("espace-prestataire/demandes-clients/", v.ProviderRequestListView.as_view(), name="provider_requests"),
    path("espace-prestataire/demandes-clients/<int:id>/proposer/", v.OfferCreateView.as_view(), name="offer_create"),
    path("espace-prestataire/propositions/<int:id>/retirer/", v.OfferWithdrawView.as_view(), name="offer_withdraw"),
]