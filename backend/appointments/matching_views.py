"""appointments/matching_views.py : écrans du matching automatique.

- auto_request           (client)    demande « trouver un prestataire automatiquement »
- match_detail           (client)    suivi de l'attribution : étapes, prestataire, score détaillé
- provider_match_action  (prestataire) confirmer / refuser une demande attribuée par l'IA
- provider_open_requests (prestataire) demandes à pourvoir classées par score de compatibilité
- provider_take_request  (prestataire) intervenir sur une demande ouverte
- ai_dashboard           (staff)     santé du service IA, poids du score, simulateur, historique
"""
from collections import Counter
from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Avg, Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from services.models import Service

from .matching import (
    REQUEST_WEIGHTS, WEIGHTS, ai_health, assign_provider, preview_matches, rank_open_requests,
    reference_service, release_and_reassign, take_request,
)
from .matching_forms import AutoRequestForm
from .models import Appointment, Review

S = Appointment.Status

CRITERIA = [  # ordre et libellés d'affichage des 4 critères du score
    ("rating", "Note des clients", "star"),
    ("proximity", "Proximité", "near_me"),
    ("availability", "Disponibilité", "event_available"),
    ("load", "Charge de travail", "balance"),
]
REQUEST_CRITERIA = [  # critères du score côté prestataire (voir REQUEST_WEIGHTS)
    ("proximity", "Proximité", "near_me"),
    ("availability", "Votre disponibilité", "event_available"),
    ("price", "Tarif", "payments"),
    ("priority", "Urgence et attente", "priority_high"),
]
PART_CLASSES = {"rating": "bg-primary", "proximity": "bg-secondary",
                "availability": "bg-tertiary-container", "load": "bg-outline"}


def _weight_rows(weights):
    return [(k, label, icon, round(float(weights.get(k, 0)) * 100)) for k, label, icon in CRITERIA]


def _parts(breakdown, weights):
    """Contribution (en points sur 100) de chaque critère au score : barre empilée du simulateur."""
    return [{"label": label, "cls": PART_CLASSES[k],
             "points": round(float(breakdown.get(k, 0) or 0) * float(weights.get(k, 0)) * 100)}
            for k, label, _ in CRITERIA]


# ------------------------------------------------------------------ client
@login_required
def auto_request(request):
    if request.user.role != "client":
        return redirect("provider_dashboard")

    form = AutoRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        service = reference_service(d["category"], d["urgent"])
        appt = Appointment.objects.create(
            client=request.user, service=service,
            date=d["date"], time_slot=d["time_slot"],
            address=d["address"], address_details=d["address_details"],
            access_code=d["access_code"], description=d["description"],
            urgent=d["urgent"], status=S.SEARCHING,
            total_price=service.estimate(urgent=d["urgent"])["total"],
        )
        assign_provider(appt)          # PENDING si trouvé, UNMATCHED sinon, SEARCHING si IA injoignable
        return redirect("match_detail", pk=appt.pk)

    return render(request, "appointments/auto_request.html", {
        "form": form,
        "slots": Appointment.Slot.choices,
        "today": timezone.localdate(),
        "category_choices": Service.Category.choices,
        "response_hours": Appointment.RESPONSE_DELAY_HOURS,
        "w": {k: round(v * 100) for k, v in WEIGHTS.items()},
    })


def _steps(appt):
    """Les 4 étapes affichées sur la page de suivi : (libellé, détail, état)."""
    st = appt.status
    searching = st == S.SEARCHING
    unmatched = st == S.UNMATCHED
    waiting = st == S.PENDING
    confirmed = st in (S.CONFIRMED, S.COMPLETED)
    cancelled = st == S.CANCELLED

    def state(done, active=False, error=False):
        return "error" if error else "active" if active else "done" if done else "todo"

    return [
        ("Demande envoyée", "Votre besoin et vos préférences sont enregistrés.", "done"),
        ("Recherche du meilleur prestataire",
         "Aucun prestataire disponible pour le moment." if unmatched else
         "Comparaison des prestataires : note, proximité, disponibilité et charge.",
         state(not searching and not unmatched, active=searching, error=unmatched)),
        ("Prestataire attribué",
         "Il a quelques heures pour accepter votre demande." if waiting else
         "Le prestataire retenu confirme votre intervention.",
         state(confirmed or cancelled and appt.provider_id is not None, active=waiting)),
        ("Intervention confirmée", "Vous recevez la confirmation dans « Mes réservations ».",
         state(confirmed, error=cancelled)),
    ]


@login_required
def match_detail(request, pk):
    appt = get_object_or_404(
        Appointment.objects.select_related("service", "provider"), pk=pk, client=request.user)
    info = appt.match_info or {}
    weights = info.get("weights") or WEIGHTS
    breakdown = info.get("breakdown") or {}

    criteria = []
    for key, label, icon in CRITERIA:
        value = float(breakdown.get(key, 0) or 0)
        weight = float(weights.get(key, 0) or 0)
        criteria.append({
            "label": label, "icon": icon,
            "value": round(value * 100),             # qualité du prestataire sur ce critère
            "weight": round(weight * 100),           # poids du critère dans le score
            "points": round(value * weight * 100),   # contribution au score final
        })

    rating = None
    if appt.provider_id:
        rating = Review.objects.filter(provider=appt.provider).aggregate(
            avg=Avg("rating"), n=Count("pk"))

    return render(request, "appointments/match_detail.html", {
        "appt": appt, "info": info, "criteria": criteria, "rating": rating,
        "steps": _steps(appt),
        "requested_label": dict(Appointment.Slot.choices).get(appt.requested_slot, ""),
        "refresh": appt.status == S.SEARCHING,       # la page se recharge tant que ça cherche
    })


# ------------------------------------------------------------------ prestataire
@login_required
@require_POST
def provider_match_action(request, pk):
    """Confirmer ou refuser une demande attribuée par l'IA. Un refus relance le matching."""
    appt = Appointment.objects.filter(pk=pk, provider=request.user).first()
    if appt is None or appt.status != S.PENDING or not appt.is_auto:
        messages.error(request, "Cette demande n'est plus disponible (déjà traitée ou réattribuée).")
    elif request.POST.get("action") == "confirm":
        appt.status = S.CONFIRMED
        appt.save(update_fields=["status"])
        messages.success(request, "Intervention confirmée : le client est prévenu.")
    elif request.POST.get("action") == "refuse":
        release_and_reassign(appt)
        messages.success(request, "Demande refusée : elle est proposée au prestataire suivant.")
    else:
        messages.error(request, "Action inconnue.")

    nxt = request.POST.get("next", "")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return redirect(nxt)
    return redirect("provider_appointments")


@login_required
def provider_open_requests(request):
    """Demandes sans prestataire, classées par score de compatibilité pour CE prestataire."""
    if request.user.role != "provider":
        return redirect("home")
    result = rank_open_requests(request.user)
    for m in result["items"]:
        b = m["breakdown"]
        m["criteria"] = [
            {"label": label, "icon": icon, "value": round(float(b.get(k, 0)) * 100),
             "weight": round(REQUEST_WEIGHTS[k] * 100),
             "points": round(float(b.get(k, 0)) * REQUEST_WEIGHTS[k] * 100)}
            for k, label, icon in REQUEST_CRITERIA
        ]
    return render(request, "appointments/provider_open_requests.html", {
        "result": result,
        "weights": [(label, icon, round(REQUEST_WEIGHTS[k] * 100)) for k, label, icon in REQUEST_CRITERIA],
    })


@login_required
@require_POST
def provider_take_request(request, pk):
    """Intervenir sur une demande ouverte : elle devient CONFIRMÉE pour ce prestataire."""
    if request.user.role != "provider":
        return redirect("home")
    ok, message = take_request(request.user, pk)
    (messages.success if ok else messages.error)(request, message)
    return redirect("provider_appointments" if ok else "provider_open_requests")


# ------------------------------------------------------------------ staff : service IA
def _next_workday():
    day = timezone.localdate() + timedelta(days=1)
    while day.weekday() == 6:
        day += timedelta(days=1)
    return day


@login_required
@user_passes_test(lambda u: u.is_active and u.is_staff, login_url="login")
def ai_dashboard(request):
    health = ai_health()
    weights = (health["config"] or {}).get("weights") or WEIGHTS

    # --- statistiques sur les demandes passées par le matching
    ai_qs = Appointment.objects.exclude(requested_date=None).order_by("-created_at")
    rows = list(ai_qs.select_related("client", "provider", "service")[:200])
    by_status = Counter(a.status for a in rows)
    assigned = sum(1 for a in rows if a.provider_id)
    stats = {
        "total": len(rows),
        "waiting": by_status[S.PENDING],
        "confirmed": by_status[S.CONFIRMED] + by_status[S.COMPLETED],
        "unmatched": by_status[S.UNMATCHED],
        "searching": by_status[S.SEARCHING],
        "assign_rate": round(100 * assigned / len(rows)) if rows else None,
        "reassigned": sum(1 for a in rows if a.excluded_provider_ids),
    }

    # --- simulateur : classement que l'IA donnerait maintenant (rien n'est enregistré)
    sim = {
        "category": request.GET.get("category") or Service.Category.values[0],
        "date": request.GET.get("date") or _next_workday().isoformat(),
        "slot": request.GET.get("slot") or Appointment.Slot.S2.value,
        "urgent": request.GET.get("urgent") == "on",
    }
    result = None
    if "simulate" in request.GET:
        try:
            day = datetime.strptime(sim["date"], "%Y-%m-%d").date()
        except ValueError:
            messages.error(request, "Date invalide.")
        else:
            result = preview_matches(sim["category"], day, sim["slot"], sim["urgent"])
            for m in result["matches"]:
                m["parts"] = _parts(m["breakdown"], weights)

    kpis = [
        ("Demandes", stats["total"], "inbox", "info"),
        ("En attente", stats["waiting"], "hourglass_top", "warn"),
        ("Confirmées", stats["confirmed"], "task_alt", "ok"),
        ("Sans prestataire", stats["unmatched"], "person_off", "bad"),
        ("Taux d'attribution",
         f"{stats['assign_rate']} %" if stats["assign_rate"] is not None else "—", "percent", "info"),
        ("Réattribuées", stats["reassigned"], "autorenew", "info"),
    ]
    return render(request, "ai/dashboard.html", {
        "health": health, "weight_rows": _weight_rows(weights), "stats": stats, "kpis": kpis,
        "recent": rows[:15], "sim": sim, "result": result,
        "categories": Service.Category.choices, "slots": Appointment.Slot.choices,
    })
