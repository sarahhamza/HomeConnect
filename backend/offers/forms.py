from django import forms
from django.utils import timezone

from services.models import Service

from appointments.models import Appointment

from .models import Offer, ServiceRequest

FIELD = ("w-full px-4 py-2.5 rounded-lg bg-surface-container-low text-on-surface font-body-md "
         "text-body-md focus:bg-surface-container-lowest focus:outline-none focus:ring-2 "
         "focus:ring-primary")


def _date_widget():
    return forms.DateInput(attrs={"type": "date", "class": FIELD,
                                  "min": timezone.localdate().isoformat()}, format="%Y-%m-%d")


class ServiceRequestForm(forms.ModelForm):
    class Meta:
        model = ServiceRequest
        fields = ("category", "title", "description", "city", "address", "address_details",
                  "access_code", "date", "time_slot", "budget", "urgent")
        widgets = {
            "category": forms.Select(attrs={"class": FIELD}),
            "title": forms.TextInput(attrs={"class": FIELD, "placeholder": "Ex : Fuite sous l'évier de la cuisine"}),
            "description": forms.Textarea(attrs={"class": f"{FIELD} resize-none", "rows": 4,
                                                 "placeholder": "Décrivez précisément votre besoin..."}),
            "city": forms.TextInput(attrs={"class": FIELD, "placeholder": "Tunis"}),
            "address": forms.TextInput(attrs={"class": FIELD, "placeholder": "N°, rue, code postal"}),
            "address_details": forms.TextInput(attrs={"class": FIELD, "placeholder": "Bâtiment, étage, porte"}),
            "access_code": forms.TextInput(attrs={"class": FIELD, "placeholder": "Digicode, interphone"}),
            "time_slot": forms.Select(attrs={"class": FIELD}),
            "budget": forms.NumberInput(attrs={"class": FIELD, "min": "0", "step": "0.01",
                                               "placeholder": "Facultatif"}),
            "urgent": forms.CheckboxInput(attrs={"class": "h-4 w-4 rounded accent-[#00375e]"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].widget = _date_widget()

    def clean_date(self):
        day = self.cleaned_data["date"]
        if day < timezone.localdate():
            raise forms.ValidationError("La date ne peut pas être dans le passé.")
        return day


class OfferForm(forms.ModelForm):
    class Meta:
        model = Offer
        fields = ("service", "price", "date", "time_slot", "message")
        widgets = {
            "service": forms.Select(attrs={"class": FIELD}),
            "price": forms.NumberInput(attrs={"class": FIELD, "min": "0.01", "step": "0.01"}),
            "time_slot": forms.Select(attrs={"class": FIELD}),
            "message": forms.Textarea(attrs={"class": f"{FIELD} resize-none", "rows": 3,
                                             "placeholder": "Présentez-vous, précisez ce qui est inclus..."}),
        }

    def __init__(self, *args, req, provider, **kwargs):
        super().__init__(*args, **kwargs)
        self.req, self.provider = req, provider
        self.fields["date"].widget = _date_widget()
        self.fields["service"].queryset = Service.objects.filter(
            provider=provider, is_active=True, category=req.category)
        self.fields["service"].empty_label = None
        if not self.is_bound:
            self.fields["date"].initial = req.date
            self.fields["time_slot"].initial = req.time_slot

    def clean_price(self):
        price = self.cleaned_data["price"]
        if price <= 0:
            raise forms.ValidationError("Le prix doit être supérieur à 0.")
        return price

    def clean(self):
        cleaned = super().clean()
        day, slot = cleaned.get("date"), cleaned.get("time_slot")
        if day and day < timezone.localdate():
            self.add_error("date", "La date ne peut pas être dans le passé.")
        elif day and slot and Appointment.objects.filter(
                provider=self.provider, date=day, time_slot=slot,
                status__in=[Appointment.Status.PENDING, Appointment.Status.CONFIRMED]).exists():
            raise forms.ValidationError("Vous avez déjà une intervention sur ce créneau.")
        return cleaned