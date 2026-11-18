#!/usr/bin/env bash
# Turns a GPIO-controlled fan on when the Pi gets hot, using the kernel's
# gpio-fan overlay. Run on the Pi; it asks for your sudo password.
#
#   bash ~/clippzz/scripts/pi-fan.sh <BCM GPIO number> [on-temperature in C, default 60]
#
# The number is the BCM GPIO number, not the physical pin number:
#   physical pin 8  = GPIO 14      physical pin 24 = GPIO 8
#
# Only for a fan with a separate control wire (three wires, like the official
# Pi case fan) or one switched through a transistor. Never power a two-wire
# fan straight from a GPIO pin: the pin cannot supply enough current.
set -euo pipefail

GPIO="${1:?Give the BCM GPIO number, for example: pi-fan.sh 14 60}"
TEMP_C="${2:-60}"
CONFIG=/boot/firmware/config.txt
CMDLINE=/boot/firmware/cmdline.txt

case "$GPIO" in ''|*[!0-9]*) echo "GPIO must be a number." >&2; exit 1;; esac
case "$TEMP_C" in ''|*[!0-9]*) echo "Temperature must be a whole number." >&2; exit 1;; esac

if [ "$GPIO" = "14" ] || [ "$GPIO" = "15" ] || [ "$GPIO" = "27" ]; then
  if systemctl is-active --quiet sandbot-arm.service 2>/dev/null; then
    echo "Stop: GPIO $GPIO is driven by the robot arm server (sandbot-arm.service," >&2
    echo "stepper direction pins 14, 15, 27). A fan there would be switched by the" >&2
    echo "arm, and the arm would lose that pin. Move the fan's control wire to a" >&2
    echo "free pin (for example GPIO 18, physical pin 12) and run this again with 18." >&2
    exit 1
  fi
fi

sudo cp -n "$CONFIG" "$CONFIG.before-fan"
sudo sed -i '/^dtoverlay=gpio-fan/d' "$CONFIG"
printf '\n[all]\ndtoverlay=gpio-fan,gpiopin=%s,temp=%s000\n' "$GPIO" "$TEMP_C" | sudo tee -a "$CONFIG" >/dev/null

if [ "$GPIO" = "14" ] && grep -q 'console=serial0' "$CMDLINE"; then
  # GPIO 14 is also the serial console's transmit pin; the two cannot share it.
  sudo cp -n "$CMDLINE" "$CMDLINE.before-fan"
  sudo sed -i 's/console=serial0,[0-9]* *//' "$CMDLINE"
  echo "Removed the serial console from GPIO 14 (backup: $CMDLINE.before-fan)."
fi

echo "Fan set: GPIO $GPIO turns on at ${TEMP_C}C and off about 10C below that."
echo "Reboot to apply:  sudo reboot"
