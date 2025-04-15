FROM ollama/ollama:latest

ENV TZ=America/Los_Angeles
ENV DEBIAN_FRONTEND=noninteractive

# Install dependencies
RUN apt-get update && apt-get upgrade -y && \
    apt-get install -y wireguard-tools iproute2 netcat-openbsd iputils-ping wget && \
    rm -rf /var/lib/apt/lists/*

# Install Miniconda

# Set the Miniconda install directory
ENV MINICONDA_PATH /root/miniconda3

# Download and install Miniconda
RUN mkdir -p $MINICONDA_PATH && \
    wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O $MINICONDA_PATH/miniconda.sh && \
    bash $MINICONDA_PATH/miniconda.sh -b -u -p $MINICONDA_PATH && \
    rm $MINICONDA_PATH/miniconda.sh

# Add Conda to PATH
ENV PATH $MINICONDA_PATH/bin:$PATH

# Initialize Conda, create the environment, and activate it in one step
RUN /root/miniconda3/bin/conda init bash && \
    conda create -n py39 python=3.9 -y && \
    echo "conda activate py39" >> /root/.bashrc

# Copy chunker_server requirements and install them
COPY chunker_server/requirements.txt /app/chunker_server/
RUN conda run -n py39 pip install -r /app/chunker_server/requirements.txt

# Set the working directory
WORKDIR /app

# Copy the peer setup and host check scripts
COPY initial_config.conf create_new_peer.sh check_host.sh /app/

# Copy chunker_server app.py
COPY chunker_server/app.py /app/chunker_server/

# Copy startup script for entrypoint
COPY startup.sh /app
ENTRYPOINT ["/app/startup.sh"]
