import json
import os
from uuid import uuid4

import redis
import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


app = FastAPI(title="Load Shedding Service")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
TOKEN_TTL_SECONDS = int(os.getenv("TOKEN_TTL_SECONDS", "300"))
CAPACITY_URL = os.getenv("CAPACITY_URL", "http://capacity-service:8000/capacity/{hospital_id}")
CAPACITY_RESERVE_URL = os.getenv("CAPACITY_RESERVE_URL", "http://capacity-service:8000/capacity/{hospital_id}/reserve")
LOGGER_LOGS_URL = os.getenv("LOGGER_LOGS_URL", "http://logger-service:8000/logs?limit=200")
KNOWN_HOSPITALS = [h.strip() for h in os.getenv("KNOWN_HOSPITALS", "H01,H02,H03").split(",") if h.strip()]

redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


class NegotiateRequest(BaseModel):
    source_hospital_id: str
    patient_count_to_redirect: int = Field(..., ge=1)
    severity_level: str


class TransferRecommendation(BaseModel):
    recommended_hospital_id: str
    transfer_score: float
    estimated_beds_available: int
    pre_authorization_token: str
    expires_in_seconds: int


class ConfirmTransferRequest(BaseModel):
    token: str


def load_to_numeric(load: str | None) -> float:
    if load == "HIGH":
        return 1.0
    if load == "MEDIUM":
        return 0.5
    return 0.0


def get_latest_loads() -> dict[str, float]:
    latest = {}
    try:
        response = requests.get(LOGGER_LOGS_URL, timeout=5)
        data = response.json() if response.ok else {}
        for item in data.get("items", []):
            hid = item.get("hospital_id")
            if hid and hid not in latest:
                latest[hid] = load_to_numeric(item.get("predicted_load"))
    except Exception:
        pass
    return latest


def get_capacity(hospital_id: str) -> dict:
    response = requests.get(CAPACITY_URL.format(hospital_id=hospital_id), timeout=5)
    if not response.ok:
        raise HTTPException(status_code=502, detail=f"failed to fetch capacity for {hospital_id}")
    payload = response.json()
    return payload.get("capacity", {})


@app.get("/health")
def health():
    return {"status": "ok", "service": "load-shedding-service"}


@app.post("/shedding/negotiate", response_model=TransferRecommendation)
def negotiate(req: NegotiateRequest):
    latest_loads = get_latest_loads()

    candidates = []
    for hid in KNOWN_HOSPITALS:
        if hid == req.source_hospital_id:
            continue
        cap = get_capacity(hid)
        beds_total = max(1, int(cap.get("beds_total", 0)))
        beds_free = int(cap.get("beds_free", 0))
        if beds_free < req.patient_count_to_redirect:
            continue

        current_load_numeric = latest_loads.get(hid, 0.0)
        score = round(((beds_free / beds_total) * 0.6) + ((1 - current_load_numeric) * 0.4), 4)
        candidates.append(
            {
                "hospital_id": hid,
                "score": score,
                "beds_free": beds_free,
            }
        )

    if not candidates:
        raise HTTPException(status_code=409, detail="no transfer candidates available")

    best = sorted(candidates, key=lambda item: item["score"], reverse=True)[0]
    token = str(uuid4())
    token_payload = {
        "source_hospital_id": req.source_hospital_id,
        "recommended_hospital_id": best["hospital_id"],
        "patient_count_to_redirect": req.patient_count_to_redirect,
        "severity_level": req.severity_level,
    }
    redis_client.setex(f"transfer:{token}", TOKEN_TTL_SECONDS, json.dumps(token_payload))

    return TransferRecommendation(
        recommended_hospital_id=best["hospital_id"],
        transfer_score=best["score"],
        estimated_beds_available=best["beds_free"],
        pre_authorization_token=token,
        expires_in_seconds=TOKEN_TTL_SECONDS,
    )


@app.get("/shedding/validate/{token}")
def validate_token(token: str):
    raw = redis_client.get(f"transfer:{token}")
    if not raw:
        return {"valid": False}
    payload = json.loads(raw)
    ttl = redis_client.ttl(f"transfer:{token}")
    return {"valid": True, **payload, "expires_in_seconds": max(0, ttl)}


@app.post("/shedding/confirm-transfer")
def confirm_transfer(req: ConfirmTransferRequest):
    validation = validate_token(req.token)
    if not validation.get("valid"):
        raise HTTPException(status_code=404, detail="transfer token expired or invalid")

    target = validation["recommended_hospital_id"]
    patient_count = int(validation["patient_count_to_redirect"])
    reserve_payload = {
        "ambulance_id": f"TRANSFER-{req.token}",
        "beds": patient_count,
        "staff": max(1, patient_count // 2),
        "ventilators": 0,
        "initial_confidence": 0.9,
        "eta_minutes": 30,
        "transfer_token": req.token,
    }
    response = requests.post(
        CAPACITY_RESERVE_URL.format(hospital_id=target),
        json=reserve_payload,
        timeout=8,
    )
    if not response.ok:
        raise HTTPException(status_code=response.status_code, detail=response.text)

    redis_client.delete(f"transfer:{req.token}")
    return {
        "ok": True,
        "target_hospital_id": target,
        "reservation": response.json(),
    }
