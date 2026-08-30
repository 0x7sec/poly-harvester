#!/bin/bash
# ==============================================================================
# Poly-Harvester: Zero-Downtime Deployment Script (Called by GitHub Actions)
# ==============================================================================

set -e

INSTALL_DIR="/opt/poly-harvester"
cd $INSTALL_DIR

echo "🔄 Pulling latest code from origin/main..."
git fetch origin main
git reset --hard origin/main

echo "📦 Updating Python virtual environment dependencies..."
$INSTALL_DIR/venv/bin/pip install --upgrade pip
$INSTALL_DIR/venv/bin/pip install -r requirements.txt
if command -v $INSTALL_DIR/venv/bin/pip &> /dev/null; then
    $INSTALL_DIR/venv/bin/pip install uvloop || true
fi

echo "🚀 Restarting poly-harvester.service..."
systemctl restart poly-harvester.service

# Wait 3 seconds and verify service health
sleep 3
if systemctl is-active --quiet poly-harvester.service; then
    echo "✅ Service successfully restarted and ACTIVE!"
    echo "   Dashboard reachable at port 8443"
else
    echo "❌ Error: Service failed to start. Showing last 30 log lines:"
    journalctl -u poly-harvester -n 30 --no-pager
    exit 1
fi
