#!/usr/bin/env python3
import os
import sys
import urllib.request

# Get the ENABLED_FOR_REQUESTS environment variable
enabled = os.getenv("ENABLED_FOR_REQUESTS", "")
if not enabled:
    print("ENABLED_FOR_REQUESTS not set")
    sys.exit(1)

# Split into individual endpoints
endpoints = enabled.split(",")

# Extract ports and check /health
for endpoint in endpoints:
    try:
        # Example item: ":11434/v1/chat/completions"
        if endpoint.startswith(":"):
            port_part = endpoint.split("/")[0]
            port = port_part.strip(":")
            url = f"http://localhost:{port}/health"

            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status != 200:
                    print(f"Healthcheck failed for {url} - Status: {response.status}")
                    sys.exit(1)
        else:
            print(f"Invalid format in ENABLED_FOR_REQUESTS: {endpoint}")
            sys.exit(1)
    except Exception as e:
        print(f"Healthcheck failed for {endpoint}: {e}")
        sys.exit(1)

print("All healthchecks passed")
sys.exit(0)