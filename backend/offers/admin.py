from django.contrib import admin

from .models import Offer, ServiceRequest


class OfferInline(admin.TabularInline):
    model = Offer
    extra = 0
    readonly_fields = ("provider", "service", "price", "date", "time_slot", "status")
    can_delete = False


@admin.register(ServiceRequest)
class ServiceRequestAdmin(admin.ModelAdmin):
    list_display = ("title", "client", "category", "city", "date", "time_slot",
                    "budget", "urgent", "status")
    list_filter = ("status", "category", "urgent", "date")
    search_fields = ("title", "client__username", "client__email", "city")
    list_select_related = ("client",)
    inlines = [OfferInline]


@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):
    list_display = ("request", "provider", "service", "price", "date", "time_slot", "status")
    list_filter = ("status", "date")
    search_fields = ("request__title", "provider__username", "provider__email")
    list_select_related = ("request", "provider", "service")