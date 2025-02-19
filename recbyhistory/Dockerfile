# recbyhistory/Dockerfile
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Copy requirements and install them
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY recbyhistory/ .

# Expose port (default 5335)
EXPOSE 5335

# Command to run the application
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5335"]
