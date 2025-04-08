#!/bin/bash

# Define the base IP
BASE_IP="10.123.123.3"
NETMASK=""

# Extract base octets
IFS='.' read -r a b c d <<< "$BASE_IP"

# Create an array of used last octets
USED_OCTETS=()

# Iterate over files in 'peers' directory and store used octets
if [ -d "/app/peers" ]; then
    for file in /app/peers/*; do
        filename=$(basename "$file")
        if [[ "$filename" =~ ^[0-9]+$ ]]; then
            USED_OCTETS+=( $((filename + 2)) )
        fi
    done
fi

# Find the first available IP
for (( i=1; ; i++ )); do
    potential_octet=$((i + 2))
    if [[ ! " ${USED_OCTETS[@]} " =~ " $potential_octet " ]]; then
        NEW_IP="$a.$b.$c.$potential_octet"
        break
    fi
done

# Print the first available IP
echo "$NEW_IP$NETMASK"

