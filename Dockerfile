FROM python:3.10-slim

WORKDIR /app

# Install git (required by GitPython)
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Use Gunicorn to serve the Flask app
# Workers: 1 (limit concurrency per instance if desired, or increase)
# Threads: 8 (handle multiple IO-bound requests)
# Timeout: 0 (allow long running requests if needed, though we use threads)
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 main:app
