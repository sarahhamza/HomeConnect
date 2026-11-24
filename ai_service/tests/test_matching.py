from datetime import datetime, time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import MatchRequest
from app.scoring import (
    haversine_km, load_score, match, proximity_score, rating_score,
)

# Mardi 24 novembre 2026 (weekday = 1), "maintenant" = lundi 23 à 20h
NOW = datetime(2026, 11, 23, 20, 0)
DESIRED = datetime(2026, 11, 24, 10, 0)
TUNIS = {"lat": 36.8065, "lng": 10.1815}
ARIANA = {"lat": 36.8665, "lng": 10.1647}   # ~7 km
SFAX = {"lat": 34.7406, "lng": 10.7603}     # ~270 km


def provider(pid, loc=TUNIS, cats=("plombier",), **kw):
    base = dict(
        id=pid, categories=list(cats), location=loc, radius_km=30,
        rating_avg=4.5, rating_count=20, upcoming_load=0,
        availabilities=[{"weekday": d, "start": "08:00", "end": "18:00"} for d in range(5)],
        busy=[],
    )
    base.update(kw)
    return base


def req(providers, **kw):
    data = dict(category="plombier", desired_at=DESIRED, location=TUNIS, providers=providers)
    data.update(kw)
    return MatchRequest(**data)


def test_haversine_tunis_ariana():
    from app.schemas import Location
    d = haversine_km(Location(**TUNIS), Location(**ARIANA))
    assert 6 < d < 8


def test_rating_bayesian_prefers_many_reviews():
    assert rating_score(4.8, 80) > rating_score(5.0, 1)


def test_components_bounds():
    assert proximity_score(0, 30) == 1 and proximity_score(60, 30) == 0
    assert load_score(0) == 1 and load_score(99) == 0


def test_exact_slot_is_proposed():
    r = match(req([provider("a")]), now=NOW)
    assert r.matches[0].slot_start == DESIRED
    assert r.matches[0].breakdown.availability == 1.0


def test_weights_formula():
    r = match(req([provider("a")]), now=NOW)
    b = r.matches[0].breakdown
    expected = b.rating * 0.4 + b.proximity * 0.3 + b.availability * 0.2 + b.load * 0.1
    assert r.matches[0].score == pytest.approx(expected, abs=1e-3)


def test_ranking_closer_and_less_loaded_wins():
    r = match(req([
        provider("loin", loc=ARIANA, upcoming_load=8),
        provider("proche", loc=TUNIS, upcoming_load=1),
    ]), now=NOW)
    assert [m.provider_id for m in r.matches] == ["proche", "loin"]


def test_busy_slot_shifts_proposal():
    busy = [{"start": datetime(2026, 11, 24, 10, 0), "end": datetime(2026, 11, 24, 11, 0)}]
    r = match(req([provider("a", busy=busy)]), now=NOW)
    # 9h ou 11h : 1 h d'écart dans les deux cas, le plus tôt gagne l'égalité
    assert r.matches[0].slot_start == datetime(2026, 11, 24, 9, 0)
    assert r.matches[0].breakdown.availability < 1.0


def test_filters_reasons():
    r = match(req([
        provider("ok"),
        provider("autre_metier", cats=("jardinier",)),
        provider("trop_loin", loc=SFAX),
        provider("complet", availabilities=[]),
        provider("deja_essaye"),
    ], exclude_provider_ids=["deja_essaye"]), now=NOW)
    assert [m.provider_id for m in r.matches] == ["ok"]
    reasons = {x.provider_id: x.reason for x in r.rejected}
    assert reasons == {
        "autre_metier": "category", "trop_loin": "out_of_zone",
        "complet": "no_slot", "deja_essaye": "excluded",
    }


def test_min_lead_time():
    # Maintenant = 9h30 le jour J : 10h est dans moins de 2 h, donc refusé
    r = match(req([provider("a")]), now=datetime(2026, 11, 24, 9, 30))
    assert r.matches[0].slot_start >= datetime(2026, 11, 24, 11, 30)


def test_weekend_skips_to_monday():
    sat = datetime(2026, 11, 28, 10, 0)
    r = match(req([provider("a")], desired_at=sat), now=NOW)
    assert r.matches[0].slot_start == datetime(2026, 11, 30, 8, 0)  # lundi 8h


def test_category_case_insensitive():
    r = match(req([provider("a", cats=("Plombier",))]), now=NOW)
    assert len(r.matches) == 1


def test_http_endpoint_and_api_key(monkeypatch):
    client = TestClient(app)
    payload = {
        "category": "plombier",
        "desired_at": "2099-01-05T10:00:00",
        "location": TUNIS,
        "providers": [provider("a")],
    }
    assert client.get("/health").json() == {"status": "ok"}
    assert client.post("/match", json=payload).status_code == 200

    monkeypatch.setenv("HOMECONNECT_AI_KEY", "secret")
    assert client.post("/match", json=payload).status_code == 401
    ok = client.post("/match", json=payload, headers={"X-API-Key": "secret"})
    assert ok.status_code == 200 and ok.json()["matches"][0]["provider_id"] == "a"


# ---- plages fixes (modèle Appointment.Slot) et adresses sans coordonnées ----
GRID = [
    {"key": "08-10", "start": "08:00", "end": "10:00"},
    {"key": "10-12", "start": "10:00", "end": "12:00"},
    {"key": "14-16", "start": "14:00", "end": "16:00"},
    {"key": "16-18", "start": "16:00", "end": "18:00"},
]


def test_fixed_slot_grid_exact_match():
    r = match(req([provider("a")], slots=GRID, duration_minutes=120,
                  desired_at=datetime(2026, 11, 24, 10, 0)), now=NOW)
    m = r.matches[0]
    assert m.slot_key == "10-12" and m.slot_start == datetime(2026, 11, 24, 10, 0)
    assert m.slot_end == datetime(2026, 11, 24, 12, 0)


def test_fixed_slot_grid_busy_slot_goes_to_nearest():
    busy = [{"start": datetime(2026, 11, 24, 10, 0), "end": datetime(2026, 11, 24, 12, 0)}]
    r = match(req([provider("a", busy=busy)], slots=GRID, duration_minutes=120,
                  desired_at=datetime(2026, 11, 24, 10, 0)), now=NOW)
    assert r.matches[0].slot_key == "08-10"   # écart 2 h (14-16 : 4 h)


def test_grid_slot_must_fit_provider_window():
    short = [{"weekday": 1, "start": "08:00", "end": "12:00"}]
    r = match(req([provider("a", availabilities=short)], slots=GRID, duration_minutes=120,
                  desired_at=datetime(2026, 11, 24, 14, 0), horizon_days=1), now=NOW)
    # 14-16 est hors de ses horaires (8h-12h) : on lui propose 10-12, jamais 14-16
    assert r.matches[0].slot_key == "10-12"
    only_afternoon = [{"weekday": 1, "start": "14:00", "end": "15:00"}]  # 1 h < plage de 2 h
    r2 = match(req([provider("b", availabilities=only_afternoon)], slots=GRID,
                   duration_minutes=120, horizon_days=1,
                   desired_at=datetime(2026, 11, 24, 14, 0)), now=NOW)
    assert r2.matches == [] and r2.rejected[0].reason == "no_slot"


def test_missing_coordinates_neutral_proximity_no_zone_filter():
    p = provider("a", loc=None)
    r = match(req([p], location=None), now=NOW)
    assert r.matches[0].distance_km is None
    assert r.matches[0].breakdown.proximity == 0.5
    r2 = match(req([provider("b", loc=SFAX)], location=None), now=NOW)  # pas de rejet
    assert len(r2.matches) == 1


def test_exact_slot_beats_better_scored_shifted_provider():
    busy = [{"start": datetime(2026, 11, 24, 10, 0), "end": datetime(2026, 11, 24, 12, 0)}]
    star = provider("star", rating_avg=5.0, rating_count=100, busy=busy)   # excellent mais pris à 10h
    modest = provider("modest", rating_avg=3.0, rating_count=3, upcoming_load=8)  # libre à 10h
    kw = dict(slots=GRID, duration_minutes=120, desired_at=datetime(2026, 11, 24, 10, 0))
    r = match(req([star, modest], **kw), now=NOW)
    assert [m.provider_id for m in r.matches] == ["modest", "star"]
    assert r.matches[0].exact and not r.matches[1].exact
    r2 = match(req([star, modest], prefer_exact_slot=False, **kw), now=NOW)
    assert r2.matches[0].provider_id == "star"


def test_provider_min_lead_hours_is_respected():
    # now = lundi 20h ; délai 48 h -> au plus tôt mercredi 20h -> jeudi 8h (horaires 8h-18h)
    r = match(req([provider("lent", min_lead_hours=48)]), now=NOW)
    assert r.matches[0].slot_start == datetime(2026, 11, 26, 8, 0)
    r2 = match(req([provider("rapide", min_lead_hours=2)]), now=NOW)
    assert r2.matches[0].slot_start == DESIRED
