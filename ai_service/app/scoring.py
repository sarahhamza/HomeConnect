"""Moteur de matching : filtre (métier, zone, créneau) puis classement.

score = note*0.4 + proximité*0.3 + disponibilité*0.2 + charge*0.1
Chaque composante est normalisée entre 0 et 1.
"""
import math
from datetime import datetime, timedelta

from .schemas import (
    Location, Match, MatchRequest, MatchResponse, ProviderIn, Rejected,
    ScoreBreakdown, SlotDef,
)

WEIGHTS = {"rating": 0.4, "proximity": 0.3, "availability": 0.2, "load": 0.1}

SLOT_STEP_MIN = 30        # pas de recherche des créneaux
MIN_LEAD_HOURS = 2        # on ne propose pas un RDV dans moins de 2 h
PRIOR_RATING = 3.5        # note moyenne supposée pour lisser les nouveaux
PRIOR_WEIGHT = 5          # équivaut à 5 avis "fictifs" à PRIOR_RATING
MAX_LOAD = 10             # au-delà, la charge est considérée comme saturée
AVAILABILITY_DECAY_H = 24  # écart de 24 h avec l'heure souhaitée -> score ~0.37
NEUTRAL_PROXIMITY = 0.5   # score de proximité quand une des deux positions est inconnue


def haversine_km(a: Location, b: Location) -> float:
    r = 6371.0
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dphi = p2 - p1
    dlmb = math.radians(b.lng - a.lng)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def rating_score(avg: float | None, count: int) -> float:
    """Moyenne bayésienne : un prestataire avec 1 avis à 5/5 ne bat pas
    un prestataire avec 80 avis à 4.8."""
    avg = PRIOR_RATING if avg is None else avg
    smoothed = (count * avg + PRIOR_WEIGHT * PRIOR_RATING) / (count + PRIOR_WEIGHT)
    return smoothed / 5


def proximity_score(distance_km: float, radius_km: float) -> float:
    return max(0.0, 1 - distance_km / radius_km)


def load_score(upcoming: int) -> float:
    return 1 - min(upcoming, MAX_LOAD) / MAX_LOAD


def availability_score(gap: timedelta) -> float:
    return math.exp(-abs(gap.total_seconds()) / 3600 / AVAILABILITY_DECAY_H)


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


def _candidates(day, window, duration: timedelta, slots: list[SlotDef]):
    """Débuts/fins possibles dans une plage hebdomadaire du prestataire.

    Avec `slots` : uniquement les plages fixes entièrement couvertes par la plage.
    Sans : on balaie la plage par pas de SLOT_STEP_MIN.
    """
    if slots:
        for s in slots:
            if window.start <= s.start and s.end <= window.end:
                yield datetime.combine(day, s.start), datetime.combine(day, s.end), s.key
        return
    step = timedelta(minutes=SLOT_STEP_MIN)
    window_end = datetime.combine(day, window.end)
    start = datetime.combine(day, window.start)
    while start + duration <= window_end:
        yield start, start + duration, None
        start += step


def find_slot(
    provider: ProviderIn,
    desired_at: datetime,
    duration_minutes: int,
    horizon_days: int,
    now: datetime,
    slots: list[SlotDef] | None = None,
    lead_hours: float = MIN_LEAD_HOURS,
) -> tuple[datetime, datetime, str | None] | None:
    """Créneau libre le plus proche de `desired_at` : (début, fin, clé de plage)."""
    duration = timedelta(minutes=duration_minutes)
    earliest = now + timedelta(hours=lead_hours)
    best = None  # (écart, début, fin, clé)

    for offset in range(horizon_days):
        day = (desired_at + timedelta(days=offset)).date()
        for w in provider.availabilities:
            if w.weekday != day.weekday():
                continue
            for start, end, key in _candidates(day, w, duration, slots or []):
                if start < earliest:
                    continue
                if any(_overlaps(start, end, b.start, b.end) for b in provider.busy):
                    continue
                cand = (abs(start - desired_at), start, end, key)
                if best is None or cand[:2] < best[:2]:
                    best = cand

    if best is None:
        return None
    return best[1], best[2], best[3]


def match(req: MatchRequest, now: datetime | None = None) -> MatchResponse:
    now = now or datetime.now()
    excluded = set(req.exclude_provider_ids)
    category = req.category.strip().lower()
    matches: list[Match] = []
    rejected: list[Rejected] = []

    for p in req.providers:
        if p.id in excluded:
            rejected.append(Rejected(provider_id=p.id, reason="excluded"))
            continue
        if category not in {c.strip().lower() for c in p.categories}:
            rejected.append(Rejected(provider_id=p.id, reason="category"))
            continue
        # Sans coordonnées d'un côté ou de l'autre : pas de filtre de zone,
        # proximité neutre (les adresses Appointment sont du texte libre).
        distance = None
        if req.location is not None and p.location is not None:
            distance = haversine_km(req.location, p.location)
            if distance > p.radius_km:
                rejected.append(Rejected(provider_id=p.id, reason="out_of_zone"))
                continue
        lead = MIN_LEAD_HOURS if p.min_lead_hours is None else p.min_lead_hours
        slot = find_slot(p, req.desired_at, req.duration_minutes, req.horizon_days,
                         now, req.slots, lead_hours=lead)
        if slot is None:
            rejected.append(Rejected(provider_id=p.id, reason="no_slot"))
            continue

        parts = ScoreBreakdown(
            rating=rating_score(p.rating_avg, p.rating_count),
            proximity=(NEUTRAL_PROXIMITY if distance is None
                       else proximity_score(distance, p.radius_km)),
            availability=availability_score(slot[0] - req.desired_at),
            load=load_score(p.upcoming_load),
        )
        total = sum(getattr(parts, k) * w for k, w in WEIGHTS.items())
        matches.append(
            Match(
                provider_id=p.id,
                score=round(total, 4),
                slot_start=slot[0],
                slot_end=slot[1],
                slot_key=slot[2],
                exact=slot[0] == req.desired_at,
                distance_km=None if distance is None else round(distance, 2),
                breakdown=ScoreBreakdown(**{k: round(v, 4) for k, v in parts.model_dump().items()}),
            )
        )

    matches.sort(key=lambda m: (
        req.prefer_exact_slot and not m.exact, -m.score, m.slot_start, m.provider_id,
    ))
    return MatchResponse(matches=matches[: req.top_n], rejected=rejected)
