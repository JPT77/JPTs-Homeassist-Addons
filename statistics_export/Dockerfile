FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir pandas pyarrow

COPY export_statistics.py .

CMD ["python3", "/app/export_statistics.py"]
