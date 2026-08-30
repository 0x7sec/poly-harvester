#!/bin/bash
# ==============================================================================
# Poly-Harvester: Automated Low-Latency Debian VPS Provisioner
# Configures:
#   1. High-Performance HFT Kernel Network Tuning (/etc/sysctl.d/99-hft-network.conf)
#   2. Python 3.11/3.12 Virtual Environment & uvloop
#   3. Systemd Process Supervision with High CPU Priority (Nice=-10)
#   4. Nginx Reverse Proxy with WebSocket & SSL Support
# ==============================================================================

set -e

if [ "$EUID" -ne 0 ]; then
  echo "❌ Please run as root: sudo bash scripts/setup_vps.sh"
  exit 1
fi

echo "🚀 Starting Poly-Harvester Debian VPS Setup..."

# 1. Update system packages
apt-get update && apt-get upgrade -y
apt-get install -y \
  python3 \
  python3-venv \
  python3-dev \
  python3-pip \
  git \
  curl \
  build-essential \
  pkg-config \
  libssl-dev \
  nginx \
  certbot \
  python3-certbot-nginx \
  ufw \
  htop \
  jq

# 2. Automated Low-Latency Kernel Network Tuning
echo "⚡ Applying Low-Latency HFT Kernel Network Tuning..."
cat << 'EOF' > /etc/sysctl.d/99-hft-network.conf
# ==============================================================================
# Poly-Harvester High-Frequency Trading TCP/IP Kernel Tuning
# ==============================================================================
# Fast TCP Handshakes
net.ipv4.tcp_fastopen = 3

# Fast Socket Port Recycling
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fin_timeout = 15

# Expanded Socket Memory Buffers (16MB)
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.ipv4.tcp_rmem = 4096 87380 16777216
net.ipv4.tcp_wmem = 4096 65536 16777216

# Connection Queue Backlogs
net.core.netdev_max_backlog = 10000
net.core.somaxconn = 65535

# Keepalive for persistent WebSocket feeds
net.ipv4.tcp_keepalive_time = 300
net.ipv4.tcp_keepalive_intvl = 15
net.ipv4.tcp_keepalive_probes = 5
EOF

sysctl --system

# 3. Create Project Directory & Virtual Environment
INSTALL_DIR="/opt/poly-harvester"
echo "📁 Setting up project directory at $INSTALL_DIR..."

mkdir -p $INSTALL_DIR
if [ ! -d "$INSTALL_DIR/.git" ]; then
  echo "Cloning repository..."
  git clone https://github.com/0x7sec/poly-harvester.git $INSTALL_DIR
fi

cd $INSTALL_DIR

if [ ! -d "$INSTALL_DIR/venv" ]; then
  python3 -m venv $INSTALL_DIR/venv
fi

# Upgrade pip & install dependencies
$INSTALL_DIR/venv/bin/pip install --upgrade pip setuptools wheel
$INSTALL_DIR/venv/bin/pip install -r requirements.txt
$INSTALL_DIR/venv/bin/pip install uvloop

# Ensure data directory exists
mkdir -p $INSTALL_DIR/data

# 4. Configure Systemd Service
echo "⚙️ Configuring systemd service (/etc/systemd/system/poly-harvester.service)..."
cat << 'EOF' > /etc/systemd/system/poly-harvester.service
[Unit]
Description=Poly-Harvester Autonomous Quant Arbitrage Engine
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/poly-harvester
ExecStart=/opt/poly-harvester/venv/bin/python main.py
Restart=always
RestartSec=3s

# High CPU Priority & File Limits
Nice=-10
LimitNOFILE=65536
LimitNPROC=65536

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=poly-harvester

# Environment
Environment="PYTHONUNBUFFERED=1"
Environment="PORT=8443"

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable poly-harvester.service
systemctl restart poly-harvester.service

# 5. Configure Firewall (UFW)
echo "🛡️ Configuring Firewall..."
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 8443/tcp
ufw --force enable

echo "=============================================================================="
echo "🎉 Poly-Harvester Debian VPS Setup Complete!"
echo "   Service Status: sudo systemctl status poly-harvester"
echo "   Live Logs:      sudo journalctl -u poly-harvester -f"
echo "   Dashboard:      http://$(curl -s ifconfig.me):8443"
echo "=============================================================================="
