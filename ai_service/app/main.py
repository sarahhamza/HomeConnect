import os
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException

from .schemas import MatchRequest, MatchResponse
from .scoring import match

app = FastAPI(title="HomeConnect AI", version="1.0.0")


def check_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Si HOMECONNECT_AI_KEY est défini, Django doit envoyer le même en-tête
    X-API-Key. Sans variable, l'API reste ouverte (dev local)."""
    expected = os.getenv("HOMECONNECT_AI_KEY")
    if expected and not secrets.compare_digest(x_api_key or "", expected):
        raise HTTPException(status_code=401, detail="Clé API invalide")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/match", response_model=MatchResponse, dependencies=[Depends(check_api_key)])
def post_match(req: MatchRequest) -> MatchResponse:
    return match(req)
