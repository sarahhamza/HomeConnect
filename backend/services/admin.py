from django.contrib import admin

from .models import Availability, Service


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("name", "provider", "category", "price", "has_urgency",
                    "radius_km", "is_active")
    list_filter = ("category", "is_active", "has_urgency")
    search_fields = ("name", "provider__username", "provider__email")
    list_select_related = ("provider",)
    actions = ["publier", "depublier"]

    @admin.action(description="Publier les services sélectionnés")
    def publier(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=True)} service(s) publié(s).")

    @admin.action(description="Dépublier les services sélectionnés")
    def depublier(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=False)} service(s) dépublié(s).")


@admin.register(Availability)
class AvailabilityAdmin(admin.ModelAdmin):
    list_display = ("provider", "weekday", "start_time", "end_time")
    list_filter = ("weekday",)
    search_fields = ("provider__username", "provider__email")