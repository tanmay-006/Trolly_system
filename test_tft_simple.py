#!/usr/bin/env python3
from luma.core.interface.serial import spi
from luma.lcd.device import st7735
from PIL import Image, ImageDraw

print("Initializing display...")
serial = spi(port=0, device=0, gpio_DC=24, gpio_RST=25, bus_speed_hz=16_000_000)
device = st7735(serial, width=160, height=128, rotate=2, bgr=True)

# Fill screen with different colors to test
print("Test 1: Full white...")
img = Image.new('RGB', (160, 128), 'white')
device.display(img)
input("Press Enter for next test...")

print("Test 2: Full red...")
img = Image.new('RGB', (160, 128), 'red')
device.display(img)
input("Press Enter for next test...")

print("Test 3: Full green...")
img = Image.new('RGB', (160, 128), 'green')
device.display(img)
input("Press Enter for next test...")

print("Test 4: Full blue...")
img = Image.new('RGB', (160, 128), 'blue')
device.display(img)
input("Press Enter for next test...")

print("Test 5: Black with white text...")
img = Image.new('RGB', (160, 128), 'black')
draw = ImageDraw.Draw(img)
draw.text((20, 50), "HELLO TFT!", fill='white')
device.display(img)
input("Press Enter to finish...")

print("Done!")
