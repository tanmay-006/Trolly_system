#!/bin/bash

# Smart Trolley System Setup Script for Raspberry Pi
echo "Setting up Smart Trolley System on Raspberry Pi..."

# Detect current directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

echo "Working directory: $SCRIPT_DIR"

# Update system
echo "Updating system packages..."
sudo apt update
sudo apt upgrade -y

# Install system dependencies
echo "Installing system dependencies..."
sudo apt install -y python3-dev python3-pip python3-setuptools python3-venv
sudo apt install -y build-essential libssl-dev libffi-dev
sudo apt install -y bluetooth bluez libbluetooth-dev libudev-dev
sudo apt install -y i2c-tools spi-tools

# Install Pi-specific system packages (cannot be pip-installed)
echo "Installing Raspberry Pi camera system packages..."
sudo apt install -y python3-picamera2 python3-libcamera libcamera-dev

# Verify libcamera installation
echo "Verifying libcamera installation..."
if python3 -c "import libcamera; print('libcamera OK')" 2>/dev/null; then
    echo "✓ libcamera installed successfully"
else
    echo "⚠ WARNING: libcamera import failed. This may be a known issue with Debian Trixie."
    echo "  The system will fall back to stdin barcode input."
    echo "  Camera functionality may require OS downgrade to Bookworm or system updates."
fi

# Enable SPI and I2C
echo "Enabling SPI and I2C interfaces..."
sudo raspi-config nonint do_spi 0
sudo raspi-config nonint do_i2c 0

# Setup GPIO permissions
echo "Setting up GPIO permissions..."
sudo usermod -a -G gpio $USER
sudo usermod -a -G spi $USER
sudo usermod -a -G i2c $USER

# Create virtual environment with system-site-packages enabled
echo "Creating Python virtual environment..."
if [ -d ".venv" ]; then
    echo "Removing existing .venv..."
    rm -rf .venv
fi
python3 -m venv --system-site-packages .venv

# Install Python dependencies
echo "Installing Python dependencies..."
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements_pi.txt

# Verify critical imports
echo "Verifying Python package imports..."
python3 -c "import psycopg2; print('✓ psycopg2')" || echo "✗ psycopg2 FAILED"
python3 -c "import pyzbar; print('✓ pyzbar')" || echo "✗ pyzbar FAILED"
python3 -c "from luma.lcd.device import st7735; print('✓ luma.lcd')" || echo "✗ luma.lcd FAILED"
python3 -c "from picamera2 import Picamera2; print('✓ picamera2')" 2>/dev/null || echo "⚠ picamera2 (fallback to stdin)"
python3 -c "from hx711 import HX711; print('✓ hx711')" || echo "✗ hx711 FAILED"

# Setup Bluetooth printer
echo "Setting up Bluetooth..."
sudo systemctl enable bluetooth
sudo systemctl start bluetooth

echo ""
echo "========================================"
echo "Setup complete!"
echo "========================================"
echo ""
echo "Next steps:"
echo "1. Create a .env file with your DATABASE_URL and other secrets"
echo "2. Activate the virtual environment: source .venv/bin/activate"
echo "3. Run the Pi runtime: python main.py"
echo ""
echo "Note: If camera import failed, the system will use stdin for barcode input."
echo ""
