FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

# Train model at build time (creates model.joblib)
RUN python app/train_model.py && mv model.joblib app/model.joblib

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
