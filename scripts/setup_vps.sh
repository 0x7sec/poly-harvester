#!/bin/bash
# ==============================================================================
# Poly-Harvester: Automated Low-Latency Debian VPS Provisioner
# ==============================================================================

export DEBIAN_FRONTEND=noninteractive

echo "🚀 Starting Poly-Harvester Debian VPS Provisioning..."

# 1. Base packages
apt-get update -y
apt-get install -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" \
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
  jq || true

# 2. Automated Low-Latency HFT Kernel Network Tuning
echo "⚡ Applying Low-Latency HFT Kernel Network Tuning..."
cat << 'EOF' > /etc/sysctl.d/99-hft-network.conf
# Poly-Harvester HFT TCP/IP Kernel Tuning
net.ipv4.tcp_fastopen = 3
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fin_timeout = 15
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.ipv4.tcp_rmem = 4096 87380 16777216
net.ipv4.tcp_wmem = 4096 65536 16777216
net.core.netdev_max_backlog = 10000
net.core.somaxconn = 65535
net.ipv4.tcp_keepalive_time = 300
net.ipv4.tcp_keepalive_intvl = 15
net.ipv4.tcp_keepalive_probes = 5
EOF

sysctl --system || true

# 3. Project Directory & Python Virtual Environment
INSTALL_DIR="/opt/poly-harvester"
echo "📁 Setting up Python virtual environment in $INSTALL_DIR..."

mkdir -p $INSTALL_DIR/data

cd $INSTALL_DIR

if [ ! -d "$INSTALL_DIR/venv" ]; then
  python3 -m venv $INSTALL_DIR/venv
fi

# Upgrade pip & install dependencies
$INSTALL_DIR/venv/bin/pip install --upgrade pip setuptools wheel
$INSTALL_DIR/venv/bin/pip install -r requirements.txt
$INSTALL_DIR/venv/bin/pip install uvloop || true

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

# 5. Configure Nginx Reverse Proxy for poly.0x7sec.com
echo "🌐 Configuring Nginx Reverse Proxy for poly.0x7sec.com..."
cat << 'EOF' > /etc/nginx/sites-available/poly-harvester
server {
    listen 80;
    server_name poly.0x7sec.com;

    location / {
        proxy_pass http://127.0.0.1:8443;
        proxy_http_version 1.1;

        # WebSocket Streaming Support (Crucial for 20Hz Real-Time Price/Telemetry)
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Disable caching so UI updates are immediately visible without Cloudflare stale cache
        add_header Cache-Control "no-store, no-cache, must-revalidate, max-age=0" always;
        add_header Pragma "no-cache" always;

        # Disable buffering for zero-latency tick delivery
        proxy_buffering off;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }
}
EOF

ln -sf /etc/nginx/sites-available/poly-harvester /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx || true

# 6. Attempt automated Let's Encrypt SSL certificate
echo "🔒 Checking Let's Encrypt SSL for poly.0x7sec.com..."
certbot --nginx -d poly.0x7sec.com --non-interactive --agree-tos --register-unsafely-without-email --redirect || echo "⚠️ Notice: Cloudflare handles SSL on the edge."

# 7. Configure Firewall (UFW)
echo "🛡️ Configuring Firewall..."
ufw allow 22/tcp || true
ufw allow 80/tcp || true
ufw allow 443/tcp || true
ufw allow 8443/tcp || true
ufw --force enable || true

HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "YOUR_VPS_IP")
echo "=============================================================================="
echo "🎉 Poly-Harvester Debian VPS Setup Complete!"
echo "   Domain:         https://poly.0x7sec.com"
echo "   Service Status: systemctl status poly-harvester"
echo "   Live Logs:      journalctl -u poly-harvester -f"
echo "   Dashboard:      http://${HOST_IP}:8443"
echo "=============================================================================="
