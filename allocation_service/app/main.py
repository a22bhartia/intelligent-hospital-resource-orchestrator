from fastapi import FastAPI
from pydantic import BaseModel, Field, field_validator
from fastapi.middleware.cors import CORSMiddleware
import requests
from uuid import uuid4

app = FastAPI(title="Allocation Service")

# Microservice URLs inside docker network
PREDICTION_URL = "http://prediction-service:8000/predict"
LOGGER_URL = "http://logger-service:8000/log"

# ✅ Event/Outbreak Analyzer URL (inside docker network)
EVENT_URL = "http://event-analyzer-service:8000/analyze"

# Capacity service endpoints (inside docker network)
CAPACITY_GET_URL = "http://capacity-service:8000/capacity/{hospital_id}"
CAPACITY_PUT_URL = "http://capacity-service:8000/capacity/{hospital_id}"
CAPACITY_RESERVE_URL = "http://capacity-service:8000/capacity/{hospital_id}/reserve"
LOAD_SHEDDING_URL = "http://load-shedding-service:8000/shedding/negotiate"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOWED_INCIDENT_TYPES = {"none", "accident", "dengue", "covid", "flood", "fire"}

class Features(BaseModel):
    hospital_id: str
    hour: int
    day_of_week: int
    ambulance_cases: int
    walkin_cases: int

    # IMPORTANT: allow null/missing -> default 0 so Swagger/UI never causes 422
    weather_risk: int = Field(default=0)
    event_risk: int = Field(default=0)
    outbreak_risk: int = Field(default=0)

    # ✅ NEW (must): numeric risk score passed to predictor
    risk_score: float = Field(default=0.0)

    # ✅ Incident inputs (safe defaults)
    incident_type: str = Field(default="none")
    incident_severity: int = Field(default=0)
    recent_risk_series: list[float] = Field(default_factory=list)

    @field_validator("weather_risk", "event_risk", "outbreak_risk", mode="before")
    @classmethod
    def null_to_zero(cls, v):
        if v is None or v == "":
            return 0
        return v

    @field_validator("risk_score", mode="before")
    @classmethod
    def null_to_zero_float(cls, v):
        if v is None or v == "":
            return 0.0
        try:
            return float(v)
        except Exception:
            return 0.0

    @field_validator("incident_type", mode="before")
    @classmethod
    def normalize_incident_type(cls, v):
        if v is None or v == "":
            return "none"
        s = str(v).strip().lower()
        return s if s in ALLOWED_INCIDENT_TYPES else "none"

    @field_validator("incident_severity", mode="before")
    @classmethod
    def normalize_incident_severity(cls, v):
        if v is None or v == "":
            return 0
        try:
            n = int(float(v))
        except Exception:
            n = 0
        if n < 0:
            return 0
        if n > 10:
            return 10
        return n

@app.get("/health")
def health():
    return {"status": "ok"}

def clamp_non_negative(x: int) -> int:
    return x if x >= 0 else 0

def compute_allocation(load: str) -> dict:
    if load == "HIGH":
        return {"beds": 6, "staff": 4, "ventilators": 1}
    if load == "MEDIUM":
        return {"beds": 3, "staff": 2, "ventilators": 0}
    return {"beds": 0, "staff": 0, "ventilators": 0}

def normalize_capacity_payload(cap_json: dict) -> dict:
    if isinstance(cap_json, dict) and "capacity" in cap_json and isinstance(cap_json["capacity"], dict):
        return cap_json["capacity"]
    return cap_json if isinstance(cap_json, dict) else {}


def capacity_ratio(capacity: dict | None) -> float:
    if not capacity:
        return 1.0
    total = max(1, int(capacity.get("beds_total", 0)))
    free = int(capacity.get("beds_free", 0))
    return free / total

@app.post("/decide")
def decide(f: Features):
    # 0) Call event analyzer first (best-effort; do NOT break if it fails)
    an_payload = {
        "hospital_id": f.hospital_id,
        "hour": f.hour,
        "day_of_week": f.day_of_week,
        "weather_risk": f.weather_risk,
        "incident_type": f.incident_type,
        "incident_severity": f.incident_severity,
        "recent_risk_series": f.recent_risk_series,
    }

    an = {}
    an_error = None
    try:
        an_res = requests.post(EVENT_URL, json=an_payload, timeout=5)
        if an_res.ok:
            an = an_res.json() or {}
        else:
            an_error = f"event-analyzer-service returned {an_res.status_code}"
    except Exception as e:
        an_error = str(e)

    # Override/augment risks used for prediction
    f_dict = f.model_dump()

    if isinstance(an, dict):
        # overwrite binary risks
        f_dict["event_risk"] = an.get("event_risk", f.event_risk)
        f_dict["outbreak_risk"] = an.get("outbreak_risk", f.outbreak_risk)

        # ✅ NEW (must): pass numeric risk_score also
        f_dict["risk_score"] = an.get("risk_score", 0.0)
    else:
        f_dict["risk_score"] = 0.0

    # 1) Ask prediction service (now using f_dict)
    try:
        pred_res = requests.post(PREDICTION_URL, json=f_dict, timeout=10)
        pred_res.raise_for_status()
        pred_data = pred_res.json()
    except Exception as e:
        return {"error": "prediction-service failed", "details": str(e)}

    load = pred_data.get("predicted_load")
    if not load:
        return {"error": "prediction failed", "details": pred_data}

    # 2) Decide action text
    if load == "HIGH":
        action = "PREPARE_EXTRA_BEDS_AND_CALL_STAFF"
    elif load == "MEDIUM":
        action = "KEEP_EXTRA_STAFF_ON_ALERT"
    else:
        action = "NO_ACTION"

    # 3) Capacity allocation (real resource update)
    allocation = compute_allocation(load)
    cap_before = None
    cap_after = None
    capacity_update_ok = False
    reservation_id = None

    try:
        cap_res = requests.get(CAPACITY_GET_URL.format(hospital_id=f.hospital_id), timeout=5)
        if cap_res.status_code == 200:
            cap_before = normalize_capacity_payload(cap_res.json())

            if any(allocation[k] > 0 for k in ("beds", "staff", "ventilators")) and cap_before:
                reserve_payload = {
                    "ambulance_id": f"{f.hospital_id}-{uuid4()}",
                    "beds": allocation["beds"],
                    "staff": allocation["staff"],
                    "ventilators": allocation["ventilators"],
                    "initial_confidence": max(0.5, min(1.0, float(f_dict.get("risk_score", 0.0) or 0.0) + 0.35)),
                    "eta_minutes": 30,
                }
                reserve_res = requests.post(
                    CAPACITY_RESERVE_URL.format(hospital_id=f.hospital_id),
                    json=reserve_payload,
                    timeout=7
                )

                if reserve_res.status_code == 200:
                    reserve_data = reserve_res.json()
                    cap_after = normalize_capacity_payload(reserve_data)
                    capacity_update_ok = True
                    reservation_id = reserve_data.get("reservation_id")
                else:
                    cap_after = cap_before
            else:
                cap_after = cap_before
        else:
            cap_before = None
            cap_after = None

    except Exception:
        cap_before = None
        cap_after = None

    # 4) Return rich result (dashboard can show this)
    result = {
        "hospital_id": f.hospital_id,
        "predicted_load": load,
        "action": action,
        "allocation": allocation,
        "capacity_updated": capacity_update_ok,
        "capacity_before": cap_before,
        "capacity_after": cap_after,
        "reservation_id": reservation_id,

        # Useful to confirm the value being sent to predictor
        "risk_score": f_dict.get("risk_score", 0.0),

        "incident": {
            "type": f.incident_type,
            "severity": f.incident_severity,
            "analysis": an,
        },

        "analyzer_error": an_error,
    }

    transfer_recommendation = None
    if load == "HIGH" and capacity_ratio(cap_before) < 0.2:
        try:
            shed_res = requests.post(
                LOAD_SHEDDING_URL,
                json={
                    "source_hospital_id": f.hospital_id,
                    "patient_count_to_redirect": max(1, allocation["beds"]),
                    "severity_level": load,
                },
                timeout=6,
            )
            if shed_res.ok:
                transfer_recommendation = shed_res.json()
        except Exception:
            transfer_recommendation = None

    result["transfer_recommendation"] = transfer_recommendation

    # 5) Log (IMPORTANT: send only what logger-service expects)
    try:
        requests.post(
            LOGGER_URL,
            json={
                "hospital_id": result["hospital_id"],
                "predicted_load": result["predicted_load"],
                "action": result["action"],
                "resource_status": "AVAILABLE" if capacity_update_ok else "UNAVAILABLE",
                "plan": {
                    "beds_needed": allocation["beds"],
                    "staff_needed": allocation["staff"],
                    "ventilators_needed": allocation["ventilators"],
                },
                "reserved": allocation if capacity_update_ok else {"beds": 0, "staff": 0, "ventilators": 0},
            },
            timeout=5
        )
    except Exception:
        pass

    return result
