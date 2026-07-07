# New Novelty-Focused Features

This document summarizes the three new features added to the hospital orchestration system, why they matter from a patent/novelty perspective, which endpoints were added, and how to test them in the current codebase.

## 1. Temporal Confidence Decay Lock

This feature turns resource reservations into time-aware confidence locks instead of permanent decrements. Each reservation now has an initial confidence, an ETA, a decay constant, and a current confidence score that drops over time. When the score falls below the configured threshold, the lock is released automatically and capacity is returned.

Why this helps novelty:
- It adds a real technical locking mechanism tied to time and ambulance progression, rather than a static reservation.
- It couples prediction confidence to physical inventory release logic.

Implemented in:
- `capacity_service/app/main.py`
- `capacity_service/tests/test_decay.py`

Main endpoints:
- `GET /capacity/locks/status/{hospital_id}`
- `POST /capacity/ambulance-update`
- `POST /capacity/{hospital_id}/reserve`
- `GET /health`

How to test:
1. Start the stack with `docker compose up --build`.
2. Reserve capacity:
   ```bash
   curl -X POST http://localhost:8005/capacity/H01/reserve \
     -H "Content-Type: application/json" \
     -d "{\"ambulance_id\":\"AMB-001\",\"beds\":2,\"staff\":1,\"ventilators\":0,\"eta_minutes\":10}"
   ```
3. Check lock state:
   ```bash
   curl http://localhost:8005/capacity/locks/status/H01
   ```
4. Update or cancel the ambulance:
   ```bash
   curl -X POST http://localhost:8005/capacity/ambulance-update \
     -H "Content-Type: application/json" \
     -d "{\"ambulance_id\":\"AMB-001\",\"new_eta_minutes\":2,\"is_cancelled\":false}"
   ```
5. Run decay tests from `capacity_service`:
   ```bash
   pytest tests/test_decay.py
   ```

## 2. Cross-Hospital Load Shedding Negotiation

This feature adds a dedicated microservice that recommends a target hospital when the source hospital is under high load and low free-bed ratio. The service computes a transfer score, issues a short-lived pre-authorization token in Redis, and supports transfer confirmation.

Why this helps novelty:
- It creates an automated negotiation layer between hospitals instead of a passive alert.
- It introduces tokenized pre-authorization for physical capacity transfer decisions.

Implemented in:
- `load_shedding_service/app/main.py`
- `allocation_service/app/main.py`
- `capacity_service/app/main.py`
- `dashboard/index.html`

Main endpoints:
- `POST /shedding/negotiate`
- `GET /shedding/validate/{token}`
- `POST /shedding/confirm-transfer`
- `GET /health`

How to test:
1. Force a high-load scenario from the dashboard or `POST /decide`.
2. If the source hospital has fewer than 20% free beds, allocation returns `transfer_recommendation`.
3. Confirm the transfer:
   ```bash
   curl -X POST http://localhost:8006/shedding/confirm-transfer \
     -H "Content-Type: application/json" \
     -d "{\"token\":\"<token-from-negotiate>\"}"
   ```
4. Check the dashboard banner for the transfer recommendation and confirmation state.

## 3. Causal Event Fingerprint Library

This feature extends the event analyzer to persist normalized temporal signatures and match incoming event shapes against historical ones using DTW (Dynamic Time Warping). Matching historical patterns can boost the current risk score.

Why this helps novelty:
- It moves beyond event labels into event-shape recognition.
- It introduces pattern similarity as a real-time input to autonomous resource orchestration.

Implemented in:
- `event-analyzer-service/app/main.py`
- `dashboard/index.html`

Main endpoints:
- `POST /fingerprints/record`
- `POST /fingerprints/match`
- `GET /fingerprints/library`
- `POST /analyze`
- `GET /health`

How to test:
1. Record a fingerprint:
   ```bash
   curl -X POST http://localhost:8007/fingerprints/record \
     -H "Content-Type: application/json" \
     -d "{\"event_type\":\"flood\",\"temporal_signature\":[0.1,0.4,0.8,1.0,0.6],\"source_date\":\"2026-04-10\",\"hospital_id\":\"H01\"}"
   ```
2. Match a new signature:
   ```bash
   curl -X POST http://localhost:8007/fingerprints/match \
     -H "Content-Type: application/json" \
     -d "{\"event_type\":\"flood\",\"temporal_signature\":[0.1,0.35,0.75,0.95,0.55]}"
   ```
3. Run a new decision from the dashboard and inspect the fingerprint overlay chart under Live Analytics.

## Docker and Routing Updates

The Compose stack now includes:
- `redis`
- `load-shedding-service`

The dashboard container now includes an Nginx config that proxies:
- `/api/allocation/`
- `/api/logger/`
- `/api/capacity/`
- `/api/load-shedding/`
- `/api/event-analyzer/`

## Notes

- Database model additions were implemented through SQLAlchemy models in the services, not by extending `db/init.sql`.
- The current repository still retains its original lightweight prediction model and static dashboard architecture; these features were added on top of that existing structure rather than by rewriting the project from scratch.
