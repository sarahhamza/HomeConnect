from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q


class Appointment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "En attente"
        CONFIRMED = "confirmed", "Confirmé"
        CANCELLED = "cancelled", "Annulé"
        COMPLETED = "completed", "Terminé"
        SEARCHING = "searching", "Recherche d'un prestataire"
        UNMATCHED = "unmatched", "Aucun prestataire disponible"

    class Slot(models.TextChoices):
        S1 = "08-10", "08h00 - 10h00"
        S2 = "10-12", "10h00 - 12h00"
        S3 = "14-16", "14h00 - 16h00"
        S4 = "16-18", "16h00 - 18h00"

    client = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                               related_name="appointments")
    # Vide tant que le matching n'a pas attribué de prestataire
    provider = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                 related_name="provider_appointments",
                                 limit_choices_to={"role": "provider"},
                                 null=True, blank=True)
    service = models.ForeignKey("services.Service", on_delete=models.PROTECT,
                                related_name="appointments")
    date = models.DateField("Date souhaitée")
    time_slot = models.CharField("Plage horaire", max_length=5, choices=Slot.choices)
    address = models.CharField("Adresse postale", max_length=255)
    address_details = models.CharField("Bâtiment / Étage / Porte", max_length=150, blank=True)
    access_code = models.CharField("Code d'accès / Interphone", max_length=100, blank=True)
    description = models.TextField("Description du problème", blank=True, max_length=1000)
    urgent = models.BooleanField("Urgence prioritaire", default=False)
    total_price = models.DecimalField("Total estimé TTC", max_digits=9, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    # --- Matching automatique ---
    # Demande d'origine du client (date / time_slot sont écrasés à l'attribution)
    requested_date = models.DateField(null=True, blank=True)
    requested_slot = models.CharField(max_length=5, blank=True)
    # Départ du délai de 2 h avant réattribution
    assigned_at = models.DateTimeField(null=True, blank=True)
    # Prestataires déjà essayés (refus ou pas de réponse)
    excluded_provider_ids = models.JSONField(default=list, blank=True)
    # Optionnels : sans eux, la proximité est neutre dans le score
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "date", "time_slot"],
                condition=Q(status__in=["pending", "confirmed"]),
                name="unique_active_slot_per_provider",
            )
        ]

    def __str__(self):
        return f"{self.service.name} · {self.client} · {self.date:%d/%m/%Y}"

    @property
    def can_cancel(self):
        return self.status in (self.Status.PENDING, self.Status.CONFIRMED,
                               self.Status.SEARCHING)


class Review(models.Model):
    """Note laissée après une intervention terminée ; alimente le score du matching."""

    appointment = models.OneToOneField(Appointment, on_delete=models.CASCADE,
                                       related_name="review")
    provider = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                 related_name="reviews")
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(blank=True, max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.provider} · {self.rating}/5"
