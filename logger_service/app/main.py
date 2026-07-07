import os
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Dict, Any, List
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

app = FastAPI(title="Logger Service (MySQL)")

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080", "http://127.0.0.1:8080", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_HOST = os.getenv("DB_HOST", "mysql")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = os.getenv("DB_NAME", "hospitaldb")
DB_USER = os.getenv("DB_USER", "appuser")
DB_PASS = os.getenv("DB_PASS", "apppass")

DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=3600,
)

class LogItem(BaseModel):
    hospital_id: str
    predicted_load: str
    action: str
    resource_status: str = "UNKNOWN"
    plan: Dict[str, Any] = {}
    reserved: Dict[str, Any] = {}

@app.get("/health")
def health():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "db": "connected"}
    except OperationalError as e:
        return {"status": "error", "db": "down", "details": str(e)}

@app.post("/log")
def log(item: LogItem):
    plan = item.plan or {}
    reserved = item.reserved or {}

    beds_needed = int(plan.get("beds_needed", 0))
    staff_needed = int(plan.get("staff_needed", 0))
    ventilators_needed = int(plan.get("ventilators_needed", 0))

    beds_reserved = int(reserved.get("beds", 0))
    staff_reserved = int(reserved.get("staff", 0))
    ventilators_reserved = int(reserved.get("ventilators", 0))

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO decision_logs
                (hospital_id, predicted_load, action, resource_status,
                 beds_needed, staff_needed, ventilators_needed,
                 beds_reserved, staff_reserved, ventilators_reserved)
                VALUES
                (:hospital_id, :predicted_load, :action, :resource_status,
                 :beds_needed, :staff_needed, :ventilators_needed,
                 :beds_reserved, :staff_reserved, :ventilators_reserved)
            """),
            {
                "hospital_id": item.hospital_id,
                "predicted_load": item.predicted_load,
                "action": item.action,
                "resource_status": item.resource_status,
                "beds_needed": beds_needed,
                "staff_needed": staff_needed,
                "ventilators_needed": ventilators_needed,
                "beds_reserved": beds_reserved,
                "staff_reserved": staff_reserved,
                "ventilators_reserved": ventilators_reserved,
            }
        )

    return {"message": "logged"}

@app.get("/logs")
def logs(limit: int = 100) -> Dict[str, Any]:
    limit = max(1, min(limit, 500))

    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT id, hospital_id, predicted_load, action, resource_status,
                       beds_needed, staff_needed, ventilators_needed,
                       beds_reserved, staff_reserved, ventilators_reserved,
                       created_at
                FROM decision_logs
                ORDER BY id DESC
                LIMIT :limit
            """),
            {"limit": limit}
        ).mappings().all()

    items = []
    for r in rows:
        items.append({
            "id": r["id"],
            "hospital_id": r["hospital_id"],
            "predicted_load": r["predicted_load"],
            "action": r["action"],
            "resource_status": r["resource_status"],
            "plan": {
                "beds_needed": r["beds_needed"],
                "staff_needed": r["staff_needed"],
                "ventilators_needed": r["ventilators_needed"],
            },
            "reserved": {
                "beds": r["beds_reserved"],
                "staff": r["staff_reserved"],
                "ventilators": r["ventilators_reserved"],
            },
            "timestamp": str(r["created_at"]),
        })

    return {"count": len(items), "items": items}
