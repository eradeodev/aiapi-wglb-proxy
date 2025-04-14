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

# Set the working directory
WORKDIR /app

# Copy everything from the current directory into the container
COPY create_new_peer.sh /app
COPY initial_config.conf /app
COPY startup.sh /app
COPY check_host.sh /app
COPY chunker_server /app/chunker_server

# Install requirements for chunker_server
RUN conda run -n py39 pip install -r /app/chunker_server/requirements.txt

# Default command
ENTRYPOINT ["/app/startup.sh"]
