#!/bin/bash

ROUTER_NAME="bgp1"
PID=$1

if [ -z "$PID" ]; then
    echo "Usage: $0 <PID>"
    exit 1
fi

# Clean up old ns
sudo rm -f /var/run/netns/${ROUTER_NAME}

# Create and mount netns dir
sudo mkdir -p /var/run/netns
sudo mount -t tmpfs tmpfs /var/run/netns || true

# Create symlink
sudo ln -s /proc/${PID}/ns/net /var/run/netns/${ROUTER_NAME}

# Verify
echo "Created namespace for ${ROUTER_NAME} (PID=${PID})"
sudo ip netns exec ${ROUTER_NAME} ip route show 10.0.3.0/24