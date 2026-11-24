from collections import defaultdict

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, TemplateView, UpdateView

from appointments.provider_views import provider_dashboard_context

from .forms import ServiceForm
from .models import Service
from .forms import AvailabilityForm
from .models import Availability

class ProviderRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Utilisateur connecté ET de rôle prestataire."""

    def test_func(self):
        return self.request.user.role == "provider"

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            messages.error(self.request, "Cet espace est réservé aux prestataires.")
            return redirect("home")
        return super().handle_no_permission()


class OwnServiceMixin(ProviderRequiredMixin):
    """Le prestataire ne voit que SES services (sinon 404)."""

    def get_queryset(self):
        return Service.objects.filter(provider=self.request.user)


class PublishActionMixin:
    """Bouton « Publier » = actif, bouton « Brouillon » = inactif."""

    def form_valid(self, form):
        draft = self.request.POST.get("action") == "draft"
        form.instance.is_active = not draft
        messages.success(self.request, "Brouillon enregistré." if draft else "Service publié.")
        return super().form_valid(form)


class ProviderDashboardView(ProviderRequiredMixin, TemplateView):
    template_name = "services/provider_dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # total, published, drafts, recent + demandes, planning, revenus…
        ctx.update(provider_dashboard_context(self.request.user))
        return ctx


class ServiceListView(OwnServiceMixin, ListView):
    template_name = "services/service_list.html"
    context_object_name = "services"


class ServiceCreateView(PublishActionMixin, ProviderRequiredMixin, CreateView):
    model = Service
    form_class = ServiceForm
    template_name = "services/service_form.html"
    success_url = reverse_lazy("service_list")

    def form_valid(self, form):
        form.instance.provider = self.request.user
        return super().form_valid(form)


class ServiceUpdateView(PublishActionMixin, OwnServiceMixin, UpdateView):
    form_class = ServiceForm
    template_name = "services/service_form.html"
    success_url = reverse_lazy("service_list")


class ServiceDeleteView(OwnServiceMixin, DeleteView):
    template_name = "services/service_confirm_delete.html"
    context_object_name = "service"
    success_url = reverse_lazy("service_list")

    def form_valid(self, form):
        messages.success(self.request, "Service supprimé.")
        return super().form_valid(form)
    
class AvailabilityOwnedMixin(ProviderRequiredMixin):
    model = Availability
    success_url = reverse_lazy("availability_list")
 
    def get_queryset(self):
        return Availability.objects.filter(provider=self.request.user)
 
 
class AvailabilityListView(AvailabilityOwnedMixin, ListView):
    template_name = "services/availability_list.html"
    context_object_name = "availabilities"
 
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        slots = defaultdict(list)
        for a in ctx["availabilities"]:
            slots[a.weekday].append(a)
        ctx["week"] = [
            {"label": label, "slots": slots[value]}
            for value, label in Availability.Weekday.choices
        ]
        ctx["total"] = len(ctx["availabilities"])
        return ctx
 
 
class AvailabilityFormMixin(AvailabilityOwnedMixin):
    form_class = AvailabilityForm
    template_name = "services/availability_form.html"
    success_message = ""
 
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["provider"] = self.request.user
        return kwargs
 
    def form_valid(self, form):
        messages.success(self.request, self.success_message)
        return super().form_valid(form)
 
 
class AvailabilityCreateView(AvailabilityFormMixin, CreateView):
    success_message = "Disponibilité ajoutée."


class AvailabilityUpdateView(AvailabilityFormMixin, UpdateView):
    success_message = "Disponibilité modifiée."

 
class AvailabilityDeleteView(AvailabilityOwnedMixin, DeleteView):
    template_name = "services/availability_confirm_delete.html"
 
    def form_valid(self, form):
        messages.success(self.request, "Disponibilité supprimée.")
        return super().form_valid(form)