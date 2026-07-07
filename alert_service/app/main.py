from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime

app = FastAPI(title="Emergency Alert & Coordination Module (Mock)")

ALERTS = []

class Alert(BaseModel):
    hospital_id: str
    level: str
    message: str
    action: str

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/alert")
def send_alert(a: Alert):
    rec = a.model_dump()
    rec["timestamp_utc"] = datetime.utcnow().isoformat()
    ALERTS.append(rec)
    return {"message": "alert_sent", "count": len(ALERTS)}

@app.get("/alerts")
def alerts(limit: int = 100):
    return {"count": len(ALERTS), "items": ALERTS[-limit:]}
