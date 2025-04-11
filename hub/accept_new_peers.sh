#!/bin/bash
# Accept incoming connections from default peer to create new peers

# Ensure peers directory exists
mkdir -p /app/peers

# Ensure WireGuard interface is up (might be needed if script restarts independently)
if ! ip link show hub-wg &>/dev/null; then
    echo "WireGuard interface hub-wg is down. Attempting to start..."
    # Attempt a safe start using the existing config if possible
    if [ -f /app/hub-wg.conf ]; then
        wg-quick up /app/hub-wg.conf || { echo "ERROR: Failed to bring up hub-wg interface."; exit 1; }
    else
        echo "ERROR: hub-wg.conf not found. Cannot start interface."
        exit 1
    fi
fi


while true; do
    # Retrieve the next IP address
    NEXT_IP=$(./find_next_ip.sh)
    echo "Offering IP $NEXT_IP to next connecting peer..."
    # Send IP (strip potential netmask if find_next_ip adds it)
    echo "${NEXT_IP%%/*}" | nc -l -p 1234 -q 1
    echo "Sent IP address ${NEXT_IP%%/*} to client"

    # Calculate next peer number based on the offered IP
    IFS='.' read -r a b c d <<< "${NEXT_IP%%/*}"
    # Handle potential errors if d is not a number or less than 3
    if [[ "$d" =~ ^[0-9]+$ && "$d" -ge 3 ]]; then
        NEXT_PEER_NUMBER=$(($d - 2))
    else
        echo "ERROR: Could not determine peer number from IP $NEXT_IP. Skipping."
        continue # Skip to the next iteration
    fi

    PEER_CONFIG_FILE="peers/${NEXT_PEER_NUMBER}"

    # Listen for client peer config with a timeout
    echo "Waiting for peer config on port 1235 for peer ${NEXT_PEER_NUMBER}..."
    if timeout 10 nc -l -p 1235 > "$PEER_CONFIG_FILE"; then
        # Successfully retrieved peer config
        echo "Retrieved client peer config for peer ${NEXT_PEER_NUMBER}"

        # --- Add Peer Dynamically ---
        # Parse PublicKey and AllowedIPs from the received file
        PEER_PUB_KEY=$(awk -F'= ' '/^PublicKey/{gsub(/ /, "", $2); print $2}' "$PEER_CONFIG_FILE")
        PEER_ALLOWED_IPS=$(awk -F'= ' '/^AllowedIPs/{gsub(/ /, "", $2); print $2}' "$PEER_CONFIG_FILE")
        PEER_KEEPALIVE_INTERVAL=$(awk -F'= ' '/^PersistentKeepalive/{gsub(/ /, "", $2); print $2}' "$PEER_CONFIG_FILE")

        if [ -z "$PEER_PUB_KEY" ] || [ -z "$PEER_ALLOWED_IPS" ] || [ -z "$PEER_KEEPALIVE_INTERVAL" ]; then
            echo "FAILED: Could not parse PublicKey or AllowedIPs or PersistentKeepalive from $PEER_CONFIG_FILE"
            rm -f "$PEER_CONFIG_FILE"
        else
            echo "Adding peer $PEER_PUB_KEY with AllowedIPs $PEER_ALLOWED_IPS to hub-wg interface..."
            # Add the peer using wg set
            if wg set hub-wg peer "$PEER_PUB_KEY" allowed-ips "$PEER_ALLOWED_IPS" persistent-keepalive "$PEER_KEEPALIVE_INTERVAL"; then
                 ip -4 route add "$PEER_ALLOWED_IPS" dev hub-wg
                 echo "Successfully added peer $PEER_PUB_KEY to WireGuard interface."
                 # Rebuild proxy config only after successful peer addition
                 echo "Rebuilding proxy config..."
                 ./build_proxy_conf.sh
            else
                 echo "FAILED: wg set command failed for peer $PEER_PUB_KEY."
                 rm -f "$PEER_CONFIG_FILE"
            fi
        fi
        # --- End Dynamic Add ---

    else
        # Timeout or error, handle failure
        echo "FAILED: Could not retrieve client peer config for peer ${NEXT_PEER_NUMBER} (timeout or connection error)"
        # No file was created or it's empty/incomplete, ensure it's removed if it exists
        rm -f "$PEER_CONFIG_FILE"
    fi
done
