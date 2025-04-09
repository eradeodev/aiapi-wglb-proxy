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

# Replace placeholders with environment values
sed -i "s|#HUBENDPOINT#|$HUB_ENDPOINT|g" "$FILE"
sed -i "s|#HUBPUBLICKEY#|$HUB_PUB_KEY|g" "$FILE"
sed -i "s|#DEFAULTPEERPRIV#|$DEFAULT_PEER_PRIV_KEY|g" "$FILE"

function create_new_config {
    # Start default wireguard connection, retrying the command until it succeeds
    while true; do
        # Try to bring up the WireGuard interface
        wg-quick up ./initial_config.conf

        # Check if the command was successful
        if [ $? -eq 0 ]; then
            echo "WireGuard connection established successfully."
            break
        else
            echo "Failed to bring up WireGuard. Retrying in 5 seconds..."
            sleep 5
        fi
    done
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

# Periodic connectivity check & auto-recovery
(
while true; do
    if ! ping -c 1 -W 1 10.123.123.1 &> /dev/null; then
        echo "Lost connection to 10.123.123.1. Attempting recovery..."
        wg-quick down ./wg-configs/custom_config.conf
        sleep 2
        wg-quick up ./wg-configs/custom_config.conf

        # Wait and recheck connection
        retry=0
        while ! ping -c 1 -W 1 10.123.123.1 &> /dev/null; do
            ((retry++))
            echo "Reconnection attempt $retry failed..."
            sleep 2
            if [[ $retry -eq 10 ]]; then
                echo "Failed to reconnect. Regenerating config..."
                wg-quick down ./wg-configs/custom_config.conf
                rm -f wg-configs/custom_config.conf
                create_new_config
                break
            fi
        done

        echo "Connection re-established!"
    fi
    sleep 30  # Check every 30 seconds
done
) &

# start ollama
echo "Starting ollama"
/bin/ollama serve

