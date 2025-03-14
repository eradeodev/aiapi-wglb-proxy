#!/bin/bash

# Define file for tracking inactive peers
INACTIVE_PEERS_FILE="./wg_inactive_peers.log"
DAYS_TO_KEEP=7
CURRENT_DATE=$(date +%Y-%m-%d)

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
        echo "$PEER $CURRENT_DATE" >> "$INACTIVE_PEERS_FILE"
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
while IFS= read -r LINE; do
    PEER=$(echo "$LINE" | awk '{print $1}')
    FIRST_SEEN=$(echo "$LINE" | awk '{print $2}')
    AGE=$(( ( $(date -d "$CURRENT_DATE" +%s) - $(date -d "$FIRST_SEEN" +%s) ) / 86400 ))
    
    if [ "$AGE" -lt "$DAYS_TO_KEEP" ]; then
        echo "$LINE" >> "$TMP_FILE"
    else
        echo "Searching for peer file containing public key: $PEER"
        for FILE in ./peers/*; do
            if grep -q "$PEER" "$FILE"; then
                echo "Deleting file: $FILE (inactive for $AGE days)"
                rm -f "$FILE"
                break
            fi
        done
    fi
done < "$INACTIVE_PEERS_FILE"

mv "$TMP_FILE" "$INACTIVE_PEERS_FILE"
