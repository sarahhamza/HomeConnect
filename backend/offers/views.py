from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView

from services.models import Service

from appointments.models import Appointment
from appointments.provider_views import ProviderRequiredMixin
from appointments.views import ClientRequiredMixin

from .forms import OfferForm, ServiceRequestForm
from .models import Offer, ServiceRequest


# ───────────────────────────── Côté client ─────────────────────────────

class ClientRequestListView(ClientRequiredMixin, ListView):
    template_name = "offers/request_list.html"
    context_object_name = "requests"

    def get_queryset(self):
        return (ServiceRequest.objects.filter(client=self.request.user)
                .annotate(pending_offers=Count("offers", filter=Q(offers__status=Offer.Status.PENDING))))


class ClientRequestCreateView(ClientRequiredMixin, CreateView):
    form_class = ServiceRequestForm
    template_name = "offers/request_form.html"

    def form_valid(self, form):
        form.instance.client = self.request.user
        messages.success(self.request, "Votre besoin est publié : les prestataires peuvent y répondre.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("request_detail", kwargs={"id": self.object.pk})


class ClientRequestDetailView(ClientRequiredMixin, DetailView):
    template_name = "offers/request_detail.html"
    context_object_name = "req"
    pk_url_kwarg = "id"

    def get_queryset(self):
        return ServiceRequest.objects.filter(client=self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["offers"] = self.object.offers.select_related("provider", "service")
        return ctx


class ClientRequestCancelView(ClientRequiredMixin, View):
    def post(self, request, id):
        req = get_object_or_404(ServiceRequest, id=id, client=request.user)
        if req.is_open:
            with transaction.atomic():
                req.status = ServiceRequest.Status.CANCELLED
                req.save(update_fields=["status"])
                req.offers.filter(status=Offer.Status.PENDING).update(status=Offer.Status.DECLINED)
            messages.success(request, "Votre besoin a été annulé.")
        else:
            messages.error(request, "Ce besoin ne peut plus être annulé.")
        return redirect("request_list")


class OfferDecisionView(ClientRequiredMixin, View):
    """POST action=accept|decline. Accepter crée le rendez-vous confirmé."""

    def post(self, request, id):
        offer = get_object_or_404(
            Offer.objects.select_related("request", "service", "provider"),
            id=id, request__client=request.user)
        req = offer.request
        back = redirect("request_detail", id=req.pk)
        action = request.POST.get("action")

        if offer.status != Offer.Status.PENDING or not req.is_open:
            messages.error(request, "Cette proposition n'est plus disponible.")
            return back

        if action == "decline":
            offer.status = Offer.Status.DECLINED
            offer.save(update_fields=["status"])
            messages.success(request, "Proposition refusée.")
            return back

        if action != "accept":
            return back

        if offer.date < timezone.localdate():
            messages.error(request, "La date proposée est déjà passée.")
            return back
        busy = Appointment.objects.filter(
            provider=offer.provider, date=offer.date, time_slot=offer.time_slot,
            status__in=[Appointment.Status.PENDING, Appointment.Status.CONFIRMED]).exists()
        if busy:
            messages.error(request, "Ce prestataire n'est plus disponible sur ce créneau.")
            return back

        with transaction.atomic():
            Appointment.objects.create(
                client=req.client, provider=offer.provider, service=offer.service,
                date=offer.date, time_slot=offer.time_slot,
                address=req.address, address_details=req.address_details,
                access_code=req.access_code,
                description=f"{req.title}\n{req.description}"[:1000],
                urgent=req.urgent, total_price=offer.price,
                status=Appointment.Status.CONFIRMED)
            offer.status = Offer.Status.ACCEPTED
            offer.save(update_fields=["status"])
            req.offers.exclude(pk=offer.pk).filter(status=Offer.Status.PENDING) \
                .update(status=Offer.Status.DECLINED)
            req.status = ServiceRequest.Status.ASSIGNED
            req.save(update_fields=["status"])
        messages.success(request, "Proposition acceptée : votre rendez-vous est confirmé.")
        return redirect("client_dashboard")


# ─────────────────────────── Côté prestataire ──────────────────────────

class ProviderRequestListView(ProviderRequiredMixin, ListView):
    template_name = "offers/provider_requests.html"
    context_object_name = "rows"

    def get_queryset(self):
        user = self.request.user
        self.scope = "all" if self.request.GET.get("scope") == "all" else "mine"
        qs = (ServiceRequest.objects.filter(status=ServiceRequest.Status.OPEN,
                                            date__gte=timezone.localdate())
              .select_related("client").order_by("date", "-urgent"))
        if self.scope == "mine":
            cats = Service.objects.filter(provider=user, is_active=True).values_list("category", flat=True)
            qs = qs.filter(category__in=list(cats))
        mine = {o.request_id: o for o in Offer.objects.filter(provider=user, request__in=qs)}
        return [(r, mine.get(r.pk)) for r in qs]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["scope"] = self.scope
        ctx["my_offers"] = (Offer.objects.filter(provider=self.request.user)
                            .select_related("request", "service").order_by("-created_at")[:20])
        return ctx


class OfferCreateView(ProviderRequiredMixin, CreateView):
    form_class = OfferForm
    template_name = "offers/offer_form.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            self.req = get_object_or_404(ServiceRequest, id=kwargs["id"],
                                         status=ServiceRequest.Status.OPEN)
            if Offer.objects.filter(request=self.req, provider=request.user).exists():
                messages.error(request, "Vous avez déjà répondu à ce besoin.")
                return redirect("provider_requests")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update(req=self.req, provider=self.request.user)
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["req"] = self.req
        ctx["has_services"] = ctx["form"].fields["service"].queryset.exists()
        return ctx

    def form_valid(self, form):
        form.instance.request = self.req
        form.instance.provider = self.request.user
        messages.success(self.request, "Votre proposition a été envoyée au client.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("provider_requests")


class OfferWithdrawView(ProviderRequiredMixin, View):
    def post(self, request, id):
        offer = get_object_or_404(Offer, id=id, provider=request.user)
        if offer.status == Offer.Status.PENDING:
            offer.status = Offer.Status.WITHDRAWN
            offer.save(update_fields=["status"])
            messages.success(request, "Proposition retirée.")
        else:
            messages.error(request, "Cette proposition ne peut plus être retirée.")
        return redirect("provider_requests")