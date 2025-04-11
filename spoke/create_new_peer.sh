#!/bin/bash

PRIVATE_KEY=$(wg genkey)
PUBLIC_KEY=$(echo $PRIVATE_KEY | wg pubkey)

# Get IP Address from HUB and use that for AllowedIPs
HUB_WG_IP="10.123.123.1"
if [ -z "$HUB_ENDPOINT" ]; then
    echo "Hub Endpoint not set"
    exit 1
fi
if [ -z "$HUB_PUB_KEY" ]; then
    echo "HUB Public Key not set"
    exit 1
fi


# Attempt to retrieve SPOKE_IP with retries
timeout=10
interval=1
elapsed=0
SPOKE_IP=""
while [ $elapsed -lt $timeout ]; do
    SPOKE_IP=$(nc $HUB_WG_IP 1234 -w 5)
    if [ -n "$SPOKE_IP" ]; then
        break
    fi
    sleep $interval
    elapsed=$((elapsed + interval))
done

if [ -z "$SPOKE_IP" ]; then
    echo "Failed to retrieve SPOKE_IP within $timeout seconds." >&2
    exit 1
fi

# Create new peer config content
cat > new_peer.txt << EOF

[Peer]
PublicKey = $PUBLIC_KEY
AllowedIPs = $SPOKE_IP/32
PersistentKeepalive = 25
EnabledForRequests = $ENABLED_FOR_REQUESTS
EOF

# Attempt to send new peer config with retries
elapsed=0
while [ $elapsed -lt $timeout ]; do
    echo "Trying to send new peer config..."
    nc $HUB_WG_IP 1235 -w 5 -N < new_peer.txt && break
    sleep $interval
    elapsed=$((elapsed + interval))
done

if [ $elapsed -ge $timeout ]; then
    echo "Failed to send new peer config within $timeout seconds." >&2
    exit 1
fi

# Update spoke config
mkdir -p wg-configs
cat > wg-configs/custom_config.conf << EOF
[Interface]
PrivateKey = $PRIVATE_KEY
Address = $SPOKE_IP/32
ListenPort = 51823

[Peer]
PublicKey = $HUB_PUB_KEY
AllowedIPs = $HUB_WG_IP/32
Endpoint = $HUB_ENDPOINT
PersistentKeepalive = 25
EOF
