#!/usr/bin/env bash
# One-time hardening for a fresh Ubuntu 24.04 DigitalOcean droplet.
# Run as root:  bash deploy/setup-droplet.sh <new-username>
#
# What it does:
#   * installs security updates now and turns on automatic security updates
#   * creates a sudo user that logs in with your existing SSH key
#   * disables root login and password login over SSH
#   * firewall: only SSH, HTTP and HTTPS are allowed in
#   * fail2ban to ban IPs that brute-force SSH
#   * installs Docker and the Compose plugin
#   * adds 1 GB of swap so small droplets do not run out of memory
set -euo pipefail

USERNAME="${1:-}"
if [[ $EUID -ne 0 || -z "$USERNAME" ]]; then
  echo "usage (as root): bash $0 <new-username>" >&2
  exit 1
fi
if [[ ! -s /root/.ssh/authorized_keys ]]; then
  echo "No SSH key in /root/.ssh/authorized_keys. Create the droplet with an SSH key first;" >&2
  echo "otherwise disabling password login would lock you out." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
echo "==> Updating packages"
apt-get update -q
apt-get -y -q upgrade
apt-get -y -q install ufw fail2ban unattended-upgrades docker.io docker-compose-v2 git

echo "==> Automatic security updates"
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "==> Creating user $USERNAME"
if ! id "$USERNAME" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "$USERNAME"
fi
usermod -aG sudo,docker "$USERNAME"
install -d -m 700 -o "$USERNAME" -g "$USERNAME" "/home/$USERNAME/.ssh"
install -m 600 -o "$USERNAME" -g "$USERNAME" /root/.ssh/authorized_keys "/home/$USERNAME/.ssh/authorized_keys"
if [[ "$(passwd -S "$USERNAME" | awk '{print $2}')" != "P" ]]; then
  echo "Set a password for $USERNAME (used for sudo, not for SSH):"
  passwd "$USERNAME"
fi

echo "==> Hardening SSH"
cat > /etc/ssh/sshd_config.d/99-hardening.conf <<CONF
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
MaxAuthTries 3
LoginGraceTime 30
X11Forwarding no
AllowUsers $USERNAME
CONF
sshd -t
systemctl reload ssh

echo "==> Firewall"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

echo "==> fail2ban"
systemctl enable --now fail2ban

echo "==> Swap"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo "/swapfile none swap sw 0 0" >> /etc/fstab
fi

systemctl enable --now docker
echo
echo "Done. Before closing this session, open a NEW terminal and check you can log in:"
echo "    ssh $USERNAME@$(hostname -I | awk '{print $1}')"
echo "Root login over SSH is now disabled."
