FROM ollama/ollama:latest

# Install WireGuard tools
RUN apt-get update && \
    apt-get install -y wireguard-tools iproute2 netcat-openbsd iputils-ping python3 python3-pip && \
    rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /app

# Copy everything from the current directory into the container
COPY create_new_peer.sh /app
COPY initial_config.conf /app
COPY startup.sh /app
COPY check_host.sh /app

COPY chunker_server /app

# Install Python dependencies for chunker
RUN pip3 install -r chunker_server/requirements.txt

# Default command
ENTRYPOINT ["/app/startup.sh"]
