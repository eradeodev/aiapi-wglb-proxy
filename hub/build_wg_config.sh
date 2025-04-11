# file: /base-eradeo-pool/data/base-eradeo/projects/eradeo/eradeoroot/ollama-wglb-proxy/hub/build_wg_config.sh
#!/bin/bash

PEERS_DIR="/app/peers"
OUTPUT_FILE="hub-wg.conf"
BASE_CONFIG="base_config.txt"

# Ensure peers directory exists
mkdir -p "$PEERS_DIR"

# Start with the base configuration
cp "$BASE_CONFIG" "$OUTPUT_FILE" || { echo "Error copying base config"; exit 1; }

# Append only valid WireGuard peer lines from peer files
echo "" >> "$OUTPUT_FILE" # Add a newline for separation

if [ -d "$PEERS_DIR" ]; then
    shopt -s nullglob # Prevent loop running if directory is empty
    for file in "$PEERS_DIR"/*; do
        if [[ -f "$file" ]]; then
            echo "# Peer config from file: $(basename "$file")" >> "$OUTPUT_FILE"
            # Use grep to extract only relevant WireGuard Peer lines
            # Allows comments (#) and specific keys. Adjust regex if needed (e.g., for Endpoint or PresharedKey)
            grep -E '^\s*#|^\s*\[Peer\]|^\s*PublicKey\s*=|^\s*AllowedIPs\s*=|^\s*PersistentKeepalive\s*=' "$file" >> "$OUTPUT_FILE"
            echo "" >> "$OUTPUT_FILE" # Add a newline after each peer's config
        fi
    done
    shopt -u nullglob
fi

echo "WireGuard configuration built in $OUTPUT_FILE"