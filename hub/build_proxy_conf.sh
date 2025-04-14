#!/bin/bash
# Use the wireguard configs to build up the proxy config

#!/bin/bash

cd /app
PEERS_DIR="/app/peers"
OUTPUT_FILE="proxy_config.ini"
PORT=11434

echo -n "" > "$OUTPUT_FILE"

count=0
for file in "$PEERS_DIR"/*; do
    if [[ -f "$file" ]]; then
        IP=$(grep -oP 'AllowedIPs = \K[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' "$file")
        ENABLED=$(grep -oP 'EnabledForRequests = \K.*' "$file")
        UUID_SERVER_NAME=$(grep -oP 'ServerName = \K.*' "$file")
        if [[ -n "$IP" ]]; then
            SERVER_NAME="${UUID_SERVER_NAME}_$count"
            echo "[$SERVER_NAME]" >> "$OUTPUT_FILE"
            echo "url = http://$IP:$PORT" >> "$OUTPUT_FILE"
            echo "enabled_for_requests = $ENABLED" >> "$OUTPUT_FILE"
            echo "" >> "$OUTPUT_FILE"
            ((count++))
        fi
    fi
done

echo "Configuration written to $OUTPUT_FILE"

