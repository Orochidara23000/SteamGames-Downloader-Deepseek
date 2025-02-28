FROM python:3.9-slim

WORKDIR /app

# Install system dependencies (curl, tar, wget for steamcmd installation)
RUN apt-get update && apt-get install -y \
    curl \
    tar \
    wget \
    lib32gcc-s1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create directories
RUN mkdir -p /app/downloads /app/logs /app/steamcmd

# Copy application files
COPY requirements.txt .
COPY app.py .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose the port
EXPOSE 7860

# Set environment variables
ENV PORT=7860

# Command to run the application
CMD ["python", "app.py"]