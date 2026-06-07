# Stage 1: Build the Astro frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
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

# Expose the Flask port
EXPOSE 5000

# Start Flask with gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app"]
