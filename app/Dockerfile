FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV APP_DATA_DIR=/app/cache
ENV APP_UPLOADS_DIR=/app/uploads
ENV STREAMLIT_SERVER_PORT=8501
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0
ENV STREAMLIT_SERVER_HEADLESS=true
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
ENV STREAMLIT_SERVER_COOKIE_SECRET=artdb-secret-change-me
ENV STREAMLIT_SERVER_ENABLE_CORS=false

EXPOSE 8501

CMD ["sh", "-c", "mkdir -p /app/cache /app/uploads && retries=${DB_INIT_RETRIES:-30}; count=0; until python scripts/init_db.py; do count=$((count + 1)); if [ \"$count\" -ge \"$retries\" ]; then echo 'Database initialization failed.' >&2; exit 1; fi; sleep 2; done; exec streamlit run app.py --server.port=${STREAMLIT_SERVER_PORT:-8501} --server.address=${STREAMLIT_SERVER_ADDRESS:-0.0.0.0} --server.headless=${STREAMLIT_SERVER_HEADLESS:-true} --browser.gatherUsageStats=${STREAMLIT_BROWSER_GATHER_USAGE_STATS:-false} --server.enableCORS=${STREAMLIT_SERVER_ENABLE_CORS:-false}"]
