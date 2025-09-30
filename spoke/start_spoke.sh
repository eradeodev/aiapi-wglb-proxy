#!/bin/bash

export UUID_SERVER_NAME=$(edmulti -s || cat /etc/machine-id)

docker compose down
if [ $# -gt 0 ]; then
    docker volume rm spoke_configs
fi
docker compose build
docker compose up -d
docker compose logs -f
