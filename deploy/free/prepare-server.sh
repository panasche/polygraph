#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu server for Polygraph Cloud.
#
#   sudo ./deploy/free/prepare-server.sh
#
# It installs Docker from Docker's own package repository, opens ports 80 and
# 443 in the server's firewall, turns on automatic security updates, and adds
# two daily jobs: the retention prune and a backup. Safe to run again.
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
systemctl enable --now docker
# Membership of the docker group is as powerful as root. Fine on a server only you use.
usermod -aG docker "$user"

echo "==> Firewall: allow HTTP and HTTPS"
# Oracle's Ubuntu images block everything except SSH in iptables, on top of
# the security list in the Oracle console. Both must allow 80 and 443.
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

echo "==> Daily jobs (times are UTC: 21:30 UTC is 03:00 IST)"
cat >/etc/cron.d/polygraph <<EOF
# Polygraph Cloud daily jobs. Output goes to the system journal:
#   journalctl -t polygraph-prune    journalctl -t polygraph-backup
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
30 21 * * * $user $here/polygraph.sh run --rm --no-deps -T worker python -m polygraph_cloud.jobs.prune 2>&1 | logger -t polygraph-prune
0 22 * * * $user $here/backup.sh 2>&1 | logger -t polygraph-backup
EOF
chmod 644 /etc/cron.d/polygraph

echo
echo "Done. Log out and back in so '$user' can run docker without sudo."
echo "Next: ./deploy/free/init-env.sh"
