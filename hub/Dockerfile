FROM python:3.11.11-slim-bookworm

# Install WireGuard tools
RUN apt-get update && \
    apt-get install -y wireguard-tools iproute2 netcat-openbsd iputils-ping curl git apache2 cron logrotate && \
    rm -rf /var/lib/apt/lists/* 

# Set the working directory
WORKDIR /app

# Set up proxy server
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
COPY main.py .
COPY ascii_colors.py .
COPY config_manager.py .
COPY logger.py .
COPY proxy_handler.py .

# Copy custom logrotate config
COPY access_log_rotate.conf /etc/logrotate.d/access_log

# Set up cron job to run every hour
RUN echo "0 * * * * /usr/sbin/logrotate -f /etc/logrotate.d/access_log" > /etc/cron.d/logrotate_hourly && chmod 0644 /etc/cron.d/logrotate_hourly && crontab /etc/cron.d/logrotate_hourly

# Copy everything from the current directory into the container
COPY accept_new_peers.sh /app
COPY build_wg_config.sh /app
COPY find_next_ip.sh /app
COPY base_config.txt /app
COPY rebuild_and_start_wg.sh /app
COPY startup.sh /app
COPY build_proxy_conf.sh /app
COPY authorized_users.txt /app
COPY prune_old_peers.sh /app


# Configure bash history
RUN { \
    grep -qxF 'export HISTFILESIZE=2000' /etc/bashrc || echo 'export HISTFILESIZE=2000' >> /etc/bashrc; \
    grep -qxF 'export HISTSIZE=1000' /etc/bashrc || echo 'export HISTSIZE=1000' >> /etc/bashrc; \
    grep -qxF 'export HISTFILE=~/.bash_history' /etc/bashrc || echo 'export HISTFILE=~/.bash_history' >> /etc/bashrc; \
    grep -qxF 'PROMPT_COMMAND="history -a; $PROMPT_COMMAND"' /etc/bashrc || echo 'PROMPT_COMMAND="history -a; $PROMPT_COMMAND"' >> /etc/bashrc; \
}

# Default command
WORKDIR /app
ENTRYPOINT ["/app/startup.sh"]
