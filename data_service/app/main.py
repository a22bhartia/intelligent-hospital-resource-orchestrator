from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Dict
from datetime import datetime

app = FastAPI(title="Data Service")

# In-memory storage (for beginner version)
ADMISSIONS: List[Dict] = []

class Admission(BaseModel):
    hospital_id: str
    hour: int                 # 0 to 23
    day_of_week: int          # 0=Mon ... 6=Sun
    ambulance_cases: int
    walkin_cases: int
    weather_risk: int         # 0=normal, 1=bad weather
    event_risk: int           # 0=none, 1=big event/festival
    outbreak_risk: int        # 0=none, 1=outbreak

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/admissions")
def add_admission(a: Admission):
    record = a.model_dump()
    record["timestamp"] = datetime.utcnow().isoformat()
    ADMISSIONS.append(record)
    return {"message": "admission stored", "count": len(ADMISSIONS)}

@app.get("/admissions")
def get_admissions():
    return {"count": len(ADMISSIONS), "items": ADMISSIONS[-50:]}  # last 50
