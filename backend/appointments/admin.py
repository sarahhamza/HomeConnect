from django.contrib import admin

from .models import Appointment, Review


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ("id", "client", "provider", "service", "date", "time_slot",
                    "status", "urgent", "total_price")
    list_filter = ("status", "urgent", "time_slot", "date")
    search_fields = ("client__username", "client__email", "provider__username",
                     "provider__email", "service__name")
    list_select_related = ("client", "provider", "service")
    date_hierarchy = "date"
    readonly_fields = ("created_at", "assigned_at", "match_info")
    actions = ["remettre_en_recherche", "annuler"]

    @admin.action(description="Remettre en recherche (sans prestataire) : pour tester « Demandes à pourvoir »")
    def remettre_en_recherche(self, request, queryset):
        n = queryset.update(provider=None, status=Appointment.Status.SEARCHING,
                            assigned_at=None, match_info={})
        self.message_user(request, f"{n} rendez-vous remis en recherche.")

    @admin.action(description="Annuler les rendez-vous sélectionnés")
    def annuler(self, request, queryset):
        n = queryset.update(status=Appointment.Status.CANCELLED)
        self.message_user(request, f"{n} rendez-vous annulé(s).")


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("id", "provider", "rating")
    list_filter = ("rating",)
    search_fields = ("provider__username", "provider__email")