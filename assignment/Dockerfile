FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agents ./agents
ENV STATE_DB=/data/state.sqlite3 MEDIA_DIR=/media
EXPOSE 8000
CMD ["uvicorn", "agents.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
