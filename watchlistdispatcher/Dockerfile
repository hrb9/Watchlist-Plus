# watchlistdispatcher/Dockerfile
FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 6000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "6000"]
