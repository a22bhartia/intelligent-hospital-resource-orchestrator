import json
import os
import time
from datetime import date, datetime
from uuid import uuid4

from dtaidistance import dtw
from fastapi import FastAPI, Depends
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, String, DateTime, Date, Text
from sqlalchemy.orm import sessionmaker, declarative_base, Session

app = FastAPI(title="Event/Outbreak Analyzer")

DB_HOST = os.getenv("DB_HOST", "mysql")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = os.getenv("DB_NAME", "hospitaldb")
DB_USER = os.getenv("DB_USER", "appuser")
DB_PASS = os.getenv("DB_PASS", "apppass")
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)
DTW_THRESHOLD = float(os.getenv("DTW_THRESHOLD", "2.5"))
MAX_PATTERN_BOOST = float(os.getenv("MAX_PATTERN_BOOST", "0.2"))

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class EventFingerprint(Base):
    __tablename__ = "event_fingerprints"

    fingerprint_id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    event_type = Column(String(50), nullable=False, index=True)
    temporal_signature = Column(Text, nullable=False)
    source_date = Column(Date, nullable=False)
    hospital_id = Column(String(20), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


@app.on_event("startup")
def startup_db():
    for _ in range(30):
        try:
            with engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
            Base.metadata.create_all(bind=engine)
            return
        except Exception:
            time.sleep(2)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class IncidentIn(BaseModel):
    hospital_id: str
    hour: int
    day_of_week: int
    weather_risk: int = 0
    incident_type: str = Field(default="none")
    incident_severity: int = Field(default=0, ge=0, le=10)
    recent_risk_series: list[float] = Field(default_factory=list)

class IncidentOut(BaseModel):
    event_risk: int
    outbreak_risk: int
    risk_score: float
    label: str
    historical_pattern_boost: float = 0.0
    fingerprint_matches: list[dict] = Field(default_factory=list)


class FingerprintRecordRequest(BaseModel):
    event_type: str
    temporal_signature: list[float] = Field(..., min_length=1)
    source_date: date
    hospital_id: str


class FingerprintMatchRequest(BaseModel):
    event_type: str
    temporal_signature: list[float] = Field(..., min_length=1)


def normalize_signature(signature: list[float]) -> list[float]:
    vals = [max(0.0, float(v)) for v in signature]
    if not vals:
        return []
    max_val = max(vals)
    if max_val <= 0:
        return [0.0 for _ in vals]
    return [round(v / max_val, 6) for v in vals]


def serialize_fingerprint(row: EventFingerprint) -> dict:
    return {
        "fingerprint_id": row.fingerprint_id,
        "event_type": row.event_type,
        "temporal_signature": json.loads(row.temporal_signature),
        "source_date": row.source_date.isoformat(),
        "hospital_id": row.hospital_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def compute_matches(db: Session, event_type: str, signature: list[float]) -> list[dict]:
    normalized = normalize_signature(signature)
    if not normalized:
        return []

    rows = db.query(EventFingerprint).filter(EventFingerprint.event_type == event_type).all()
    matches = []
    for row in rows:
        candidate = json.loads(row.temporal_signature)
        distance = float(dtw.distance(normalized, candidate))
        matches.append(
            {
                "fingerprint_id": row.fingerprint_id,
                "hospital_id": row.hospital_id,
                "source_date": row.source_date.isoformat(),
                "dtw_distance": round(distance, 4),
                "temporal_signature": candidate,
            }
        )
    return sorted(matches, key=lambda item: item["dtw_distance"])[:3]

def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))

@app.get("/health")
def health():
    return {"status": "ok", "service": "event-analyzer-service"}

@app.post("/analyze", response_model=IncidentOut)
def analyze(i: IncidentIn, db: Session = Depends(get_db)):
    t = (i.incident_type or "none").lower().strip()
    sev = max(0, min(10, i.incident_severity))

    # Base score from severity
    score = sev / 10.0

    # Simple rules (you can expand later)
    event_risk = 0
    outbreak_risk = 0
    label = "NONE"

    if t in ["accident", "fire", "flood"]:
        event_risk = 1 if sev >= 3 else 0
        label = t.upper()
        # disasters tend to spike more at night + bad weather
        if i.hour >= 18 or i.hour <= 6:
            score += 0.10
        if i.weather_risk == 1 and t in ["flood"]:
            score += 0.15

    elif t in ["dengue", "covid", "respiratory"]:
        outbreak_risk = 1 if sev >= 3 else 0
        label = t.upper()
        # outbreaks are less "instant spike" but more persistent
        score += 0.10

    score = clamp01(score)

    # convert risk_score to 0/1 signals too (optional)
    if score >= 0.6:
        event_risk = max(event_risk, 1) if t in ["accident", "fire", "flood"] else event_risk
        outbreak_risk = max(outbreak_risk, 1) if t in ["dengue", "covid", "respiratory"] else outbreak_risk

    matches = compute_matches(db, t, i.recent_risk_series)
    historical_pattern_boost = 0.0
    if matches and matches[0]["dtw_distance"] < DTW_THRESHOLD:
        closeness = 1 - (matches[0]["dtw_distance"] / max(DTW_THRESHOLD, 0.0001))
        historical_pattern_boost = round(MAX_PATTERN_BOOST * max(0.0, closeness), 4)
        score = clamp01(score + historical_pattern_boost)

    return IncidentOut(
        event_risk=event_risk,
        outbreak_risk=outbreak_risk,
        risk_score=score,
        label=label,
        historical_pattern_boost=historical_pattern_boost,
        fingerprint_matches=matches,
    )


@app.post("/fingerprints/record")
def record_fingerprint(payload: FingerprintRecordRequest, db: Session = Depends(get_db)):
    row = EventFingerprint(
        event_type=payload.event_type.strip().lower(),
        temporal_signature=json.dumps(normalize_signature(payload.temporal_signature)),
        source_date=payload.source_date,
        hospital_id=payload.hospital_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "fingerprint": serialize_fingerprint(row)}


@app.post("/fingerprints/match")
def match_fingerprint(payload: FingerprintMatchRequest, db: Session = Depends(get_db)):
    matches = compute_matches(db, payload.event_type.strip().lower(), payload.temporal_signature)
    return {"event_type": payload.event_type.strip().lower(), "matches": matches}


@app.get("/fingerprints/library")
def fingerprint_library(db: Session = Depends(get_db)):
    rows = db.query(EventFingerprint).order_by(EventFingerprint.event_type, EventFingerprint.created_at.desc()).all()
    grouped = {}
    for row in rows:
        grouped.setdefault(row.event_type, []).append(serialize_fingerprint(row))
    return {"groups": grouped}
