import csv, time, random, requests
from statistics import mean

ALLOCATION_URL = "http://localhost:8003/decide"

def rand_int(a,b): return random.randint(a,b)

def scenario():
    hid = random.choice(["H01","H02","H03"])
    hour = rand_int(0,23)
    day = rand_int(0,6)
    # demand pattern
    evening = 1 if 18 <= hour <= 22 else 0
    ambulance = rand_int(0, 6 + 8*evening)
    walkin = rand_int(2, 20 + 25*evening)
    # leave risks None so event_service fills them
    return {
        "hospital_id": hid,
        "hour": hour,
        "day_of_week": day,
        "ambulance_cases": ambulance,
        "walkin_cases": walkin,
        "weather_risk": None,
        "event_risk": None,
        "outbreak_risk": None
    }

def wait_time_proxy(demand, reserved_staff, load):
    # A simple proxy: more staff reduces waiting; HIGH penalizes
    base = demand / max(reserved_staff + 2, 2)
    if load == "HIGH": base *= 1.4
    elif load == "MEDIUM": base *= 1.15
    return base

def main(n=120, out_csv="simulation_output.csv"):
    rows = []
    waits = []
    readiness = []

    for i in range(n):
        s = scenario()
        r = requests.post(ALLOCATION_URL, json=s, timeout=15)
        data = r.json()

        load = data.get("predicted_load","NA")
        rs = data.get("resource_status","NA")
        reserved_staff = (data.get("reserved",{}) or {}).get("staff", 0)

        demand = s["ambulance_cases"] + s["walkin_cases"]
        wt = wait_time_proxy(demand, reserved_staff, load)

        waits.append(wt)
        readiness.append(1 if rs in ["AVAILABLE","PARTIAL"] else 0)

        rows.append({
            "TimeIndex": i,
            "HospitalID": s["hospital_id"],
            "Hour": s["hour"],
            "DayOfWeek": s["day_of_week"],
            "Demand": demand,
            "PredictedLoad": load,
            "ResourceStatus": rs,
            "Action": data.get("action",""),
            "BedsReserved": (data.get("reserved",{}) or {}).get("beds", 0),
            "StaffReserved": reserved_staff,
            "VentilatorsReserved": (data.get("reserved",{}) or {}).get("ventilators", 0),
            "WaitTimeProxy": round(wt, 2)
        })

        time.sleep(0.05)

    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print("Saved:", out_csv)
    print("Avg wait-time proxy:", round(mean(waits), 2))
    print("Readiness ratio:", round(mean(readiness), 2))

if __name__ == "__main__":
    main()
