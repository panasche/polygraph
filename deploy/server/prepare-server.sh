#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu server for Polygraph Cloud.
#
#   sudo ./deploy/server/prepare-server.sh
#
# It installs Docker from Docker's own package repository, adds swap on small
# servers, caps Docker's log size, opens ports 80 and 443 where the image's
# firewall blocks them, turns on automatic security updates, and adds the daily
# and weekly jobs. Safe to run again.
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Run it with sudo." >&2; exit 1; }
. /etc/os-release
[[ "$ID" == "ubuntu" ]] || { echo "This script expects Ubuntu (found $ID)." >&2; exit 1; }

user="${SUDO_USER:-ubuntu}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export DEBIAN_FRONTEND=noninteractive

# Files copied from Windows can lose the executable bit.
chmod +x "$here"/*.sh

echo "==> Packages and automatic security updates"
apt-get update -q
apt-get install -y -q ca-certificates curl git openssl unattended-upgrades
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "==> Swap"
# Building the web image needs more memory than a 2 GB server has. Swap lets
# the build finish instead of being killed; at run time the stack fits in RAM.
mem_mb=$(awk '/MemTotal/ { print int($2 / 1024) }' /proc/meminfo)
if [[ $mem_mb -lt 4000 ]] && ! swapon --show --noheadings | grep -q .; then
  fallocate -l 4G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab
  echo 'vm.swappiness=10' >/etc/sysctl.d/90-polygraph-swap.conf
  sysctl -q -p /etc/sysctl.d/90-polygraph-swap.conf
  echo "    Added 4 GB of swap (${mem_mb} MB of RAM)."
else
  echo "    Not needed (${mem_mb} MB of RAM, or swap already on)."
fi

echo "==> Docker Engine and the Compose plugin"
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    >/etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
# Without a cap, container logs grow until the disk is full.
if [[ ! -f /etc/docker/daemon.json ]]; then
  cat >/etc/docker/daemon.json <<'EOF'
{
  "log-driver": "local",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
EOF
  systemctl restart docker
fi
systemctl enable --now docker
# Membership of the docker group is as powerful as root. Fine on a server only you use.
usermod -aG docker "$user"

echo "==> Firewall"
# Oracle's Ubuntu images block everything except SSH in iptables, on top of the
# security list in the Oracle console. AWS images don't; the security group in
# the AWS console is the firewall there. Only add rules where something blocks.
if iptables -S INPUT | grep -qE -- '-j (REJECT|DROP)'; then
  for rule in "-p tcp --dport 80" "-p tcp --dport 443" "-p udp --dport 443"; do
    # shellcheck disable=SC2086
    iptables -C INPUT $rule -m conntrack --ctstate NEW -j ACCEPT 2>/dev/null ||
      iptables -I INPUT 1 $rule -m conntrack --ctstate NEW -j ACCEPT
  done
  if command -v netfilter-persistent >/dev/null; then
    netfilter-persistent save
  else
    echo "    netfilter-persistent isn't installed, so these rules last until the next reboot."
    echo "    Install it with: sudo apt-get install -y iptables-persistent"
  fi
  echo "    Opened 80 and 443 in iptables."
else
  echo "    Nothing to open here. Allow 80 and 443 in your cloud provider's firewall."
fi

echo "==> Scheduled jobs (times are UTC: 21:30 UTC is 03:00 IST)"
cat >/etc/cron.d/polygraph <<EOF
# Polygraph Cloud scheduled jobs. Output goes to the system journal:
#   journalctl -t polygraph-prune    journalctl -t polygraph-backup    journalctl -t polygraph-docker
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
30 21 * * * $user $here/polygraph.sh run --rm --no-deps -T worker python -m polygraph_cloud.jobs.prune 2>&1 | logger -t polygraph-prune
0 22 * * * $user $here/backup.sh 2>&1 | logger -t polygraph-backup
# Sundays: drop old images and build cache from earlier deploys, so the disk stays small.
30 22 * * 0 $user { docker image prune -f && docker builder prune -f --filter until=168h; } 2>&1 | logger -t polygraph-docker
EOF
chmod 644 /etc/cron.d/polygraph

echo
echo "Done. Log out and back in so '$user' can run docker without sudo."
echo "Next: ./deploy/server/init-env.sh"
