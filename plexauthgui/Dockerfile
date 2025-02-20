# PlexAuthGUI/Dockerfile
FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY plexauthgui/ .

EXPOSE 5332

CMD ["python", "app.py"]
