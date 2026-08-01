# Stage 1: Build the Astro frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build

# Stage 2: Build the Python backend
FROM python:3.11-slim
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy built frontend from Stage 1
COPY --from=frontend-builder /app/dist /app/dist

# Copy backend files
COPY . .

# Expose default port
EXPOSE 10000

# Start Flask with gunicorn bound to $PORT
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-10000} --workers 2 --threads 4 app:app"]
