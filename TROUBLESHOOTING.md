# Troubleshooting Guide

## Camera Scanner Issues

### Problem: "Camera scanner unavailable (picamera2=False)"

**Symptoms:**
- Warning at startup: `WARNING Camera scanner unavailable (picamera2=False pyzbar=True). Using stdin fallback.`
- System falls back to manual barcode entry via stdin
- Camera not detected despite `python3-picamera2` being installed

**Root Causes:**

1. **Virtual Environment Without System Site Packages**
   - The venv was created without access to system-installed packages
   - `picamera2` and `libcamera` are system-only packages (cannot be pip-installed)
   - **Fix:** Recreate venv with `--system-site-packages` flag

2. **Conflicting Local libcamera Installation**
   - An outdated locally-compiled libcamera in `/usr/local/lib/aarch64-linux-gnu/`
   - Missing symbols: `_ZN9libcamera8controls3rpi20CnnEnableInputTensorE`
   - Takes precedence over newer system version in `/usr/lib/aarch64-linux-gnu/`
   - **Fix:** Remove or backup the local installation

**Solution Steps:**

```bash
# 1. Backup and remove conflicting local libcamera
sudo mv /usr/local/lib/aarch64-linux-gnu/libcamera.so.0.7.0 \
        /usr/local/lib/aarch64-linux-gnu/libcamera.so.0.7.0.bak
sudo mv /usr/local/lib/aarch64-linux-gnu/libcamera-base.so.0.7.0 \
        /usr/local/lib/aarch64-linux-gnu/libcamera-base.so.0.7.0.bak

# 2. Recreate virtual environment with system site packages
mv .venv .venv_backup
python3 -m venv --system-site-packages .venv

# 3. Reinstall Python dependencies
source .venv/bin/activate
pip install -r requirements_pi.txt

# 4. Verify imports
python3 -c "import libcamera; print('✓ libcamera OK')"
python3 -c "from picamera2 import Picamera2; print('✓ picamera2 OK')"
python3 -c "from pyzbar.pyzbar import decode; print('✓ pyzbar OK')"

# 5. Test the application
python main.py
# Should see: "INFO Camera started for continuous barcode scanning"
```

**Verification:**

After applying the fix, you should see these logs at startup:
```
INFO Camera camera_manager.cpp:340 libcamera v0.7.0+rpt20260205
INFO Camera now open.
INFO Camera started for continuous barcode scanning
```

**NOT** this warning:
```
WARNING Camera scanner unavailable (picamera2=False pyzbar=True). Using stdin fallback.
```

---

## HX711 Weight Sensor Warnings

**Symptoms:**
```
RuntimeWarning: This channel is already in use, continuing anyway.
WARNING HX711 reset timed out after 1.00s; disabling weight reads
```

**Causes:**
- GPIO pins may be in use from previous runs
- Weight sensor hardware not properly connected
- Insufficient power to the sensor

**Solutions:**

1. **Clean GPIO State:**
   ```bash
   # Reset GPIO pins before running
   sudo python3 -c "import RPi.GPIO as GPIO; GPIO.setmode(GPIO.BCM); GPIO.cleanup()"
   ```

2. **Check Hardware Connections:**
   - DOUT → GPIO 5
   - SCK → GPIO 6
   - VCC → 3.3V or 5V
   - GND → Ground

3. **Disable Warnings (Optional):**
   Add to top of script:
   ```python
   import RPi.GPIO as GPIO
   GPIO.setwarnings(False)
   ```

---

## TFT Display Issues

**Symptoms:**
```
RuntimeWarning: This channel is already in use, continuing anyway.
```

**Cause:**
- SPI GPIO pins (CE0, DC, RST) already initialized from previous runs

**Solutions:**

1. **GPIO Cleanup Before Run:**
   ```bash
   sudo python3 -c "import RPi.GPIO as GPIO; GPIO.cleanup()"
   ```

2. **Enable SPI Interface (if not enabled):**
   ```bash
   sudo raspi-config nonint do_spi 0
   sudo reboot
   ```

3. **Verify SPI Devices:**
   ```bash
   ls -l /dev/spidev*
   # Should show: /dev/spidev0.0 and /dev/spidev0.1
   ```

---

## Database Connection Issues

**Symptoms:**
- `psycopg2.OperationalError: could not connect to server`
- Product lookups timing out

**Solutions:**

1. **Check .env Configuration:**
   ```bash
   cat .env | grep DATABASE_URL
   # Should be: DATABASE_URL=postgresql://user:pass@host:port/dbname?sslmode=require
   ```

2. **Test Database Connection:**
   ```python
   import psycopg2
   import os
   from dotenv import load_dotenv

   load_dotenv()
   conn = psycopg2.connect(os.getenv("DATABASE_URL"))
   print("✓ Database connected")
   conn.close()
   ```

3. **Network Connectivity:**
   ```bash
   # Test network access
   ping -c 3 <database-host>

   # Test database port
   nc -zv <database-host> 5432
   ```

---

## Best Practices

### Before Each Run

```bash
# 1. Activate virtual environment
source .venv/bin/activate

# 2. Clean GPIO state (optional, reduces warnings)
sudo python3 -c "import RPi.GPIO as GPIO; GPIO.cleanup()"

# 3. Verify camera detection
python3 -c "from picamera2 import Picamera2; print('Camera OK')"

# 4. Run application
python main.py
```

### Development Tips

1. **Quick Camera Test:**
   ```bash
   rpicam-still -o test.jpg -t 1000
   # If this works, libcamera is functional
   ```

2. **Check System Logs:**
   ```bash
   dmesg | grep -i camera
   # Shows kernel-level camera detection
   ```

3. **Monitor Application Logs:**
   ```bash
   python main.py 2>&1 | tee runtime.log
   # Saves all output to runtime.log
   ```

---

## Known Issues

### Debian Trixie (Testing) Compatibility

- Trixie is a testing/unstable distribution
- Library versions may have breaking changes
- For production, consider Raspberry Pi OS Bookworm (stable)

### Locally Compiled Libraries

If you've compiled libcamera from source:
- Check `/usr/local/lib/aarch64-linux-gnu/` for conflicting versions
- System packages in `/usr/lib/` should take precedence
- Consider removing local builds to use system packages

---

## Getting Help

1. **Check Logs:**
   - Application logs show detailed error messages
   - Look for `WARNING` and `ERROR` level messages

2. **Verify Hardware:**
   - Camera: `rpicam-still -o test.jpg -t 1000`
   - GPIO: `gpio readall`
   - SPI: `ls /dev/spidev*`

3. **System Information:**
   ```bash
   # OS version
   lsb_release -a

   # Python version
   python3 --version

   # Package versions
   dpkg -l | grep -E 'libcamera|picamera2'
   ```
