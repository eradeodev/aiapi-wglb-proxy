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

# Start cron service
service cron start

# Register peer pruner in cron job
SCRIPT_PATH="/app/prune_old_peers.sh"
CRON_JOB="* * * * * $SCRIPT_PATH >> /proc/1/fd/1 2>> /proc/1/fd/2"

# Ensure the script is executable
chmod +x "$SCRIPT_PATH"

# Check if the cron job already exists
if (crontab -l 2>/dev/null | grep -F "$SCRIPT_PATH") ; then
    echo "Cron job already exists."
else
    (crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -
    echo "Cron job added: $CRON_JOB"
fi


# Start load balancer in a loop; if this script does exit, Docker should restart it due to restart=always:
while true; do
    echo "Starting load balancer"
    ./build_proxy_conf.sh
    python3 ./main.py --config ./proxy_config.ini --users_list ./authorized_users.txt --port 11434
done
