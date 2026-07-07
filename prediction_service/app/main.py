from fastapi import FastAPI
from pydantic import BaseModel, Field, field_validator
from joblib import load
import os

app = FastAPI(title="Prediction Service")

MODEL_PATH = os.path.join(os.path.dirname(__file__), "model.joblib")
model = None

class Features(BaseModel):
    hospital_id: str
    hour: int
    day_of_week: int
    ambulance_cases: int
    walkin_cases: int
    weather_risk: int
    event_risk: int
    outbreak_risk: int

    # ✅ NEW
    risk_score: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("risk_score", mode="before")
    @classmethod
    def normalize_risk_score(cls, v):
        # allow null/blank safely
        if v is None or v == "":
            return 0.0
        try:
            x = float(v)
        except Exception:
            return 0.0
        # clamp 0..1
        if x < 0.0:
            return 0.0
        if x > 1.0:
            return 1.0
        return x

@app.on_event("startup")
def startup():
    global model
    if os.path.exists(MODEL_PATH):
        model = load(MODEL_PATH)

@app.get("/health")
def health():
    return {"status": "ok", "model_loaded": model is not None}

@app.post("/predict")
def predict(f: Features):
    if model is None:
        return {"error": "model not loaded. rebuild image to generate model.joblib"}

    # ✅ IMPORTANT: feature order must match training order
    X = [[
        f.hour,
        f.day_of_week,
        f.ambulance_cases,
        f.walkin_cases,
        f.weather_risk,
        f.event_risk,
        f.outbreak_risk,
        f.risk_score,
    ]]

    pred = model.predict(X)[0]
    return {"hospital_id": f.hospital_id, "predicted_load": pred}
