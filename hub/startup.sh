#!/bin/bash
# Startup script for Hub node

# Set all environment variables
if [ -z "$HUB_PRIV_KEY" ]; then
    echo "Hub Private Key not set"
    exit 1
fi
if [ -z "$DEFAULT_PEER_PUB_KEY" ]; then
    echo "Default Peer Public Key not set"
    exit 1
fi

# Modify file
FILE="base_config.txt"
sed -i "s|#HUBPRIVKEY#|$HUB_PRIV_KEY|g" "$FILE"
sed -i "s|#DEFAULTPEERPUBKEY#|$DEFAULT_PEER_PUB_KEY|g" "$FILE"

# Start up wireguard
echo "Starting wireguard"
./rebuild_and_start_wg.sh

# Accept incoming connections
echo "Accepting new peers"
./accept_new_peers.sh &

# Ensure prune script is executable
chmod +x /app/prune_old_peers.sh

# Add peer pruning cron job
echo "* * * * * /app/prune_old_peers.sh >> /proc/1/fd/1 2>> /proc/1/fd/2" > /etc/cron.d/peer_prune
chmod 0644 /etc/cron.d/peer_prune
crontab /etc/cron.d/peer_prune

# Start cron
echo "Starting cron"
cron


# Start load balancer in a loop; if this script does exit, Docker should restart it due to restart=always:
while true; do
    echo "Starting load balancer"
    ./build_proxy_conf.sh
    python3 ./main.py --config ./proxy_config.ini --users_list ./authorized_users.txt --port 11434
done
