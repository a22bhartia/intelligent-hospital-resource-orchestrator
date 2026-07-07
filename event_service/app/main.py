from fastapi import FastAPI
from pydantic import BaseModel
from random import random

app = FastAPI(title="External Event Data Integrator")

class RiskRequest(BaseModel):
    hospital_id: str
    hour: int
    day_of_week: int

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/risk")
def risk(req: RiskRequest):
    # Simple proxy rules to simulate integration:
    # evening hours higher risk; weekends slight increase; random outbreak chance
    weather_risk = 1 if (req.hour >= 18 and req.hour <= 22 and random() < 0.45) else 0
    event_risk = 1 if (req.day_of_week in [5, 6] and random() < 0.35) else 0
    outbreak_risk = 1 if (random() < 0.10) else 0

    return {
        "hospital_id": req.hospital_id,
        "weather_risk": weather_risk,
        "event_risk": event_risk,
        "outbreak_risk": outbreak_risk
    }
