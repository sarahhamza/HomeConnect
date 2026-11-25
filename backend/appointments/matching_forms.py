"""appointments/matching_forms.py : formulaire « trouver un prestataire automatiquement »."""
from django import forms
from django.utils import timezone

from services.models import Service

from .matching import reference_service
from .models import Appointment

INPUT = ("w-full px-4 py-3 rounded-xl bg-surface-container-low border border-outline-variant "
         "text-on-surface placeholder:text-outline focus:outline-none focus:ring-2 "
         "focus:ring-primary/30 focus:border-primary transition")


class AutoRequestForm(forms.Form):
    category = forms.ChoiceField(label="Type d'intervention", choices=Service.Category.choices)
    date = forms.DateField(label="Date souhaitée",
                           widget=forms.DateInput(attrs={"type": "date", "class": INPUT}))
    time_slot = forms.ChoiceField(label="Plage horaire souhaitée",
                                  choices=Appointment.Slot.choices, widget=forms.RadioSelect)
    address = forms.CharField(label="Adresse postale", max_length=255,
                              widget=forms.TextInput(attrs={"class": INPUT + " pl-11",
                                                            "placeholder": "12 rue de la Liberté, Tunis"}))
    address_details = forms.CharField(label="Bâtiment / Étage / Porte", max_length=150, required=False,
                                      widget=forms.TextInput(attrs={"class": INPUT,
                                                                    "placeholder": "Bât. B, 2e étage"}))
    access_code = forms.CharField(label="Code d'accès / Interphone", max_length=100, required=False,
                                  widget=forms.TextInput(attrs={"class": INPUT}))
    description = forms.CharField(label="Description du problème", max_length=1000, required=False,
                                  widget=forms.Textarea(attrs={"class": INPUT, "rows": 4,
                                                               "placeholder": "Ex. fuite sous l'évier de la cuisine…"}))
    urgent = forms.BooleanField(label="Urgence prioritaire", required=False)

    def clean_date(self):
        day = self.cleaned_data["date"]
        if day < timezone.localdate():
            raise forms.ValidationError("Choisissez une date à partir d'aujourd'hui.")
        return day

    def clean(self):
        data = super().clean()
        category, urgent = data.get("category"), data.get("urgent", False)
        if category and reference_service(category, urgent) is None:
            if urgent:
                raise forms.ValidationError(
                    "Aucun prestataire de cette catégorie ne propose l'urgence pour le moment.")
            raise forms.ValidationError(
                "Aucun prestataire ne propose encore cette catégorie de service.")
        return data
