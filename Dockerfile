FROM python:3.12-slim

LABEL org.opencontainers.image.source=https://github.com/javikobcn/music-search

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]