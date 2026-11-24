"""appointments/matching.py : appelle FastAPI POST /match et attribue un prestataire.

Écrit pour tes modèles : Appointment (date + time_slot, provider = User role="provider",
contrainte unique_active_slot_per_provider), services.Service (category, has_urgency,
lead_time_hours, estimate()) et services.Availability (weekday, start_time, end_time).

settings.py :
    HOMECONNECT_AI_URL = "http://127.0.0.1:8001"
    HOMECONNECT_AI_KEY = ""                      # = HOMECONNECT_AI_KEY du service FastAPI
    MAX_ASSIGN_ATTEMPTS = 3                      # prestataires essayés avant "unmatched"
    MATCHING_DEFAULT_AVAILABILITY = False        # True (démo) : sans horaires saisis, lun-sam 8h-18h
"""
import logging
from collections import defaultdict
from datetime import date, datetime, time

import httpx
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.db.models import Avg, Count
from django.utils import timezone

from services.models import Availability, Service
from .models import Appointment, Review

User = get_user_model()
log = logging.getLogger(__name__)

ACTIVE = (Appointment.Status.PENDING, Appointment.Status.CONFIRMED)
SLOT_MINUTES = 120
DEFAULT_WEEKLY = [{"weekday": d, "start": "08:00", "end": "18:00"} for d in range(6)]  # lun-sam


# ------------------------------------------------------------------ helpers
def slot_bounds(key: str) -> tuple[time, time]:
    """'14-16' -> (14:00, 16:00)."""
    start, end = key.split("-")
    return time(int(start)), time(int(end))


def slot_datetimes(day: date, key: str) -> tuple[datetime, datetime]:
    start, end = slot_bounds(key)
    return datetime.combine(day, start), datetime.combine(day, end)


def eligible_services(appt: Appointment):
    """Services qui peuvent répondre à la demande : même catégorie, publiés,
    et avec option d'urgence si le client l'a demandée."""
    qs = Service.objects.filter(is_active=True, category=appt.service.category)
    if appt.urgent:
        qs = qs.filter(has_urgency=True)
    return qs


# ------------------------------------------------------------------ payload
def build_payload(appt: Appointment) -> dict:
    today = timezone.localdate()
    desired_day = appt.requested_date or appt.date
    desired_slot = appt.requested_slot or appt.time_slot

    services_by_provider = defaultdict(list)
    for s in eligible_services(appt):
        services_by_provider[str(s.provider_id)].append(s)

    ratings = {
        str(r["provider"]): (r["avg"], r["n"])
        for r in Review.objects.values("provider").annotate(avg=Avg("rating"), n=Count("pk"))
    }

    weekly = defaultdict(list)
    for a in Availability.objects.all():
        weekly[str(a.provider_id)].append({
            "weekday": a.weekday,
            "start": a.start_time.isoformat(),
            "end": a.end_time.isoformat(),
        })

    booked = defaultdict(list)
    for b in (Appointment.objects.filter(provider__isnull=False, status__in=ACTIVE,
                                         date__gte=today).exclude(pk=appt.pk)):
        booked[str(b.provider_id)].append(b)

    category = appt.service.category
    providers = []
    for u in User.objects.filter(role="provider", is_active=True):
        uid = str(u.pk)
        svcs = services_by_provider.get(uid)
        if not svcs:
            continue
        avg, n = ratings.get(uid, (None, 0))
        lat, lng = getattr(u, "latitude", None), getattr(u, "longitude", None)
        windows = weekly.get(uid) or (
            DEFAULT_WEEKLY if getattr(settings, "MATCHING_DEFAULT_AVAILABILITY", False) else []
        )
        busy = []
        for b in booked[uid]:
            s, e = slot_datetimes(b.date, b.time_slot)
            busy.append({"start": s.isoformat(), "end": e.isoformat()})
        providers.append({
            "id": uid,
            "categories": [category],
            "location": {"lat": lat, "lng": lng} if lat is not None and lng is not None else None,
            "radius_km": max(s.radius_km for s in svcs),
            "rating_avg": float(avg) if avg is not None else None,
            "rating_count": n,
            "upcoming_load": len(booked[uid]),
            "min_lead_hours": min(s.lead_time_hours for s in svcs),
            "availabilities": windows,
            "busy": busy,
        })

    lat, lng = getattr(appt, "latitude", None), getattr(appt, "longitude", None)
    return {
        "category": category,
        "desired_at": slot_datetimes(desired_day, desired_slot)[0].isoformat(),
        "location": {"lat": lat, "lng": lng} if lat is not None and lng is not None else None,
        "duration_minutes": SLOT_MINUTES,
        "horizon_days": 2 if appt.urgent else 7,      # urgence : on cherche vite
        "slots": [
            {"key": k, "start": slot_bounds(k)[0].isoformat(), "end": slot_bounds(k)[1].isoformat()}
            for k in Appointment.Slot.values
        ],
        "exclude_provider_ids": [str(i) for i in appt.excluded_provider_ids],
        "providers": providers,
    }


def _call_ai(payload: dict) -> list[dict] | None:
    headers = {"X-API-Key": settings.HOMECONNECT_AI_KEY} if settings.HOMECONNECT_AI_KEY else {}
    try:
        r = httpx.post(f"{settings.HOMECONNECT_AI_URL}/match", json=payload,
                       headers=headers, timeout=5)
        r.raise_for_status()
        return r.json()["matches"]
    except (httpx.HTTPError, KeyError, ValueError):
        log.exception("Service de matching injoignable")
        return None


def _service_of(provider_pk: str, appt: Appointment):
    """Le service du prestataire retenu : même intitulé si possible, sinon le moins cher."""
    candidates = list(eligible_services(appt).filter(provider_id=provider_pk).order_by("price"))
    for s in candidates:
        if s.name.strip().lower() == appt.service.name.strip().lower():
            return s
    return candidates[0] if candidates else None


# ------------------------------------------------------------------ API publique
def assign_provider(appt: Appointment) -> bool:
    """Attribue le meilleur prestataire : statut -> PENDING (il doit confirmer sous 2 h).

    - Service IA injoignable : le RDV reste SEARCHING, `reassign_pending` réessaiera.
    - Plus de candidat, ou MAX_ASSIGN_ATTEMPTS atteint : statut UNMATCHED.
    """
    if not appt.requested_date:                       # mémorise la demande d'origine
        appt.requested_date, appt.requested_slot = appt.date, appt.time_slot

    if len(appt.excluded_provider_ids) >= settings.MAX_ASSIGN_ATTEMPTS:
        return _mark_unmatched(appt)

    matches = _call_ai(build_payload(appt))
    if matches is None:
        appt.save()
        return False

    for m in matches:
        if not m.get("slot_key"):
            continue
        service = _service_of(m["provider_id"], appt)
        if service is None:
            continue
        appt.provider = User.objects.get(pk=m["provider_id"])
        appt.service = service
        appt.date = datetime.fromisoformat(m["slot_start"]).date()
        appt.time_slot = m["slot_key"]
        appt.total_price = service.estimate(urgent=appt.urgent)["total"]   # tarif du prestataire
        appt.status = Appointment.Status.PENDING
        appt.assigned_at = timezone.now()
        try:
            appt.save()                               # contrainte unique_active_slot_per_provider
            return True
        except IntegrityError:
            continue                                  # créneau pris entre-temps : suivant

    return _mark_unmatched(appt)


def _mark_unmatched(appt: Appointment) -> bool:
    appt.provider = None
    appt.date = appt.requested_date or appt.date
    appt.time_slot = appt.requested_slot or appt.time_slot
    appt.status = Appointment.Status.UNMATCHED
    appt.assigned_at = None
    appt.save()
    return False


def release_and_reassign(appt: Appointment) -> bool:
    """Le prestataire a refusé ou n'a pas répondu : on l'exclut et on passe au suivant."""
    if appt.provider_id is not None:
        appt.excluded_provider_ids = [*appt.excluded_provider_ids, str(appt.provider_id)]
    appt.provider = None
    appt.status = Appointment.Status.SEARCHING
    appt.assigned_at = None
    appt.date = appt.requested_date or appt.date
    appt.time_slot = appt.requested_slot or appt.time_slot
    appt.save()
    return assign_provider(appt)
