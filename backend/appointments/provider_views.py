from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import TemplateView

from services.models import Service

from .models import Appointment


class ProviderRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Utilisateur connecté ET de rôle prestataire."""

    def test_func(self):
        return self.request.user.role == "provider"

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            messages.error(self.request, "Cet espace est réservé aux prestataires.")
            return redirect("home")
        return super().handle_no_permission()


class ProviderAppointmentListView(ProviderRequiredMixin, TemplateView):
    template_name = "appointments/provider_appointments.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        S = Appointment.Status
        mine = Appointment.objects.filter(provider=self.request.user)

        counts = {row["status"]: row["n"]
                  for row in mine.values("status").annotate(n=Count("id"))}
        current = self.request.GET.get("status", "")
        qs = mine.select_related("service", "client")
        if current in S.values:
            qs = qs.filter(status=current)

        ctx.update({
            "appointments": qs,
            "current_status": current,
            "total_count": mine.count(),
            "tabs": [(value, label, counts.get(value, 0)) for value, label in S.choices],
        })
        return ctx


class ProviderAppointmentActionView(ProviderRequiredMixin, View):
    """POST : confirm | refuse | complete (transitions autorisées uniquement)."""

    def post(self, request, id):
        appt = get_object_or_404(Appointment, id=id, provider=request.user)
        S = Appointment.Status
        action = request.POST.get("action")

        if action == "confirm" and appt.status == S.PENDING:
            appt.status = S.CONFIRMED
            messages.success(request, "Intervention confirmée. Le client est informé.")
        elif action == "refuse" and appt.status in (S.PENDING, S.CONFIRMED):
            appt.status = S.CANCELLED
            messages.success(request, "Demande refusée / intervention annulée.")
        elif action == "complete" and appt.status == S.CONFIRMED:
            appt.status = S.COMPLETED
            messages.success(request, "Intervention marquée comme terminée.")
        else:
            messages.error(request, "Action impossible pour l'état actuel de cette intervention.")
            return redirect("provider_appointments")

        appt.save(update_fields=["status"])
        nxt = request.POST.get("next", "")
        if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
            return redirect(nxt)
        return redirect("provider_appointments")


def provider_dashboard_context(user):
    """Toutes les variables utilisées par le template du dashboard prestataire.

    À appeler depuis ta vue existante :  ctx.update(provider_dashboard_context(self.request.user))
    """
    S = Appointment.Status
    today = timezone.localdate()

    services = Service.objects.filter(provider=user)
    mine = Appointment.objects.filter(provider=user).select_related("service", "client")
    pending = mine.filter(status=S.PENDING)
    confirmed = mine.filter(status=S.CONFIRMED)
    completed = mine.filter(status=S.COMPLETED)

    published = services.filter(is_active=True).count()
    total = services.count()
    revenue = completed.filter(date__year=today.year, date__month=today.month) \
                       .aggregate(t=Sum("total_price"))["t"] or 0

    return {
        "today": today,
        "total": total,
        "published": published,
        "drafts": total - published,
        "recent": services[:5],
        "pending_count": pending.count(),
        "pending_requests": pending.order_by("date", "time_slot")[:10],
        "today_appointments": confirmed.filter(date=today).order_by("time_slot"),
        "upcoming_appointments": confirmed.filter(date__gt=today).order_by("date", "time_slot")[:5],
        "completed_count": completed.count(),
        "month_revenue": revenue,
    }


class ProviderDashboardView(ProviderRequiredMixin, TemplateView):
    """Remplace ta vue 'provider_dashboard' actuelle (même nom d'URL)."""
    template_name = "provider_dashboard.html"   # adapte si ton template est ailleurs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(provider_dashboard_context(self.request.user))
        return ctx