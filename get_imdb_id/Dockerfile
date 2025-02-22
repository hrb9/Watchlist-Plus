FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY config.py .
COPY tmdb_services.py .


EXPOSE 5331

CMD ["python", "app.py"]