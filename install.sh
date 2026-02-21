#!/bin/bash

# =============================================================================

# Shazam Vinylstreamer — Installation Script

# =============================================================================

# Prerequisites already in place: icecast-kh, darkice

# Run this script from the directory containing the files:

# shazam_vinylstreamer.py

# nowplaying.html

# 

# Usage:

# chmod +x install.sh

# ./install.sh

# =============================================================================

set -e  # Exit on any error

echo “=== Shazam Vinylstreamer installer ===”

# —————————————————————————–

# 1. System dependencies

# —————————————————————————–

echo “”
echo “— Installing system dependencies —”
sudo apt update
sudo apt install -y ffmpeg python3-venv python3-pip

# —————————————————————————–

# 2. Python virtual environment + shazamio

# —————————————————————————–

echo “”
echo “— Setting up Python venv at /home/pi/shazam/venv —”
mkdir -p /home/pi/shazam
python3 -m venv /home/pi/shazam/venv
/home/pi/shazam/venv/bin/pip install –upgrade pip
/home/pi/shazam/venv/bin/pip install shazamio

# —————————————————————————–

# 3. Copy script

# —————————————————————————–

echo “”
echo “— Installing shazam_vinylstreamer.py —”
cp shazam_vinylstreamer.py /home/pi/shazam/shazam_vinylstreamer.py
chmod +x /home/pi/shazam/shazam_vinylstreamer.py

# —————————————————————————–

# 4. Fix permissions on Icecast web root so the pi user can write cover art

# —————————————————————————–

echo “”
echo “— Fixing Icecast web root permissions —”
sudo chown pi:pi /usr/share/icecast2/web

# —————————————————————————–

# 5. Copy web files to Icecast web root

# —————————————————————————–

echo “”
echo “— Copying web files to Icecast web root —”
sudo cp nowplaying.html /usr/share/icecast2/web/nowplaying.html

# —————————————————————————–

# 6. Install and enable systemd service

# —————————————————————————–

echo “”
echo “— Installing systemd service —”
sudo tee /etc/systemd/system/shazam_vinylstreamer.service > /dev/null <<EOF
[Unit]
Description=Shazam Vinylstreamer — Song recognition for Icecast stream
After=network.target icecast-kh.service

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/shazam
ExecStart=/home/pi/shazam/venv/bin/python /home/pi/shazam/shazam_vinylstreamer.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable shazam_vinylstreamer
sudo systemctl start shazam_vinylstreamer

# —————————————————————————–

# Done

# —————————————————————————–

echo “”
echo “=== Installation complete ===”
echo “”
echo “Check service status:  sudo systemctl status shazam_vinylstreamer”
echo “Follow logs:           sudo journalctl -u shazam_vinylstreamer -f”
echo “”
echo “Web pages available at:”
echo “  https://vinylstreamer.litebattre.com/nowplaying.html”