#!/usr/bin/env bash
set -euo pipefail

device="${1:-/dev/serial/by-id/usb-Turing_UsbMonitor_USB35INCHIPSV2-if00}"

if [[ -e "$device" ]]; then
  device="$(readlink -f "$device")"
elif compgen -G "/dev/ttyACM*" >/dev/null; then
  device="$(ls /dev/ttyACM* | head -n1)"
else
  echo "No TURZX/Turing serial display found under /dev/ttyACM*."
  exit 1
fi

echo "Device: $device"
stat -c 'Owner: %U  Group: %G  Mode: %A' "$device"

if [[ -r "$device" && -w "$device" ]]; then
  echo "Current user can read/write the device."
  exit 0
fi

group="$(stat -c '%G' "$device")"
cat <<EOF
Current user cannot read/write the device.

Run this once, then log out and back in:
  sudo usermod -aG $group "$USER"

Temporary current-session fix:
  sudo setfacl -m u:$USER:rw "$device"
EOF
