from django.conf import settings
from django.db import models



class Appointment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "En attente"
        CONFIRMED = "confirmed", "Confirmé"
        CANCELLED = "cancelled", "Annulé"
        COMPLETED = "completed", "Terminé"

    class Slot(models.TextChoices):
        S1 = "08-10", "08h00 - 10h00"
        S2 = "10-12", "10h00 - 12h00"
        S3 = "14-16", "14h00 - 16h00"
        S4 = "16-18", "16h00 - 18h00"

    client = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                               related_name="appointments")
    provider = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                 related_name="provider_appointments",
                                 limit_choices_to={"role": "provider"})
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

    class Meta:
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return f"{self.service.name} · {self.client} · {self.date:%d/%m/%Y}"

    @property
    def can_cancel(self):
        return self.status in (self.Status.PENDING, self.Status.CONFIRMED)