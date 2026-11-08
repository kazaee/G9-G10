#!/bin/sh
set -e
# Run on the installed Ubuntu as an administrator after logging in at the console.
# The chosen interface is ens2f0 (MAC 8c:dc:d4:b7:e3:08).
sudo systemctl disable --now provision-network.timer
sudo systemctl stop provision-network.service
sudo tee /etc/netplan/90-provision-management.yaml >/dev/null <<'EOF'
network:
  version: 2
  ethernets:
    management:
      match:
        macaddress: "8c:dc:d4:b7:e3:08"
      optional: true
      dhcp4: false
      dhcp6: false
      addresses:
        - 193.141.127.102/24
      routes:
        - to: default
          via: 193.141.127.254
      nameservers:
        addresses:
          - 4.2.2.4
EOF
sudo chmod 600 /etc/netplan/90-provision-management.yaml
sudo netplan generate && sudo netplan apply
printf '%s\n' '{"mac":"8c:dc:d4:b7:e3:08","interface":"ens2f0"}' | sudo tee /etc/provision-network/selected.json >/dev/null
sudo cat /etc/netplan/90-provision-management.yaml
ip -br addr
ip route
