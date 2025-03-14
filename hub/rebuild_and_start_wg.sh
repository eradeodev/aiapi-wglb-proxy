#!/bin/bash
# Tear down, rebuild, and restart Wireguard

# Check if Wireguard interface is up, and tear it down if so
if ip link show hub-wg &>/dev/null; then
    wg-quick down ./hub-wg.conf
fi

# Rebuild new config
./build_wg_config.sh

# Start Wireguard up again
wg-quick up ./hub-wg.conf
