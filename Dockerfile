# Use official Python slim image
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install dependencies first (layer cache)
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY backend/ ./backend/

# Copy frontend
COPY frontend/ ./frontend/

# Set working directory to backend (where main.py lives)
WORKDIR /app/backend

# Expose port
EXPOSE 8000

# Start server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
