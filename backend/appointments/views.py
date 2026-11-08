from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.functional import cached_property
from django.views import View
from django.views.generic import CreateView, TemplateView

from services.models import Service

from .forms import AppointmentForm
from .models import Appointment


class ClientRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Utilisateur connecté ET de rôle client."""

    def test_func(self):
        return self.request.user.role == "client"

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            messages.error(self.request, "Cet espace est réservé aux clients.")
            return redirect("home")
        return super().handle_no_permission()


class ClientDashboardView(ClientRequiredMixin, TemplateView):
    template_name = "appointments/client_dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = timezone.localdate()
        S = Appointment.Status

        services = Service.objects.filter(is_active=True).select_related("provider")
        q = self.request.GET.get("q", "").strip()
        category = self.request.GET.get("category", "")
        if q:
            services = services.filter(Q(name__icontains=q) | Q(base_city__icontains=q)
                                        | Q(description__icontains=q))
        if category in Service.Category.values:
            services = services.filter(category=category)

        mine = Appointment.objects.filter(client=self.request.user).select_related("service", "provider")
        ctx.update({
            "services": services,
            "services_total": Service.objects.filter(is_active=True).count(),
            "categories": Service.Category.choices,
            "q": q,
            "current_category": category,
            "appointments": mine,
            "upcoming": mine.filter(status__in=[S.PENDING, S.CONFIRMED], date__gte=today).count(),
            "pending": mine.filter(status=S.PENDING).count(),
            "completed": mine.filter(status=S.COMPLETED).count(),
        })
        return ctx


class AppointmentCreateView(ClientRequiredMixin, CreateView):
    model = Appointment
    form_class = AppointmentForm
    template_name = "appointments/appointment_form.html"
    success_url = reverse_lazy("client_dashboard")

    @cached_property
    def service(self):
        return get_object_or_404(Service, id=self.kwargs["service_id"], is_active=True)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["service"] = self.service
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update({"service": self.service, "estimate": self.service.estimate(),
                    "slots": Appointment.Slot.choices})
        return ctx

    def form_valid(self, form):
        appt = form.instance
        appt.client = self.request.user
        appt.service = self.service
        appt.provider = self.service.provider
        appt.total_price = self.service.estimate(urgent=form.cleaned_data["urgent"])["total"]
        messages.success(self.request, "Votre demande de réservation a été envoyée au prestataire.")
        return super().form_valid(form)


class AppointmentCancelView(ClientRequiredMixin, View):
    def post(self, request, id):
        appt = get_object_or_404(Appointment, id=id, client=request.user)
        if appt.can_cancel:
            appt.status = Appointment.Status.CANCELLED
            appt.save(update_fields=["status"])
            messages.success(request, "Réservation annulée.")
        else:
            messages.error(request, "Cette réservation ne peut plus être annulée.")
        return redirect("client_dashboard")