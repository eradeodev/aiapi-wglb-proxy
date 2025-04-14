#!/bin/bash

export UUID_SERVER_NAME=$(edmulti -s)
docker-compose build
docker-compose up -d