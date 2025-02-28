FROM python:3.9-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    tar \
    wget \
    lib32gcc-s1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create directories for downloads, logs, and SteamCMD
RUN mkdir -p /app/downloads /app/logs /app/steamcmd

# Copy application files
COPY requirements.txt .
COPY app.py .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose the Gradio port
EXPOSE 7860

# Pre-install SteamCMD to avoid startup delays
RUN mkdir -p /app/steamcmd && \
    cd /app/steamcmd && \
    wget -q https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz && \
    tar -xzf steamcmd_linux.tar.gz && \
    rm steamcmd_linux.tar.gz && \
    chmod +x steamcmd.sh && \
    ./steamcmd.sh +quit

# Command to run the application
CMD ["python", "app.py"]