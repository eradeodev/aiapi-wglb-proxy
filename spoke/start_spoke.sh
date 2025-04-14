#!/bin/bash

SERVER_NAME=$(edmulti -s)
docker-compose build
docker-compose up -d