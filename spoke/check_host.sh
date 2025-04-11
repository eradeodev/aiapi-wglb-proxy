#!/bin/bash

# Check if a host was provided
if [ -z "$1" ]; then
    echo "Usage: $0 <host>"
    exit 1
fi

host="$1"
timeout=30   # total time to check, in seconds
interval=1   # delay between attempts, in seconds
ping_timeout=3  # timeout for each individual ping attempt

end_time=$((SECONDS + timeout))

while [ $SECONDS -lt $end_time ]; do
    if ping -c 1 -W $ping_timeout "$host" > /dev/null 2>&1; then
        echo "Host is reachable"
        exit 0
    fi
    sleep $interval
done

echo "Host is NOT reachable within $timeout seconds"
exit 1