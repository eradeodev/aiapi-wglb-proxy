An OpenAI API proxy using a hub-and-spoke network model. Uses automatic spoke connection via wireguard.


use the compose.yml file to test it out.


# How it works
The Hub container should be running on a machine that is accessible by every
device that is going to be a Spoke. The firewall for this device should allow
for two ports to get through: one for Wireguard, and one for the Proxy
Server.

When setting up the Hub, you must generate two sets of public/private keys: One
for the Hub, and one for the Default Peer. The Default Peer is a peer that will
be automatically be used by Newly created spokes to establish a connection with
the Hub, where they will then generate a unique peer for themselves. This will
free up the Default Peer for another Spoke to use to join to the Wireguard VPN.

# Installation
There are docker compose files for the hub, and the spoke. Use the compose file
in it's specified folder to create either the Hub or a Spoke. You must also
create a .env file with the necessary environment variables. You can view which
environment variables to set in the compose files.

Be sure to generate the Hub public/private keys and Default Peer public/private
keys before moving to the next step.

Before running the Hub container, you must edit the `authorized_users.txt` file
with the users keys you require. This is not editable while the container is
running, so if you must add a user key to `authorized_users.txt` you need to
bring the container down, edit th e file, and bring the container back up.

When running the Spoke container, be sure to edit the compose file if you want
CPU inference instead of Nvidia GPU support.

The following are the various environment variables required by the Hub and
Spoke respectively:

Hub
```
HUB_PRIV_KEY: The private key generate for the Hub
DEFAULT_PEER_PUB_KEY: The public key generated for the Default Peer
```
Spoke
```
HUB_ENDPOINT: The publicly reachable address and port for the Hub Wireguard instance. eg: localhost:51820
HUB_PUB_KEY: The public key generated for the Hub
DEFAULT_PEER_PRIV_KEY: The private key generated for the Default Peer
```

Run each container with `docker compose up`
