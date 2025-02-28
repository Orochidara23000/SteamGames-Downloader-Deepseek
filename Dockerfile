FROM python:3.10-slim

# Install system dependencies for SteamCMD
RUN apt-get update && apt-get install -y \
    wget \
    ca-certificates \
    lib32gcc-s1 \
    && rm -rf /var/lib/apt/lists/*

# Install SteamCMD
WORKDIR /steamcmd
RUN wget -qO- https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz | tar zxvf - \
    && ./steamcmd.sh +quit

# Set up application
WORKDIR /app
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Create persistent volume mount point
RUN mkdir -p /app/downloads

# Use Railway's port
ENV PORT=7860
EXPOSE $PORT

CMD ["python", "app.py"]
