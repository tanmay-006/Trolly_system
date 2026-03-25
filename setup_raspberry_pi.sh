#!/bin/bash

# Raspberry Pi POS Setup Script
echo "Setting up Raspberry Pi POS System..."

# Update system
echo "Updating system packages..."
sudo apt-get update
sudo apt-get upgrade -y

# Install Python dependencies
echo "Installing Python dependencies..."
pip3 install -r requirements_pi.txt

# Install system dependencies
echo "Installing system dependencies..."
sudo apt-get install -y python3-dev python3-pip python3-setuptools
sudo apt-get install -y build-essential libssl-dev libffi-dev
sudo apt-get install -y bluetooth bluez libbluetooth-dev libudev-dev
sudo apt-get install -y i2c-tools spi-tools

# Enable SPI and I2C
echo "Enabling SPI and I2C interfaces..."
sudo raspi-config nonint do_spi 0
sudo raspi-config nonint do_i2c 0

# Setup GPIO permissions
echo "Setting up GPIO permissions..."
sudo usermod -a -G gpio $USER
sudo usermod -a -G spi $USER
sudo usermod -a -G i2c $USER

# Create systemd service for auto-start
echo "Creating systemd service..."
sudo tee /etc/systemd/system/raspberry-pi-pos.service > /dev/null <<EOF
[Unit]
Description=Raspberry Pi POS System
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/raspberry-paymentsystem
ExecStart=/usr/bin/python3 /home/pi/raspberry-paymentsystem/raspberry_pi_pos.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Enable service
sudo systemctl daemon-reload
sudo systemctl enable raspberry-pi-pos.service

# Setup Bluetooth printer
echo "Setting up Bluetooth printer..."
sudo systemctl enable bluetooth
sudo systemctl start bluetooth

# Create desktop shortcut
echo "Creating desktop shortcut..."
mkdir -p /home/pi/Desktop
cat > /home/pi/Desktop/POS_System.desktop <<EOF
[Desktop Entry]
Name=POS System
Comment=Raspberry Pi POS System
Exec=python3 /home/pi/raspberry-paymentsystem/raspberry_pi_pos.py
Icon=terminal
Terminal=true
Type=Application
Categories=Office;
EOF

chmod +x /home/pi/Desktop/POS_System.desktop

echo "Setup complete! Please reboot your Raspberry Pi."
echo "After reboot, you can:"
echo "1. Run 'python3 raspberry_pi_pos.py' to start the POS system"
echo "2. Use the desktop shortcut to start the POS system"
echo "3. The service will auto-start on boot"
