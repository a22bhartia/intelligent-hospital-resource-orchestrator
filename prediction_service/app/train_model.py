import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from joblib import dump

# Synthetic training data (beginner-friendly)
data = []
for hour in range(24):
    for dow in range(7):
        for amb in [0, 5, 10, 20]:
            for walk in [0, 10, 20, 40]:
                for weather in [0, 1]:
                    for event in [0, 1]:
                        for outbreak in [0, 1]:
                            # ✅ NEW: sample risk_score values
                            for risk_score in [0.0, 0.2, 0.5, 0.8, 1.0]:

                                # base rule (old signals)
                                score = amb + walk + 10*weather + 10*event + 20*outbreak

                                # ✅ NEW: make risk_score strongly impact the label
                                score += 40 * risk_score  # 0..40

                                if score < 25:
                                    label = "LOW"
                                elif score < 55:
                                    label = "MEDIUM"
                                else:
                                    label = "HIGH"

                                data.append([
                                    hour, dow, amb, walk, weather, event, outbreak, risk_score, label
                                ])

df = pd.DataFrame(
    data,
    columns=[
        "hour", "day_of_week", "ambulance_cases", "walkin_cases",
        "weather_risk", "event_risk", "outbreak_risk", "risk_score", "label"
    ]
)

X = df.drop(columns=["label"])
y = df["label"]

model = RandomForestClassifier(n_estimators=200, random_state=42)
model.fit(X, y)

dump(model, "model.joblib")
print("Saved model.joblib")
