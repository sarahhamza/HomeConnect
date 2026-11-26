from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("Profil HomeConnect", {"fields": ("role", "phone", "city", "avatar")}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Profil HomeConnect", {"fields": ("email", "role", "phone", "city")}),
    )
    list_display = ("username", "email", "role", "city", "phone", "is_active", "is_staff")
    list_filter = UserAdmin.list_filter + ("role",)
    search_fields = UserAdmin.search_fields + ("phone", "city")
    actions = ["activer", "desactiver"]

    @admin.action(description="Activer les comptes sélectionnés")
    def activer(self, request, queryset):
        n = queryset.update(is_active=True)
        self.message_user(request, f"{n} compte(s) activé(s).")

    @admin.action(description="Désactiver les comptes sélectionnés (sauf le vôtre)")
    def desactiver(self, request, queryset):
        n = queryset.exclude(pk=request.user.pk).update(is_active=False)
        self.message_user(request, f"{n} compte(s) désactivé(s).")


# Titres de l'admin (le logo est dans templates/admin/base_site.html)
admin.site.site_title = "HomeConnect · Administration"
admin.site.index_title = "Tableau de bord"