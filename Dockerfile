FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 RENDER=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", "--worker-class", "gthread", "--timeout", "180", "app:app"]
