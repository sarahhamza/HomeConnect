from django.conf import settings
from django.db import models

from appointments.models import Appointment
from services.models import Service


class ServiceRequest(models.Model):
    """Besoin publié par un client ; les prestataires concernés y répondent par une Offer."""

    class Status(models.TextChoices):
        OPEN = "open", "Ouverte"
        ASSIGNED = "assigned", "Attribuée"
        CANCELLED = "cancelled", "Annulée"

    client = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                               related_name="service_requests")
    category = models.CharField("Catégorie", max_length=20, choices=Service.Category.choices)
    title = models.CharField("Titre du besoin", max_length=120)
    description = models.TextField("Description détaillée", max_length=1000)
    city = models.CharField("Ville", max_length=100)
    address = models.CharField("Adresse postale", max_length=255)
    address_details = models.CharField("Bâtiment / Étage / Porte", max_length=150, blank=True)
    access_code = models.CharField("Code d'accès / Interphone", max_length=100, blank=True)
    date = models.DateField("Date souhaitée")
    time_slot = models.CharField("Plage horaire", max_length=5, choices=Appointment.Slot.choices)
    budget = models.DecimalField("Budget indicatif (DT)", max_digits=8, decimal_places=2,
                                 null=True, blank=True)
    urgent = models.BooleanField("Urgent", default=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} · {self.client}"

    @property
    def is_open(self):
        return self.status == self.Status.OPEN


class Offer(models.Model):
    """Proposition d'un prestataire (prix + créneau) en réponse à une ServiceRequest."""

    class Status(models.TextChoices):
        PENDING = "pending", "En attente"
        ACCEPTED = "accepted", "Acceptée"
        DECLINED = "declined", "Refusée"
        WITHDRAWN = "withdrawn", "Retirée"

    request = models.ForeignKey(ServiceRequest, on_delete=models.CASCADE, related_name="offers")
    provider = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                 related_name="offers", limit_choices_to={"role": "provider"})
    service = models.ForeignKey("services.Service", on_delete=models.PROTECT,
                                related_name="offers", verbose_name="Service proposé")
    price = models.DecimalField("Prix proposé (DT)", max_digits=8, decimal_places=2)
    date = models.DateField("Date proposée")
    time_slot = models.CharField("Plage horaire", max_length=5, choices=Appointment.Slot.choices)
    message = models.TextField("Message au client", blank=True, max_length=600)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["request", "provider"], name="one_offer_per_provider"),
        ]

    def __str__(self):
        return f"Offre de {self.provider} pour « {self.request.title} »"