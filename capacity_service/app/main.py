from datetime import datetime, timedelta, timezone
from math import exp
from uuid import uuid4

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from fastapi.middleware.cors import CORSMiddleware
import os
import time
from sqlalchemy import text
import requests

# ---- DB CONFIG (Docker Compose friendly) ----
DB_HOST = os.getenv("DB_HOST", "mysql")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = os.getenv("DB_NAME", "hospitaldb")
DB_USER = os.getenv("DB_USER", "appuser")
DB_PASS = os.getenv("DB_PASS", "apppass")

# Allow override via DATABASE_URL if you ever want
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,   # helps if mysql restarts
    future=True
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

app = FastAPI(title="Capacity Service")
scheduler = BackgroundScheduler(timezone="UTC")

@app.on_event("startup")
def startup_db():
    # Try for ~60 seconds
    for i in range(30):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            Base.metadata.create_all(bind=engine)
            print("✅ Capacity DB connected and tables ready")
            return
        except Exception as e:
            print(f"⏳ Waiting for MySQL... ({i+1}/30) {e}")
            time.sleep(2)

    raise RuntimeError("❌ Could not connect to MySQL after retries")

    # unreachable, but keeps linter happy

@app.on_event("startup")
def startup_scheduler():
    if not scheduler.running:
        scheduler.add_job(decay_active_locks, "interval", seconds=30, id="decay-locks", replace_existing=True)
        scheduler.start()

@app.on_event("shutdown")
def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- DB MODEL ----
class Capacity(Base):
    __tablename__ = "capacities"

    hospital_id = Column(String(20), primary_key=True)

    beds_total = Column(Integer, default=0)
    beds_free = Column(Integer, default=0)

    staff_total = Column(Integer, default=0)
    staff_free = Column(Integer, default=0)

    ventilators_total = Column(Integer, default=0)
    ventilators_free = Column(Integer, default=0)


class ReservationLock(Base):
    __tablename__ = "reservation_locks"

    reservation_id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    hospital_id = Column(String(20), nullable=False, index=True)
    ambulance_id = Column(String(64), nullable=False, unique=True, index=True)
    beds_locked = Column(Integer, default=0)
    staff_locked = Column(Integer, default=0)
    ventilators_locked = Column(Integer, default=0)
    initial_confidence = Column(Float, default=1.0)
    lock_expiry_score = Column(Float, default=1.0)
    decay_lambda = Column(Float, default=0.05)
    lock_timestamp = Column(DateTime, default=datetime.utcnow)
    estimated_arrival_utc = Column(DateTime, nullable=True)
    status = Column(String(20), default="ACTIVE", index=True)
    transfer_token = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    released_at = Column(DateTime, nullable=True)


# ---- API MODELS ----
class CapacityModel(BaseModel):
    beds_total: int
    beds_free: int
    staff_total: int
    staff_free: int
    ventilators_total: int
    ventilators_free: int


class ReserveRequest(BaseModel):
    ambulance_id: str = Field(..., min_length=1)
    beds: int = Field(default=0, ge=0)
    staff: int = Field(default=0, ge=0)
    ventilators: int = Field(default=0, ge=0)
    initial_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    eta_minutes: int = Field(default=30, ge=0)
    decay_lambda: float | None = Field(default=None, ge=0.0)
    transfer_token: str | None = None


class LockStatusItem(BaseModel):
    reservation_id: str
    hospital_id: str
    ambulance_id: str
    status: str
    beds_locked: int
    staff_locked: int
    ventilators_locked: int
    current_confidence: float
    estimated_ttl_seconds: int
    estimated_arrival_utc: str | None


class AmbulanceUpdateRequest(BaseModel):
    ambulance_id: str
    new_eta_minutes: int | None = Field(default=None, ge=0)
    is_cancelled: bool = False

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

DEFAULTS = {
    "beds_total": 60,
    "beds_free": 20,
    "staff_total": 40,
    "staff_free": 10,
    "ventilators_total": 10,
    "ventilators_free": 3,
}

DEFAULTS_BY_HOSPITAL = {
    # you can customize per hospital if you want
    "H01": DEFAULTS,
    "H02": DEFAULTS,
    "H03": DEFAULTS,
}

DECAY_LAMBDA_DEFAULT = float(os.getenv("DECAY_LAMBDA", "0.05"))
DECAY_RELEASE_THRESHOLD = float(os.getenv("DECAY_RELEASE_THRESHOLD", "0.25"))
TOKEN_VALIDATION_URL = os.getenv("TOKEN_VALIDATION_URL", "http://load-shedding-service:8000/shedding/validate/{token}")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def serialize_capacity(row: Capacity) -> dict:
    return {
        "beds_total": row.beds_total,
        "beds_free": row.beds_free,
        "staff_total": row.staff_total,
        "staff_free": row.staff_free,
        "ventilators_total": row.ventilators_total,
        "ventilators_free": row.ventilators_free,
    }


def calculate_decayed_confidence(initial_confidence: float, decay_lambda: float, elapsed_minutes: float) -> float:
    return max(0.0, min(1.0, initial_confidence * exp(-decay_lambda * max(elapsed_minutes, 0.0))))


def compute_lock_metrics(lock: ReservationLock) -> tuple[float, int]:
    now = utcnow()
    elapsed_minutes = (now - (lock.lock_timestamp or now)).total_seconds() / 60.0
    current_confidence = calculate_decayed_confidence(
        lock.initial_confidence or 1.0,
        lock.decay_lambda or DECAY_LAMBDA_DEFAULT,
        elapsed_minutes,
    )
    ttl_seconds = 0
    if lock.estimated_arrival_utc:
        ttl_seconds = max(0, int((lock.estimated_arrival_utc - now).total_seconds()))
    return current_confidence, ttl_seconds


def ensure_capacity_row(db: Session, hospital_id: str) -> Capacity:
    row = db.query(Capacity).filter(Capacity.hospital_id == hospital_id).first()
    if row is None:
        row = Capacity(hospital_id=hospital_id, **DEFAULTS_BY_HOSPITAL.get(hospital_id, DEFAULTS))
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def validate_transfer_token(hospital_id: str, transfer_token: str | None) -> None:
    if not transfer_token:
        return
    try:
        res = requests.get(TOKEN_VALIDATION_URL.format(token=transfer_token), timeout=5)
        payload = res.json()
        if not res.ok or not payload.get("valid"):
            raise HTTPException(status_code=400, detail="invalid transfer token")
        if payload.get("recommended_hospital_id") != hospital_id:
            raise HTTPException(status_code=400, detail="transfer token does not belong to hospital")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"token validation failed: {exc}")


def release_lock(db: Session, lock: ReservationLock, status: str) -> ReservationLock:
    if lock.status != "ACTIVE":
        return lock

    row = ensure_capacity_row(db, lock.hospital_id)
    row.beds_free = min(row.beds_total, row.beds_free + (lock.beds_locked or 0))
    row.staff_free = min(row.staff_total, row.staff_free + (lock.staff_locked or 0))
    row.ventilators_free = min(row.ventilators_total, row.ventilators_free + (lock.ventilators_locked or 0))
    lock.status = status
    lock.lock_expiry_score = max(0.0, lock.lock_expiry_score or 0.0)
    lock.released_at = utcnow()
    db.commit()
    db.refresh(lock)
    return lock


def decay_active_locks():
    db = SessionLocal()
    try:
        active_locks = db.query(ReservationLock).filter(ReservationLock.status == "ACTIVE").all()
        now = utcnow()
        for lock in active_locks:
            current_confidence, ttl_seconds = compute_lock_metrics(lock)
            lock.lock_expiry_score = current_confidence
            eta_passed = bool(lock.estimated_arrival_utc and lock.estimated_arrival_utc <= now)
            if current_confidence < DECAY_RELEASE_THRESHOLD or eta_passed or ttl_seconds == 0:
                release_lock(db, lock, "EXPIRED")
        db.commit()
    finally:
        db.close()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "capacity-service",
        "db_host": DB_HOST,
        "db_name": DB_NAME,
        "db_user": DB_USER
    }

@app.get("/capacity/{hospital_id}")
def get_capacity(hospital_id: str, db: Session = Depends(get_db)):
    row = ensure_capacity_row(db, hospital_id)

    return {
        "hospital_id": row.hospital_id,
        "capacity": serialize_capacity(row)
    }

@app.put("/capacity/{hospital_id}")
def update_capacity(hospital_id: str, data: CapacityModel, db: Session = Depends(get_db)):
    row = db.query(Capacity).filter(Capacity.hospital_id == hospital_id).first()

    if row is None:
        # if someone PUTs without GETting first, we still create a row
        row = Capacity(hospital_id=hospital_id)
        db.add(row)

    row.beds_total = data.beds_total
    row.beds_free = data.beds_free
    row.staff_total = data.staff_total
    row.staff_free = data.staff_free
    row.ventilators_total = data.ventilators_total
    row.ventilators_free = data.ventilators_free

    db.commit()
    db.refresh(row)

    return {
        "ok": True,
        "hospital_id": hospital_id,
        "capacity": serialize_capacity(row)
    }

@app.post("/capacity/{hospital_id}/reset")
def reset_capacity(hospital_id: str, db: Session = Depends(get_db)):
    row = db.query(Capacity).filter(Capacity.hospital_id == hospital_id).first()

    defaults = DEFAULTS_BY_HOSPITAL.get(hospital_id, DEFAULTS)

    if row is None:
        row = Capacity(hospital_id=hospital_id, **defaults)
        db.add(row)
    else:
        row.beds_total = defaults["beds_total"]
        row.beds_free = defaults["beds_free"]
        row.staff_total = defaults["staff_total"]
        row.staff_free = defaults["staff_free"]
        row.ventilators_total = defaults["ventilators_total"]
        row.ventilators_free = defaults["ventilators_free"]

    db.commit()
    db.refresh(row)

    return {
        "ok": True,
        "hospital_id": hospital_id,
        "capacity": serialize_capacity(row)
    }


@app.post("/capacity/reset_all")
def reset_all(db: Session = Depends(get_db)):
    # Reset only known hospitals (H01-H03). Add more if needed.
    hospitals = ["H01", "H02", "H03"]
    out = {}

    for hid in hospitals:
        defaults = DEFAULTS_BY_HOSPITAL.get(hid, DEFAULTS)

        row = db.query(Capacity).filter(Capacity.hospital_id == hid).first()
        if row is None:
            row = Capacity(hospital_id=hid, **defaults)
            db.add(row)
        else:
            row.beds_total = defaults["beds_total"]
            row.beds_free = defaults["beds_free"]
            row.staff_total = defaults["staff_total"]
            row.staff_free = defaults["staff_free"]
            row.ventilators_total = defaults["ventilators_total"]
            row.ventilators_free = defaults["ventilators_free"]

        out[hid] = defaults

    db.commit()
    return {"ok": True, "reset": out}


@app.post("/capacity/{hospital_id}/reserve")
def reserve_capacity(hospital_id: str, payload: ReserveRequest, db: Session = Depends(get_db)):
    validate_transfer_token(hospital_id, payload.transfer_token)

    row = ensure_capacity_row(db, hospital_id)
    if payload.beds > row.beds_free or payload.staff > row.staff_free or payload.ventilators > row.ventilators_free:
        raise HTTPException(status_code=409, detail="insufficient free capacity")

    existing = db.query(ReservationLock).filter(ReservationLock.ambulance_id == payload.ambulance_id).first()
    if existing and existing.status == "ACTIVE":
        raise HTTPException(status_code=409, detail="ambulance already has an active lock")

    row.beds_free -= payload.beds
    row.staff_free -= payload.staff
    row.ventilators_free -= payload.ventilators

    eta = utcnow() + timedelta(minutes=payload.eta_minutes)
    lock = ReservationLock(
        hospital_id=hospital_id,
        ambulance_id=payload.ambulance_id,
        beds_locked=payload.beds,
        staff_locked=payload.staff,
        ventilators_locked=payload.ventilators,
        initial_confidence=payload.initial_confidence,
        lock_expiry_score=payload.initial_confidence,
        decay_lambda=payload.decay_lambda if payload.decay_lambda is not None else DECAY_LAMBDA_DEFAULT,
        lock_timestamp=utcnow(),
        estimated_arrival_utc=eta,
        transfer_token=payload.transfer_token,
    )
    db.add(lock)
    db.commit()
    db.refresh(lock)

    return {
        "ok": True,
        "hospital_id": hospital_id,
        "reservation_id": lock.reservation_id,
        "capacity": serialize_capacity(row),
    }


@app.get("/capacity/locks/status/{hospital_id}")
def lock_status(hospital_id: str, db: Session = Depends(get_db)):
    locks = (
        db.query(ReservationLock)
        .filter(ReservationLock.hospital_id == hospital_id, ReservationLock.status == "ACTIVE")
        .order_by(ReservationLock.created_at.desc())
        .all()
    )

    items = []
    for lock in locks:
        current_confidence, ttl_seconds = compute_lock_metrics(lock)
        lock.lock_expiry_score = current_confidence
        items.append(
            LockStatusItem(
                reservation_id=lock.reservation_id,
                hospital_id=lock.hospital_id,
                ambulance_id=lock.ambulance_id,
                status=lock.status,
                beds_locked=lock.beds_locked,
                staff_locked=lock.staff_locked,
                ventilators_locked=lock.ventilators_locked,
                current_confidence=round(current_confidence, 4),
                estimated_ttl_seconds=ttl_seconds,
                estimated_arrival_utc=lock.estimated_arrival_utc.isoformat() if lock.estimated_arrival_utc else None,
            ).model_dump()
        )
    db.commit()
    return {"hospital_id": hospital_id, "count": len(items), "items": items}


@app.post("/capacity/ambulance-update")
def ambulance_update(payload: AmbulanceUpdateRequest, db: Session = Depends(get_db)):
    lock = db.query(ReservationLock).filter(ReservationLock.ambulance_id == payload.ambulance_id).first()
    if lock is None:
        raise HTTPException(status_code=404, detail="ambulance lock not found")

    if payload.is_cancelled:
        release_lock(db, lock, "CANCELLED")
        return {"ok": True, "status": "CANCELLED", "reservation_id": lock.reservation_id}

    if payload.new_eta_minutes is not None:
        lock.estimated_arrival_utc = utcnow() + timedelta(minutes=payload.new_eta_minutes)
        lock.lock_timestamp = utcnow()

    current_confidence, ttl_seconds = compute_lock_metrics(lock)
    lock.lock_expiry_score = current_confidence
    if current_confidence < DECAY_RELEASE_THRESHOLD:
        release_lock(db, lock, "EXPIRED")
        return {"ok": True, "status": "EXPIRED", "reservation_id": lock.reservation_id}

    db.commit()
    db.refresh(lock)
    return {
        "ok": True,
        "status": lock.status,
        "reservation_id": lock.reservation_id,
        "current_confidence": round(current_confidence, 4),
        "estimated_ttl_seconds": ttl_seconds,
    }
