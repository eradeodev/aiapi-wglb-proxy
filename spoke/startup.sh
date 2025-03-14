#!/bin/bash
# Startup script for Spoke node

# Set all environment variables
if [ -z "$HUB_ENDPOINT" ]; then
    echo "Hub Endpoint not set"
    exit 1
fi
if [ -z "$HUB_PUB_KEY" ]; then
    echo "HUB Public Key not set"
    exit 1
fi
if [ -z "$DEFAULT_PEER_PRIV_KEY" ]; then
    echo "Default Peer Priv Key not set"
    exit 1
fi


# File to modify
FILE="initial_config.conf"
# Replace #HUBENDPOINT# with the value of HUB_ENDPOINT
sed -i "s|#HUBENDPOINT#|$HUB_ENDPOINT|g" "$FILE"
sed -i "s|#HUBPUBLICKEY#|$HUB_PUB_KEY|g" "$FILE"
sed -i "s|#DEFAULTPEERPRIV#|$DEFAULT_PEER_PRIV_KEY|g" "$FILE"

function create_new_config {
    # Start default wireguard connection
    wg-quick up ./initial_config.conf

    # Wait for connection to Hub
    echo "Waiting for connection to 10.123.123.1 via default config..."
    while ! ping -c 1 -W 1 10.123.123.1 &> /dev/null; do
        echo "Waiting for connection to 10.123.123.1 via default config..."
        sleep 2
    done
    echo "Connection established!"

    # Get new config
    ./create_new_peer.sh
    if [ $? -ne 0 ]; then
        echo "creating config failed."
        exit 1
    fi


    # Tear down default Wireguard config and set up new one
    wg-quick down ./initial_config.conf
    wg-quick up ./wg-configs/custom_config.conf
}

# Check for custom config, or create one
if [ -f "wg-configs/custom_config.conf" ]; then
    wg-quick up ./wg-configs/custom_config.conf
else
    create_new_config
fi

echo "Waiting for connection to 10.123.123.1 via new config..."
count=0
while ! ping -c 1 -W 1 10.123.123.1 &> /dev/null; do
    ((count++))
    echo "Waiting for connection to 10.123.123.1 via new config..."
    sleep 2
    if [[ $count -eq 60 ]]; then
        echo "couldn't connect. Attempting to retrieve new config..."
        wg-quick down ./wg-configs/custom_config.conf
        rm -f wg-configs/custom_config.conf
        create_new_config
        count=0
    fi
done
echo "Connection established!"

# start ollama
echo "Starting ollama"
/bin/ollama serve
