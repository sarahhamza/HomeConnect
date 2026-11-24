from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError

TRAVEL_FEE_CHOICES = [
    (0, "Inclus dans la zone d'intervention (0 €)"),
    (20, "Forfait standard zone proche (+20 €)"),
    (35, "Zone élargie > 25 km (+35 €)"),
    (50, "Déplacement grand couronne (+50 €)"),
]


class Service(models.Model):
    
    @property
    def urgency_amount(self):
        """Supplément d'urgence : +30 % de la main d'œuvre (si l'option est proposée)."""
        if not self.has_urgency:
            return Decimal("0.00")
        return (self.price * Decimal("0.30")).quantize(Decimal("0.01"))

    def estimate(self, urgent=False):
        labour = self.price
        travel = Decimal(self.travel_fee)
        urgency = self.urgency_amount if urgent else Decimal("0.00")
        subtotal = labour + travel + urgency
        vat = (subtotal * Decimal("0.10")).quantize(Decimal("0.01"))
        return {"labour": labour, "travel": travel, "urgency": urgency,
                "subtotal": subtotal, "vat": vat, "total": subtotal + vat}
    
    class Category(models.TextChoices):
        PLUMBING = "plomberie", "Plomberie & Sanitaire"
        ELECTRICITY = "electricite", "Électricité générale"
        LOCKSMITH = "serrurerie", "Serrurerie & Dépannage"
        PAINTING = "peinture", "Peinture & Finition"
        HEATING = "chauffage", "Chauffage & Climatisation"
        GARDENING = "jardinage", "Jardinage & Aménagement"

    class Supplies(models.TextChoices):
        PROVIDER = "artisan", "Fournies par l'artisan"
        CLIENT = "client", "À la charge du client"
        QUOTE = "devis", "Au choix après diagnostic"

    class PricingType(models.TextChoices):
        HOURLY = "horaire", "Tarif horaire"
        FIXED = "forfait", "Forfait fixe"
        QUOTE = "devis", "Sur devis libre"

    class LeadTime(models.IntegerChoices):
        H2 = 2, "2 heures"
        H12 = 12, "12 heures"
        H24 = 24, "24 heures"
        H48 = 48, "48 heures"

    provider = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="services", limit_choices_to={"role": "provider"},
    )
    name = models.CharField("Intitulé précis de la prestation", max_length=100)
    category = models.CharField("Catégorie", max_length=20, choices=Category.choices)
    description = models.TextField("Description détaillée", blank=True, max_length=1000)
    supplies = models.CharField("Fournitures", max_length=10, choices=Supplies.choices,
                                default=Supplies.PROVIDER)
    pricing_type = models.CharField("Mode de facturation", max_length=10,
                                    choices=PricingType.choices, default=PricingType.HOURLY)
    price = models.DecimalField("Montant brut facturé (€ HT)", max_digits=8, decimal_places=2,
                                validators=[MinValueValidator(Decimal("0.01"))])
    travel_fee = models.PositiveSmallIntegerField("Frais de déplacement",
                                                  choices=TRAVEL_FEE_CHOICES, default=20)
    has_urgency = models.BooleanField("Intervention d'urgence", default=False)
    base_city = models.CharField("Ville de départ", max_length=100)
    radius_km = models.PositiveSmallIntegerField(
        "Rayon d'action (km)", default=20,
        validators=[MinValueValidator(5), MaxValueValidator(80)],
    )
    lead_time_hours = models.PositiveSmallIntegerField(
        "Délai minimum", choices=LeadTime.choices, default=LeadTime.H24,
    )
    is_active = models.BooleanField("Publié", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.get_category_display()})"
class Availability(models.Model):
    class Weekday(models.IntegerChoices):
        MONDAY = 0, "Lundi"
        TUESDAY = 1, "Mardi"
        WEDNESDAY = 2, "Mercredi"
        THURSDAY = 3, "Jeudi"
        FRIDAY = 4, "Vendredi"
        SATURDAY = 5, "Samedi"
        SUNDAY = 6, "Dimanche"
 
    provider = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="availabilities",
    )
    weekday = models.PositiveSmallIntegerField("Jour", choices=Weekday.choices)
    start_time = models.TimeField("Début")
    end_time = models.TimeField("Fin")
 
    class Meta:
        ordering = ["weekday", "start_time"]
        verbose_name = "Disponibilité"
 
    def __str__(self):
        return f"{self.get_weekday_display()} {self.start_time:%H:%M}-{self.end_time:%H:%M}"
 
    def clean(self):
        if not (self.start_time and self.end_time and self.provider_id):
            return
        if self.end_time <= self.start_time:
            raise ValidationError("L'heure de fin doit être après l'heure de début.")
        overlap = Availability.objects.filter(
            provider_id=self.provider_id,
            weekday=self.weekday,
            start_time__lt=self.end_time,
            end_time__gt=self.start_time,
        ).exclude(pk=self.pk)
        if overlap.exists():
            raise ValidationError("Ce créneau chevauche une disponibilité existante ce jour-là.")