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
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from services.models import Availability, Service
from .models import Appointment, Review

User = get_user_model()
log = logging.getLogger(__name__)

ACTIVE = (Appointment.Status.PENDING, Appointment.Status.CONFIRMED)
SLOT_MINUTES = 120
# Poids du score (identiques à ai_service/app/scoring.py) : conservés dans match_info
# pour que les pages affichent « Note 40 %, Proximité 30 %... » sans rappeler l'IA.
WEIGHTS = {"rating": 0.4, "proximity": 0.3, "availability": 0.2, "load": 0.1}
# Poids du score « côté prestataire » (identiques à REQUEST_WEIGHTS dans ai_service/app/scoring.py)
REQUEST_WEIGHTS = {"proximity": 0.35, "availability": 0.30, "price": 0.20, "priority": 0.15}
OPEN_STATUSES = (Appointment.Status.SEARCHING, Appointment.Status.UNMATCHED)  # sans prestataire
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
        "top_n": 50,                                  # tous les candidats (pour « N comparés »)
        "providers": providers,
    }


def _headers() -> dict:
    key = getattr(settings, "HOMECONNECT_AI_KEY", "")
    return {"X-API-Key": key} if key else {}


def _post_json(path: str, payload: dict) -> dict | None:
    """POST vers le service IA : réponse JSON, ou None s'il est injoignable / répond mal."""
    try:
        r = httpx.post(f"{settings.HOMECONNECT_AI_URL}{path}", json=payload,
                       headers=_headers(), timeout=5)
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError):
        log.exception("Service IA injoignable (%s)", path)
        return None


def _post_match(payload: dict) -> dict | None:
    """Réponse complète du service ({matches, rejected}) ou None s'il est injoignable."""
    data = _post_json("/match", payload)
    if data is None or "matches" not in data:
        return None
    return data


def _call_ai(payload: dict) -> list[dict] | None:
    data = _post_match(payload)
    return None if data is None else data["matches"]


# ------------------------------------------------------------------ pages : aperçu et santé
REASON_LABELS = {
    "excluded": "Déjà sollicité pour cette demande",
    "category": "Catégorie non proposée",
    "out_of_zone": "Hors zone d'intervention",
    "no_slot": "Aucun créneau libre",
}


def display_name(user) -> str:
    return user.get_full_name() or user.username


def reference_service(category: str, urgent: bool = False):
    """Service « de référence » d'une catégorie (le moins cher) : sert de support à une
    demande automatique, l'IA le remplace par celui du prestataire retenu."""
    qs = Service.objects.filter(is_active=True, category=category)
    if urgent:
        qs = qs.filter(has_urgency=True)
    return qs.order_by("price").first()


def preview_matches(category: str, day: date, slot: str, urgent: bool = False) -> dict:
    """Classement que l'IA donnerait à cet instant, SANS rien enregistrer (page staff)."""
    service = reference_service(category, urgent)
    if service is None:
        return {"ok": True, "matches": [], "rejected": [], "empty": True}
    probe = Appointment(service=service, date=day, time_slot=slot, urgent=urgent,
                        excluded_provider_ids=[])
    data = _post_match(build_payload(probe))
    if data is None:
        return {"ok": False, "matches": [], "rejected": []}

    ids = {m["provider_id"] for m in data["matches"]} | {r["provider_id"] for r in data["rejected"]}
    names = {str(u.pk): display_name(u) for u in User.objects.filter(pk__in=ids)}
    for m in data["matches"]:
        m["name"] = names.get(m["provider_id"], m["provider_id"])
        m["percent"] = round(m["score"] * 100)
        m["slot_day"] = datetime.fromisoformat(m["slot_start"]).date()
    for r in data["rejected"]:
        r["name"] = names.get(r["provider_id"], r["provider_id"])
        r["label"] = REASON_LABELS.get(r["reason"], r["reason"])
    return {"ok": True, "matches": data["matches"], "rejected": data["rejected"], "empty": False}


def ai_health() -> dict:
    """État du service FastAPI : {ok, latency_ms, config, error, auth_error}.

    auth_error : le service répond mais refuse la clé (HOMECONNECT_AI_KEY différente) :
    le matching échouerait aussi, donc ok=False.
    """
    base = settings.HOMECONNECT_AI_URL
    started = timezone.now()
    try:
        httpx.get(f"{base}/health", timeout=2).raise_for_status()
    except httpx.HTTPError as exc:
        return {"ok": False, "latency_ms": None, "config": {}, "error": type(exc).__name__,
                "auth_error": False}
    latency = int((timezone.now() - started).total_seconds() * 1000)
    try:
        r = httpx.get(f"{base}/config", headers=_headers(), timeout=2)
        if r.status_code in (401, 403):
            return {"ok": False, "latency_ms": latency, "config": {}, "error": "",
                    "auth_error": True}
        r.raise_for_status()
        cfg = r.json()
    except (httpx.HTTPError, ValueError):
        cfg = {}
    return {"ok": True, "latency_ms": latency, "config": cfg, "error": "", "auth_error": False}


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
        appt.match_info = {
            "score": m.get("score"),
            "breakdown": m.get("breakdown", {}),
            "exact": m.get("exact", True),
            "distance_km": m.get("distance_km"),
            "weights": WEIGHTS,
            "compared": len(matches),                 # prestataires éligibles comparés
            "attempt": len(appt.excluded_provider_ids) + 1,
        }
        try:
            appt.save()                               # contrainte unique_active_slot_per_provider
            return True
        except IntegrityError:
            continue                                  # créneau pris entre-temps : suivant

    return _mark_unmatched(appt)


# ------------------------------------------------------------------ matching inverse : côté prestataire
def _provider_services(user) -> list:
    return list(Service.objects.filter(provider=user, is_active=True))


def _service_request_model():
    """Modèle `ServiceRequest` (app des offres, « Proposer mes services »), ou None si absent."""
    for model in apps.get_models():
        if model.__name__ == "ServiceRequest" and hasattr(model, "offers"):
            return model
    return None


def offer_url(pk) -> str:
    """Formulaire « Proposer mes services » d'un besoin client (app des offres)."""
    try:
        return reverse("offer_create", kwargs={"id": pk})
    except NoReverseMatch:
        return f"/offres/espace-prestataire/demandes-clients/{pk}/proposer/"


def _info(a) -> dict:
    """Champs communs à un Appointment sans prestataire et à un ServiceRequest (besoin client)."""
    if isinstance(a, Appointment):
        return {"kind": "appointment", "rid": f"a{a.pk}", "category": a.service.category,
                "category_label": a.service.get_category_display(), "name": a.service.name,
                "title": "", "urgent": a.urgent, "day": a.requested_date or a.date,
                "slot": a.requested_slot or a.time_slot, "created_at": a.created_at,
                "description": a.description, "budget": None, "city": ""}
    return {"kind": "request", "rid": f"r{a.pk}", "category": a.category,
            "category_label": a.get_category_display(), "name": a.title, "title": a.title,
            "urgent": a.urgent, "day": a.date, "slot": a.time_slot,
            "created_at": a.created_at, "description": a.description,
            "budget": a.budget, "city": a.city}


def _pick_service(services: list, appt):
    """Parmi les services d'un prestataire : même catégorie, même intitulé si possible
    (demandes automatiques), sinon le moins cher (comme `_service_of`).

    Demande automatique (Appointment) urgente : il faut l'option urgence. Besoin client
    (ServiceRequest) : l'urgence ne bloque pas, le prestataire propose son propre prix.
    """
    i = _info(appt)
    strict = i["kind"] == "appointment"
    cands = sorted((s for s in services
                    if s.category == i["category"] and (s.has_urgency or not (i["urgent"] and strict))),
                   key=lambda s: s.price)
    if strict:
        for s in cands:
            if s.name.strip().lower() == i["name"].strip().lower():
                return s
    return cands[0] if cands else None


def provider_payload(user, services: list) -> dict:
    """Le prestataire tel que le service IA l'attend (même format que dans `build_payload`)."""
    today = timezone.localdate()
    agg = Review.objects.filter(provider=user).aggregate(avg=Avg("rating"), n=Count("pk"))
    windows = [{"weekday": a.weekday, "start": a.start_time.isoformat(), "end": a.end_time.isoformat()}
               for a in Availability.objects.filter(provider=user)]
    if not windows and getattr(settings, "MATCHING_DEFAULT_AVAILABILITY", False):
        windows = DEFAULT_WEEKLY
    booked = list(Appointment.objects.filter(provider=user, status__in=ACTIVE, date__gte=today))
    busy = []
    for b in booked:
        st, en = slot_datetimes(b.date, b.time_slot)
        busy.append({"start": st.isoformat(), "end": en.isoformat()})
    lat, lng = getattr(user, "latitude", None), getattr(user, "longitude", None)
    return {
        "id": str(user.pk),
        "categories": sorted({s.category for s in services}),
        "location": {"lat": lat, "lng": lng} if lat is not None and lng is not None else None,
        "radius_km": max(s.radius_km for s in services),
        "rating_avg": float(agg["avg"]) if agg["avg"] is not None else None,
        "rating_count": agg["n"],
        "upcoming_load": len(booked),
        "min_lead_hours": min(s.lead_time_hours for s in services),
        "availabilities": windows,
        "busy": busy,
    }


def open_requests(user, services: list) -> list:
    """Demandes sans prestataire (recherche en cours ou aucun prestataire trouvé) de sa
    catégorie et pas déjà refusées par lui. Celles qu'il ne peut pas servir (ex. urgence sans
    l'option urgence) restent dans la liste : `rank_open_requests` les compte comme « masquées »."""
    cats = {s.category for s in services}
    uid = str(user.pk)
    qs = (Appointment.objects.filter(provider__isnull=True, status__in=OPEN_STATUSES,
                                     requested_date__isnull=False,
                                     date__gte=timezone.localdate())
          .select_related("service", "client").order_by("created_at"))
    return [a for a in qs
            if a.service.category in cats
            and uid not in {str(i) for i in a.excluded_provider_ids}]


def open_service_requests(user, services: list) -> list:
    """Besoins clients (ServiceRequest) encore ouverts, de sa catégorie, sur lesquels il
    n'a pas déjà fait de proposition."""
    model = _service_request_model()
    if model is None:
        return []
    cats = {s.category for s in services}
    return list(model.objects.filter(status=model.Status.OPEN, category__in=cats,
                                     date__gte=timezone.localdate())
                .exclude(offers__provider=user)
                .select_related("client").order_by("created_at"))


def _request_payload(appt, price: float) -> dict:
    i = _info(appt)
    lat, lng = getattr(appt, "latitude", None), getattr(appt, "longitude", None)
    if lat is None and i["kind"] == "request":         # besoin client : position du client
        lat, lng = getattr(appt.client, "latitude", None), getattr(appt.client, "longitude", None)
    waited = (timezone.now() - i["created_at"]).total_seconds() / 3600
    return {
        "id": i["rid"],                                 # a<pk> / r<pk> : pas de collision de pk
        "category": i["category"],
        "desired_at": slot_datetimes(i["day"], i["slot"])[0].isoformat(),
        "location": {"lat": lat, "lng": lng} if lat is not None and lng is not None else None,
        "duration_minutes": SLOT_MINUTES,
        "horizon_days": 2 if i["urgent"] else 7,
        "urgent": i["urgent"],
        "price": price,
        "waiting_hours": round(waited, 2),
    }


def rank_open_requests(user, appts: list | None = None) -> dict:
    """Demandes ouvertes classées pour ce prestataire par le service IA (POST /rank-requests).

    Retourne {ok, no_service, items, hidden} :
      items  : une entrée par demande compatible, triée par score décroissant
               (clés : kind « appointment » ou « request » (besoin client), appt, service, price,
               score, percent, breakdown, slot_start, slot_label, exact, distance_km, + champs
               d'affichage communs : category_label, title, urgent, created_at, description,
               budget, city, wanted_day, wanted_slot_label, offer_url) ;
      hidden : demandes de sa catégorie incompatibles avec ses horaires ou sa zone ;
      ok     : False si le service IA est injoignable.
    """
    services = _provider_services(user)
    if not services:
        return {"ok": True, "no_service": True, "items": [], "hidden": 0}
    if appts is None:                                  # rendez-vous sans prestataire + besoins clients
        pool = open_requests(user, services) + open_service_requests(user, services)
    else:
        pool = appts

    candidates = {}                                    # id -> (demande, service du prestataire, tarif)
    for a in pool:
        svc = _pick_service(services, a)
        if svc is not None:
            urgent = _info(a)["urgent"] and svc.has_urgency
            candidates[_info(a)["rid"]] = (a, svc, float(svc.estimate(urgent=urgent)["total"]))
    if not candidates:
        return {"ok": True, "no_service": False, "items": [], "hidden": len(pool)}

    data = _post_json("/rank-requests", {
        "provider": provider_payload(user, services),
        "requests": [_request_payload(a, price) for a, _, price in candidates.values()],
        "slots": [                                    # seules les 4 plages fixes sont proposables
            {"key": k, "start": slot_bounds(k)[0].isoformat(), "end": slot_bounds(k)[1].isoformat()}
            for k in Appointment.Slot.values
        ],
    })
    if data is None or "ranked" not in data:
        return {"ok": False, "no_service": False, "items": [], "hidden": 0}

    labels = dict(Appointment.Slot.choices)
    items = []
    for m in data["ranked"]:
        if not m.get("slot_key"):                     # sécurité : jamais d'attribution sans plage
            continue
        a, svc, price = candidates[m["request_id"]]
        i = _info(a)
        items.append({
            **m, "appt": a, "service": svc, "price": price,
            "percent": round(m["score"] * 100),
            "slot_start": datetime.fromisoformat(m["slot_start"]),
            "slot_label": labels.get(m["slot_key"], ""),
            "kind": i["kind"], "category_label": i["category_label"], "title": i["title"],
            "urgent": i["urgent"], "created_at": i["created_at"], "description": i["description"],
            "budget": i["budget"], "city": i["city"],
            "wanted_day": i["day"], "wanted_slot_label": labels.get(i["slot"], ""),
            "offer_url": offer_url(a.pk) if i["kind"] == "request" else "",
        })
    return {"ok": True, "no_service": False, "items": items, "hidden": len(pool) - len(items)}


def take_request(user, pk) -> tuple[bool, str]:
    """Le prestataire intervient sur une demande ouverte : (succès, message).

    Le créneau est recalculé ici par le service IA (on ne fait jamais confiance au
    navigateur). L'attribution est une mise à jour conditionnelle atomique : si deux
    prestataires cliquent en même temps, un seul passe.
    """
    appt = (Appointment.objects.select_related("service")
            .filter(pk=pk, provider__isnull=True, status__in=OPEN_STATUSES,
                    requested_date__isnull=False).first())
    if appt is None:
        return False, "Cette demande n'est plus disponible : déjà prise par un autre prestataire ou annulée."
    if str(user.pk) in {str(i) for i in appt.excluded_provider_ids}:
        return False, "Vous avez déjà refusé cette demande."

    ranked = rank_open_requests(user, [appt])
    if not ranked["ok"]:
        return False, "Service IA injoignable : réessayez dans un instant."
    if not ranked["items"]:
        return False, ("Aucun de vos services publiés ne correspond à cette demande, ou elle ne "
                       "correspond plus à vos horaires et à votre zone d'intervention.")
    m = ranked["items"][0]
    service = m["service"]

    try:
        with transaction.atomic():
            taken = Appointment.objects.filter(
                pk=appt.pk, provider__isnull=True, status__in=OPEN_STATUSES,
            ).update(
                provider=user, service=service,
                date=m["slot_start"].date(), time_slot=m["slot_key"],
                total_price=service.estimate(urgent=appt.urgent)["total"],   # tarif du prestataire
                status=Appointment.Status.CONFIRMED,      # il a choisi : pas de 2e confirmation
                assigned_at=timezone.now(),
                match_info={"by": "provider", "exact": m["exact"], "distance_km": m["distance_km"],
                            "provider_score": m["score"],
                            "attempt": len(appt.excluded_provider_ids) + 1},
            )
    except IntegrityError:                                # unique_active_slot_per_provider
        return False, "Vous avez déjà une intervention sur ce créneau."
    if not taken:
        return False, "Un autre prestataire vient de prendre cette demande."
    return True, "Intervention confirmée : le client est prévenu. Retrouvez-la dans « Mes interventions »."


def _mark_unmatched(appt: Appointment) -> bool:
    appt.provider = None
    appt.date = appt.requested_date or appt.date
    appt.time_slot = appt.requested_slot or appt.time_slot
    appt.status = Appointment.Status.UNMATCHED
    appt.assigned_at = None
    appt.match_info = {}
    appt.save()
    return False


def release_and_reassign(appt: Appointment) -> bool:
    """Le prestataire a refusé ou n'a pas répondu : on l'exclut et on passe au suivant."""
    if appt.provider_id is not None:
        appt.excluded_provider_ids = [*appt.excluded_provider_ids, str(appt.provider_id)]
    appt.provider = None
    appt.status = Appointment.Status.SEARCHING
    appt.assigned_at = None
    appt.match_info = {}
    appt.date = appt.requested_date or appt.date
    appt.time_slot = appt.requested_slot or appt.time_slot
    appt.save()
    return assign_provider(appt)