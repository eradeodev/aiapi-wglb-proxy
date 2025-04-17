#!/bin/bash

echo "@ prune_old_peers.sh"

# Define file for tracking inactive peers
cd /app || exit 1 # Exit if we can't change directory
INACTIVE_PEERS_FILE="/app/logs/wg_inactive_peers.txt"
PEERS_DIR="/app/peers"
MINS_TO_KEEP=5 # Peers inactive for less than this many minutes are kept
CURRENT_EPOCH=$(date +%s)

# Ensure WireGuard interface is up
if ! ip link show hub-wg &>/dev/null; then
    echo "WireGuard interface hub-wg is down. Cannot prune peers."
    exit 1 # Exit if wg interface isn't running
fi

# Get list of peers (public keys) without a recent handshake from wg show
# Using wg show directly is more reliable than parsing multi-line output
INACTIVE_PEERS=()
while read -r PEER_KEY; do
    HANDSHAKE_TIME=$(wg show hub-wg latest-handshakes | grep "$PEER_KEY" | awk '{print $2}')
    # Ignore default peer
    if [ "$PEER_KEY" = "$DEFAULT_PEER_PUB_KEY" ]; then
        echo "Ignoring $PEER_KEY as it matches default peer key..."
        continue
    fi
    # Add peer if handshake time is 0 or older than 10 minutes
    if [ "$HANDSHAKE_TIME" = "0" ] || [ $((CURRENT_EPOCH - HANDSHAKE_TIME)) -gt 600 ]; then
        INACTIVE_PEERS+=("$PEER_KEY")
    fi
done < <(wg show hub-wg peers)

echo "Current inactive peers (Handshake=0): ${INACTIVE_PEERS[*]}"

# Ensure log file exists
mkdir -p $(dirname "$INACTIVE_PEERS_FILE")
touch "$INACTIVE_PEERS_FILE"

# Add newly inactive peers with the current timestamp if not already logged
for PEER_KEY in "${INACTIVE_PEERS[@]}"; do
    # Use grep -q -F to treat PEER_KEY as fixed string, preventing regex issues
    ALLOWED_IPS=$(wg show hub-wg allowed-ips | grep "$PEER_KEY" | awk '{print $2}')
    if ! grep -q -F "$PEER_KEY" "$INACTIVE_PEERS_FILE"; then
        echo "Logging new inactive peer: $PEER_KEY"
        echo "$PEER_KEY $ALLOWED_IPS $CURRENT_EPOCH" >> "$INACTIVE_PEERS_FILE"
    fi
done

# Remove peers from the log file that have become active again
TMP_ACTIVE_CHECK=$(mktemp)
while IFS= read -r LINE; do
    LOGGED_PEER_KEY=$(echo "$LINE" | awk '{print $1}')
    IS_STILL_INACTIVE=0
    for INACTIVE_PEER in "${INACTIVE_PEERS[@]}"; do
        if [ "$LOGGED_PEER_KEY" = "$INACTIVE_PEER" ]; then
            IS_STILL_INACTIVE=1
            break
        fi
    done

    if [ "$IS_STILL_INACTIVE" -eq 1 ]; then
        echo "$LINE" >> "$TMP_ACTIVE_CHECK" # Keep inactive peer in log
    else
        echo "Peer $LOGGED_PEER_KEY is active again, removing from inactive log."
    fi
done < "$INACTIVE_PEERS_FILE"
cat "$TMP_ACTIVE_CHECK" > "$INACTIVE_PEERS_FILE"
rm "$TMP_ACTIVE_CHECK"


# Process the log file to remove peers inactive for too long
TMP_PRUNE_LOG=$(mktemp)
PEERS_REMOVED=0
while IFS= read -r LINE; do
    PEER_KEY=$(echo "$LINE" | awk '{print $1}')
    PEER_ALLOWED_IPS=$(echo "$LINE" | awk '{print $2}')
    FIRST_SEEN_INACTIVE=$(echo "$LINE" | awk '{print $3}')
    AGE_SECONDS=$(( CURRENT_EPOCH - FIRST_SEEN_INACTIVE ))
    AGE_MINUTES=$(( AGE_SECONDS / 60 ))

    echo "Checking peer: $PEER_KEY, inactive for $AGE_MINUTES minutes."

    if [ "$AGE_MINUTES" -lt "$MINS_TO_KEEP" ]; then
        # Keep the peer in the log file
        echo "$LINE" >> "$TMP_PRUNE_LOG"
    else
        echo "Peer $PEER_KEY inactive for $AGE_MINUTES minutes (>= $MINS_TO_KEEP). Attempting removal."
        PEER_FILE_FOUND=0
        # Find the corresponding peer config file
        for FILE in "$PEERS_DIR"/*; do
            # Use grep -q -F for exact string matching of the public key
            if [ -f "$FILE" ] && grep -q -F "$PEER_KEY" "$FILE"; then
                echo "Found config file: $FILE for peer $PEER_KEY."
                # --- Remove Peer Dynamically ---
                echo "Removing peer $PEER_KEY from hub-wg interface..."
                if wg set hub-wg peer "$PEER_KEY" remove; then
                    ip -4 route delete "$PEER_ALLOWED_IPS" dev hub-wg
                    echo "Successfully removed peer $PEER_KEY from WireGuard interface."
                    echo "Deleting peer config file: $FILE"
                    rm -f "$FILE"
                    PEERS_REMOVED=1 # Mark that we need to rebuild proxy config
                else
                    echo "FAILED: wg set remove command failed for peer $PEER_KEY. Keeping config file $FILE for now."
                    # Keep the peer in the log if removal failed, so we retry next time
                    echo "$LINE" >> "$TMP_PRUNE_LOG"
                fi
                # --- End Dynamic Remove ---
                PEER_FILE_FOUND=1
                break # Stop searching once file is found and processed
            fi
        done

        if [ "$PEER_FILE_FOUND" -eq 0 ]; then
             echo "WARNING: Could not find peer config file for inactive peer $PEER_KEY listed in log. Removing from log."
             # It's already gone or was never fully added, just remove the log entry.
        fi
    fi
done < "$INACTIVE_PEERS_FILE"

# Update the inactive peers log file
cat "$TMP_PRUNE_LOG" > "$INACTIVE_PEERS_FILE"
rm "$TMP_PRUNE_LOG"

# If any peers were successfully removed, rebuild the proxy config
if [ "$PEERS_REMOVED" -eq 1 ]; then
    echo "Peers were removed. Rebuilding proxy config..."
    ./build_proxy_conf.sh
fi

echo "Peer pruning process finished."
