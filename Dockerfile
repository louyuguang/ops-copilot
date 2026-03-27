FROM python:3.11-slim

WORKDIR /app

COPY . /app

ENV PYTHONPATH=/app/src

CMD ["python", "src/main.py"]
