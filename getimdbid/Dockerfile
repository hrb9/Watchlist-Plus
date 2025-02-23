FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY getimdbid/ .


EXPOSE 5331

CMD ["python", "app.py"]