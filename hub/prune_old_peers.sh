#!/bin/bash

# Define file for tracking inactive peers
cd /app
INACTIVE_PEERS_FILE="/app/logs/wg_inactive_peers.txt"
PEERS_DIR="/app/peers"
MINS_TO_KEEP=5
CURRENT_EPOCH=$(date +%s)

# Get list of peers without a handshake
INACTIVE_PEERS=($(wg show hub-wg peers | while read -r PEER; do
    HANDSHAKE=$(wg show hub-wg latest-handshakes | grep "$PEER" | awk '{print $2}')
    if [ "$HANDSHAKE" = "0" ]; then
        echo "$PEER"
    fi
done))
echo "$INACTIVE_PEERS"

# Ensure log file exists
if [ ! -f "$INACTIVE_PEERS_FILE" ]; then
    touch "$INACTIVE_PEERS_FILE"
fi

# Add new inactive peers with the current date if not already logged
for PEER in "${INACTIVE_PEERS[@]}"; do
    if ! grep -q "$PEER" "$INACTIVE_PEERS_FILE"; then
        echo "$PEER $CURRENT_EPOCH" >> "$INACTIVE_PEERS_FILE"
    fi
done

# Remove peers that have reconnected
while IFS= read -r LINE; do
    PEER=$(echo "$LINE" | awk '{print $1}')
    if [[ ! " ${INACTIVE_PEERS[@]} " =~ " $PEER " ]]; then
        sed -i "/$PEER/d" "$INACTIVE_PEERS_FILE"
    fi
done < "$INACTIVE_PEERS_FILE"

# Delete peers inactive for more than 7 days
TMP_FILE=$(mktemp)
PEERS_REMOVED=0
while IFS= read -r LINE; do
    PEER=$(echo "$LINE" | awk '{print $1}')
    FIRST_SEEN=$(echo "$LINE" | awk '{print $2}')
    AGE=$(( ( $CURRENT_EPOCH - $FIRST_SEEN ) / 60 )) # 60 seconds per minutes
    echo "$PEER inactive for $AGE minutes"
    if [ "$AGE" -lt "$MINS_TO_KEEP" ]; then
        echo "$LINE" >> "$TMP_FILE"
    else
        echo "Searching for peer file containing public key: $PEER"
        for FILE in $PEERS_DIR/*; do
            if grep -q "$PEER" "$FILE"; then
                echo "Deleting file: $FILE (inactive for $AGE minutes)"
                rm -f "$FILE"
                PEERS_REMOVED=1
                break
            fi
        done
    fi
done < "$INACTIVE_PEERS_FILE"

# If There were peers removed, then rebuild wireguard and the proxy config
if [[ "$PEERS_REMOVED" == "1" ]]; then
    ./rebuild_and_start_wg.sh
    ./build_proxy_conf.sh
fi

cat "$TMP_FILE" > "$INACTIVE_PEERS_FILE"
