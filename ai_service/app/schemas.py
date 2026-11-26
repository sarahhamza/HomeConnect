"""Schémas d'entrée / sortie de POST /match.

Convention : tous les datetime sont en heure LOCALE (Africa/Tunis), sans fuseau
(naïfs). Django doit envoyer `timezone.localtime(dt).replace(tzinfo=None)`.
Les identifiants sont des chaînes (compatible ObjectId MongoDB comme entiers SQL).
"""
from datetime import datetime, time

from pydantic import BaseModel, Field


class Location(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class WeeklySlot(BaseModel):
    """Plage hebdomadaire récurrente. weekday : 0 = lundi ... 6 = dimanche."""

    weekday: int = Field(ge=0, le=6)
    start: time
    end: time


class SlotDef(BaseModel):
    """Plage fixe proposable (ex. key="08-10", 08:00 -> 10:00)."""

    key: str
    start: time
    end: time


class BusySlot(BaseModel):
    """Rendez-vous déjà pris (pending ou confirmed) du prestataire."""

    start: datetime
    end: datetime


class ProviderIn(BaseModel):
    id: str
    categories: list[str]
    location: Location | None = Field(None, description="Absente : proximité neutre")
    radius_km: float = Field(30, gt=0, description="Zone d'intervention")
    rating_avg: float | None = Field(None, ge=0, le=5)
    rating_count: int = Field(0, ge=0)
    upcoming_load: int = Field(0, ge=0, description="Nb de RDV à venir (charge)")
    min_lead_hours: int | None = Field(
        None, ge=0, description="Délai minimum du prestataire ; absent : 2 h par défaut"
    )
    availabilities: list[WeeklySlot]
    busy: list[BusySlot] = []


class MatchRequest(BaseModel):
    category: str
    desired_at: datetime = Field(description="Date/heure souhaitée par le client")
    location: Location | None = Field(None, description="Absente : proximité neutre")
    slots: list[SlotDef] = Field(
        [], description="Si fourni, seules ces plages fixes sont proposées"
    )
    duration_minutes: int = Field(60, ge=15, le=480)
    horizon_days: int = Field(7, ge=1, le=30, description="Fenêtre de recherche")
    prefer_exact_slot: bool = Field(
        True,
        description="Les prestataires libres à l'heure demandée passent avant ceux qu'il "
        "faudrait décaler, quel que soit leur score. À False : classement par score seul.",
    )
    top_n: int = Field(5, ge=1, le=50)
    exclude_provider_ids: list[str] = Field(
        [], description="Prestataires déjà essayés (réattribution)"
    )
    providers: list[ProviderIn]


class ScoreBreakdown(BaseModel):
    rating: float
    proximity: float
    availability: float
    load: float


class Match(BaseModel):
    provider_id: str
    score: float
    slot_start: datetime
    slot_end: datetime
    slot_key: str | None = None
    exact: bool = True  # le créneau proposé est bien celui demandé
    distance_km: float | None = None
    breakdown: ScoreBreakdown


class Rejected(BaseModel):
    provider_id: str
    reason: str  # excluded | category | out_of_zone | no_slot


class MatchResponse(BaseModel):
    matches: list[Match]
    rejected: list[Rejected]


# ---------------------------------------------------------------------------
# Matching inverse : classer les demandes ouvertes pour UN prestataire
# (POST /rank-requests)
# ---------------------------------------------------------------------------
class RequestIn(BaseModel):
    """Demande client sans prestataire, vue par le prestataire."""

    id: str
    category: str
    desired_at: datetime = Field(description="Date/heure souhaitée par le client")
    location: Location | None = Field(None, description="Absente : proximité neutre")
    duration_minutes: int = Field(60, ge=15, le=480)
    horizon_days: int | None = Field(
        None, ge=1, le=30, description="Fenêtre de recherche de cette demande (sinon celle de la requête)"
    )
    urgent: bool = False
    price: float | None = Field(None, ge=0, description="Tarif pour CE prestataire (DT)")
    waiting_hours: float = Field(0, ge=0, description="Ancienneté de la demande")


class RankRequestsRequest(BaseModel):
    provider: ProviderIn
    requests: list[RequestIn]
    slots: list[SlotDef] = Field(
        [], description="Si fourni, seules ces plages fixes sont proposées"
    )
    horizon_days: int = Field(7, ge=1, le=30)
    top_n: int = Field(50, ge=1, le=200)


class RequestScoreBreakdown(BaseModel):
    proximity: float
    availability: float
    price: float
    priority: float


class RankedRequest(BaseModel):
    request_id: str
    score: float
    slot_start: datetime
    slot_end: datetime
    slot_key: str | None = None
    exact: bool = True  # le prestataire peut intervenir à la plage demandée
    distance_km: float | None = None
    breakdown: RequestScoreBreakdown


class RankRequestsResponse(BaseModel):
    ranked: list[RankedRequest]
    rejected: list[Rejected]  # reason : category | out_of_zone | no_slot
