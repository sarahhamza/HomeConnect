from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import RankRequestsRequest
from app.scoring import (
    NEUTRAL_PRICE, PRICE_REF, REQUEST_WEIGHTS, price_score, priority_score, rank_requests,
)

# "maintenant" = lundi 23 nov 2026 à 20h ; demandes pour le mardi 24 à 10h
NOW = datetime(2026, 11, 23, 20, 0)
DESIRED = datetime(2026, 11, 24, 10, 0)
TUNIS = {"lat": 36.8065, "lng": 10.1815}
ARIANA = {"lat": 36.8665, "lng": 10.1647}   # ~7 km
SFAX = {"lat": 34.7406, "lng": 10.7603}     # ~270 km
GRID = [
    {"key": "08-10", "start": "08:00", "end": "10:00"},
    {"key": "10-12", "start": "10:00", "end": "12:00"},
    {"key": "14-16", "start": "14:00", "end": "16:00"},
    {"key": "16-18", "start": "16:00", "end": "18:00"},
]


def prov(**kw):
    base = dict(
        id="p1", categories=["plombier"], location=TUNIS, radius_km=30,
        availabilities=[{"weekday": d, "start": "08:00", "end": "18:00"} for d in range(5)],
        busy=[],
    )
    base.update(kw)
    return base


def rq(rid, **kw):
    base = dict(id=rid, category="plombier", desired_at=DESIRED, location=TUNIS,
                price=100, waiting_hours=0)
    base.update(kw)
    return base


def rank(requests, provider=None, **kw):
    return rank_requests(RankRequestsRequest(
        provider=provider or prov(), requests=requests, slots=GRID,
        **kw), now=NOW)


def test_weights_sum_to_one():
    assert sum(REQUEST_WEIGHTS.values()) == pytest.approx(1.0)


def test_price_and_priority_components():
    assert price_score(None) == NEUTRAL_PRICE
    assert price_score(0) == 0 and price_score(PRICE_REF) == 1 and price_score(10 * PRICE_REF) == 1
    assert priority_score(True, 0) == 1
    assert priority_score(False, 0) == 0 and priority_score(False, 12) == 0.5
    assert priority_score(False, 999) == 1


def test_formula():
    r = rank([rq("a", price=100, waiting_hours=12, duration_minutes=120)])
    m = r.ranked[0]
    b = m.breakdown
    expected = b.proximity * .35 + b.availability * .30 + b.price * .20 + b.priority * .15
    assert m.score == pytest.approx(expected, abs=1e-3)
    assert m.exact and m.slot_key == "10-12"


def test_sorted_by_score_urgent_and_pricey_first():
    r = rank([
        rq("calme", price=40, duration_minutes=120),
        rq("urgent_cher", price=180, urgent=True, duration_minutes=120),
        rq("ancienne", price=40, waiting_hours=20, duration_minutes=120),
    ])
    assert [m.request_id for m in r.ranked] == ["urgent_cher", "ancienne", "calme"]


def test_closer_request_wins():
    r = rank([rq("loin", location=ARIANA, duration_minutes=120),
              rq("proche", location=TUNIS, duration_minutes=120)])
    assert r.ranked[0].request_id == "proche"


def test_filters_reasons():
    r = rank([
        rq("ok", duration_minutes=120),
        rq("autre_metier", category="jardinier", duration_minutes=120),
        rq("trop_loin", location=SFAX, duration_minutes=120),
    ])
    assert [m.request_id for m in r.ranked] == ["ok"]
    assert {x.provider_id: x.reason for x in r.rejected} == {
        "autre_metier": "category", "trop_loin": "out_of_zone"}


def test_no_slot_when_provider_has_no_hours():
    r = rank([rq("a", duration_minutes=120)], provider=prov(availabilities=[]))
    assert r.ranked == [] and r.rejected[0].reason == "no_slot"


def test_busy_slot_is_shifted_and_scores_lower():
    busy = [{"start": datetime(2026, 11, 24, 10, 0), "end": datetime(2026, 11, 24, 12, 0)}]
    free = rank([rq("a", duration_minutes=120)]).ranked[0]
    shifted = rank([rq("a", duration_minutes=120)], provider=prov(busy=busy)).ranked[0]
    assert free.exact and not shifted.exact
    assert shifted.slot_key == "08-10"          # écart 2 h (14-16 : 4 h)
    assert shifted.breakdown.availability < 1 and shifted.score < free.score


def test_missing_coordinates_are_neutral():
    r = rank([rq("a", location=None, duration_minutes=120)]).ranked[0]
    assert r.distance_km is None and r.breakdown.proximity == 0.5
    r2 = rank([rq("b", duration_minutes=120)], provider=prov(location=None)).ranked[0]
    assert r2.breakdown.proximity == 0.5


def test_per_request_horizon_urgent_searches_two_days_only():
    # lundi seulement dispo ; demande le samedi 28 : à 7 jours on trouve le lundi 30, à 2 jours non
    only_monday = prov(availabilities=[{"weekday": 0, "start": "08:00", "end": "18:00"}])
    sat = datetime(2026, 11, 28, 10, 0)
    wide = rank([rq("a", desired_at=sat, duration_minutes=120)], provider=only_monday)
    short = rank([rq("b", desired_at=sat, duration_minutes=120, horizon_days=2)], provider=only_monday)
    assert len(wide.ranked) == 1 and short.ranked == [] and short.rejected[0].reason == "no_slot"


def test_http_endpoint_and_api_key(monkeypatch):
    client = TestClient(app)
    payload = {"provider": prov(), "requests": [rq("a", desired_at="2099-01-05T10:00:00")],
               "horizon_days": 7}
    ok = client.post("/rank-requests", json=payload)
    assert ok.status_code == 200 and ok.json()["ranked"][0]["request_id"] == "a"
    monkeypatch.setenv("HOMECONNECT_AI_KEY", "secret")
    assert client.post("/rank-requests", json=payload).status_code == 401
    assert client.post("/rank-requests", json=payload, headers={"X-API-Key": "secret"}).status_code == 200


def test_config_exposes_request_weights():
    c = TestClient(app).get("/config").json()
    assert c["request_weights"] == {"proximity": 0.35, "availability": 0.30, "price": 0.20, "priority": 0.15}
