# Intelligent Hospital Resource Orchestrator (IHRO)

[![Microservices Architecture](https://img.shields.io/badge/Architecture-Microservices-blue.svg)](#architecture)
[![FastAPI](https://img.shields.io/badge/API-FastAPI-green.svg)](https://fastapi.tiangolo.com/)
[![MySQL](https://img.shields.io/badge/Database-MySQL-blue.svg)](https://www.mysql.com/)
[![Redis](https://img.shields.io/badge/Cache-Redis-red.svg)](https://redis.io/)
[![scikit-learn](https://img.shields.io/badge/Machine%20Learning-scikit--learn-orange.svg)](https://scikit-learn.org/)
[![Docker](https://img.shields.io/badge/Container-Docker-blue.svg)](https://www.docker.com/)

An enterprise-grade, distributed microservices platform designed for real-time emergency department load forecasting, dynamic resource capacity locking with temporal confidence decay, and automated inter-hospital load-shedding negotiation. 

---

## 🌟 Motivation

During mass casualty events, disease outbreaks, or natural disasters, emergency departments (ED) face extreme, unpredictable surges in patient volume. Traditional hospital management systems rely on manual, reactive resource allocation, leading to critical delays and uneven patient distribution. 

**IHRO** solves this by creating a proactive, self-negotiating microservice network. It uses machine learning to forecast surges, implements autonomous resource reservations that expire dynamically if ambulances are delayed (preventing inventory lockups), and negotiates capacity transfers between regional hospitals during overflow conditions.

---

## ✨ Features

- **🔮 ML-Driven Workload Forecasting:** A Random Forest classification model predicts hospital workload levels (`LOW`, `MEDIUM`, `HIGH`) based on historical time-of-day, day-of-week patterns, walk-ins, weather conditions, and community outbreak signals.
- **⏳ Temporal Confidence Decay Capacity Locks:** Resource reservations (beds, staff, ventilators) are converted into time-aware locks. These locks dynamically decay using a configurable exponential decay formula:
  $$C(t) = C_0 \cdot e^{-\lambda t}$$
  If ambulance updates or ETAs are delayed and the score falls below a threshold ($0.25$), resources are automatically returned to the pool to prevent resource starvation.
- **🤝 Inter-Hospital Load-Shedding & Transfer Negotiation:** When a hospital is under `HIGH` load and its free bed ratio drops below $20\%$, the negotiation layer identifies the optimal target hospital in the network. A short-lived, Redis-backed pre-authorization token is generated to secure the resource transfer.
- **🔍 Dynamic Time Warping (DTW) Event Fingerprinting:** Incoming temporal risk signatures are compared against historical disaster patterns using Dynamic Time Warping. Matching event shapes (e.g., similar progression to a past flood or viral outbreak) apply a predictive confidence boost to the current risk calculation.
- **📊 Real-Time Operations Dashboard:** A responsive single-page application incorporating:
  - **Leaflet Map:** Live geographical tracking of hospital status markers (colored dynamically by workload).
  - **Chart.js Visualizations:** Multi-series trend charts and event shape overlays.
  - **Operational Telemetry:** Action triggers, capacity status, transfer confirmations, and searchable audit logs.

---

## 🏗️ Architecture

The system consists of **7 microservices** communicating over an internal Docker network, backed by **MySQL** for persistence and **Redis** for stateful session token management.

### System Interaction Diagram
```mermaid
sequenceDiagram
    autonumber
    actor Admin as Operations Dashboard
    participant Alloc as Allocation Service
    participant Analyzer as Event Analyzer
    participant Pred as Prediction Service
    participant Cap as Capacity Service
    participant Shed as Load Shedding Service
    participant Log as Logger Service
    database DB as MySQL DB
    database Cache as Redis Cache

    Admin->>Alloc: POST /decide (situation details)
    
    rect rgb(240, 248, 255)
        note right of Alloc: Phase 1: Event Signature Analysis (DTW)
        Alloc->>Analyzer: POST /analyze (severity + recent history)
        Analyzer->>DB: Query historical signatures
        DB-->>Analyzer: Return event templates
        note over Analyzer: Perform DTW alignment & compute boost
        Analyzer-->>Alloc: Return risk score + event risks + matches
    end

    rect rgb(245, 245, 220)
        note right of Alloc: Phase 2: Workload Forecasting
        Alloc->>Pred: POST /predict (features + computed risk score)
        note over Pred: Load Random Forest classifier
        Pred-->>Alloc: Return workload prediction (LOW/MEDIUM/HIGH)
    end

    rect rgb(240, 255, 240)
        note right of Alloc: Phase 3: Capacity Locking
        Alloc->>Cap: GET /capacity/{hospital_id}
        Cap-->>Alloc: Return current capacity
        Alloc->>Cap: POST /capacity/reserve (ETA & initial confidence)
        note over Cap: Create temporal decay lock in database
        Cap-->>Alloc: Return reservation status
    end

    rect rgb(255, 240, 245)
        note right of Alloc: Phase 4: Load-Shedding Negotiation
        alt High Load & Low Capacity (<20% free beds)
            Alloc->>Shed: POST /shedding/negotiate
            Shed->>Cap: GET capacities for candidate hospitals
            Cap-->>Shed: Return capacities
            note over Shed: Rank hospitals by free capacity & load
            Shed->>Cache: Save pre-authorization token (TTL 300s)
            Shed-->>Alloc: Return transfer recommendation + token
        end
    end

    Alloc->>Log: POST /log (audit details)
    Log->>DB: Insert into decision_logs
    Alloc-->>Admin: Return complete resolution payload
```

---

## 🛠️ Tech Stack

| Service | Primary Technology | Responsibility | Port |
| :--- | :--- | :--- | :--- |
| **Dashboard** | Nginx, HTML5, Tailwind CSS, Leaflet, Chart.js | UI Portal & Nginx reverse proxy | `8080` |
| **Allocation** | FastAPI, Requests | System coordinator & core orchestration engine | `8003` |
| **Capacity** | FastAPI, SQLAlchemy, MySQL, APScheduler | Tracks resources, handles reservation locks & confidence decay | `8005` |
| **Event Analyzer**| FastAPI, SQLAlchemy, dtaidistance, MySQL | Normalizes risk series & matches patterns using DTW | `8007` |
| **Prediction** | FastAPI, scikit-learn, joblib, pandas | Classifies incoming demand surges via Random Forest | `8002` |
| **Load Shedding** | FastAPI, Redis client, Requests | Evaluates transfer targets & issues stateful TTL tokens | `8006` |
| **Logger** | FastAPI, SQLAlchemy, MySQL | Audit log recorder | `8004` |
| **External Event**| FastAPI | Mock integrator simulating weather and community risks | (internal) |
| **Database** | MySQL 8.0 | Stores capacities, fingerprints, and audit logs | `3307` |
| **Cache** | Redis 7 | Stores temporary transfer pre-auth tokens | `6379` |

---

## 📁 Directory Structure

```text
hospital-microservices/
├── alert_service/             # Triage alert notifier microservice (FastAPI)
├── allocation_service/        # Master orchestrator coordinator (FastAPI)
├── capacity_service/          # Resource capacity manager (FastAPI/SQLAlchemy)
│   └── tests/                 # Unit tests for capacity confidence decay
├── dashboard/                 # Frontend Leaflet/Chart.js code & Nginx proxy
├── data_service/              # Admissions ingest manager (FastAPI)
├── db/                        # Initialization SQL scripts for MySQL
├── event-analyzer-service/    # DTW signature library & analyzer (FastAPI)
├── event_service/             # Simulated external risk stream feed (FastAPI)
├── load_shedding_service/     # Redis-backed transfer negotiation (FastAPI)
├── logger_service/            # MySQL audit log recorder (FastAPI)
├── prediction_service/        # Random Forest classifier service (FastAPI)
├── simulation/                # Telemetry generation script
├── docker-compose.yml         # Container orchestration manifest
├── .env.example               # Configurable environment template
└── README.md                  # This file
```

---

## 🧠 Machine Learning Model Information

### Random Forest Classifier
- **Features Used:**
  1. `hour` (0–23): Capture diurnal ED patterns.
  2. `day_of_week` (0–6): Capture weekly cycles (weekends vs. weekdays).
  3. `ambulance_cases`: Real-time incoming vehicle count.
  4. `walkin_cases`: Real-time triage queue count.
  5. `weather_risk` (0/1): Binary indicator of hazardous road/weather conditions.
  6. `event_risk` (0/1): Local large-scale public events.
  7. `outbreak_risk` (0/1): Public health disease outbreak reports.
  8. `risk_score` (0.0–1.0): Dynamic score generated by the Event Analyzer.
- **Labels:** `LOW` | `MEDIUM` | `HIGH` (representing overall emergency workload).
- **Training Strategy:** The model is trained dynamically at Docker image build-time (`prediction_service/Dockerfile`) using a synthetic data script (`train_model.py`) that models high-dimensional risk combinations, ensuring scikit-learn models are pre-compiled and ready on container boot.

---

## 🚀 Installation & Running

### Prerequisites
- [Docker](https://www.docker.com/) and [Docker Compose](https://docs.docker.com/compose/) installed.
- Ports `8080`, `8002`, `8003`, `8004`, `8005`, `8006`, `8007`, `3307`, and `6379` free on your host machine.

### Spin up the Stack
1. Clone this repository:
   ```bash
   git clone https://github.com/a22bhartia/intelligent-hospital-resource-orchestrator.git
   cd intelligent-hospital-resource-orchestrator
   ```
2. Copy environment configuration:
   ```bash
   cp .env.example .env
   ```
3. Build and launch containers:
   ```bash
   docker compose up --build -d
   ```
4. Access the dashboard at [http://localhost:8080](http://localhost:8080).

### Generating Test Telemetry
Run the simulation script to trigger simulated patient admissions, incidents, and capacity decisions across all hospitals. This populates charts and logs in real-time:
```bash
# Set up virtual environment
python -m venv venv
venv\Scripts\activate
pip install requests

# Run the simulation (calls allocation endpoint 120 times)
python simulation/simulate.py
```

---

## ⚙️ Configuration & Environment Variables

Create a local `.env` file at the root directory to customize operational thresholds:

```ini
# Decay constant for locks (higher = locks release faster)
DECAY_LAMBDA=0.05
# Expiry threshold (if confidence drops below this, resource is freed)
DECAY_RELEASE_THRESHOLD=0.25

# DTW distance matching threshold for event fingerprints
DTW_THRESHOLD=2.5
# Maximum pattern boost applied to risk scores on a DTW match
MAX_PATTERN_BOOST=0.2

# Pre-authorization token TTL (seconds)
TOKEN_TTL_SECONDS=300
```

---

## 📊 API Documentation & Swagger UIs

Once the stack is running, you can access individual microservice interactive OpenAPI documentation (Swagger) at:
- **Allocation Service:** [http://localhost:8003/docs](http://localhost:8003/docs)
- **Prediction Service:** [http://localhost:8002/docs](http://localhost:8002/docs)
- **Capacity Service:** [http://localhost:8005/docs](http://localhost:8005/docs)
- **Event Analyzer:** [http://localhost:8007/docs](http://localhost:8007/docs)
- **Load Shedding Service:** [http://localhost:8006/docs](http://localhost:8006/docs)
- **Logger Service:** [http://localhost:8004/docs](http://localhost:8004/docs)

---

## 📈 Future Improvements

- **Real-Time Websockets Integration:** Replace HTTP polling on the dashboard with Websockets for push telemetry.
- **Advanced Forecasting Models:** Transition from Random Forest to a recurrent architecture (LSTM / GRU) or a LightGBM classifier to evaluate time-series trends.
- **Enhanced Spatial Optimization:** Integrate OR-Tools to solve vehicle routing optimization for ambulances undergoing redirect.

---

## 📄 License

Distributed under the MIT License. See `LICENSE` for details.

---

## 👤 Author

**Aayush Bhartia**
- GitHub: [@a22bhartia](https://github.com/a22bhartia)
