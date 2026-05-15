#!/usr/bin/env bash
set -euo pipefail

default_device="/dev/serial/by-id/usb-Turing_UsbMonitor_USB35INCHIPSV2-if00"
turzx_vendor_id="1a86"
turzx_product_id="5722"

read_sysfs_value() {
  local path="$1"
  if [[ -r "$path" ]]; then
    tr -d '\n' < "$path"
  fi
  return 0
}

tty_sysfs_device() {
  local tty_name
  tty_name="$(basename "$1")"
  readlink -f "/sys/class/tty/$tty_name/device" 2>/dev/null || true
}

usb_metadata_path() {
  local current
  current="$(tty_sysfs_device "$1")"
  while [[ -n "$current" && "$current" == /sys/* ]]; do
    if [[ -r "$current/idVendor" || -r "$current/idProduct" ]]; then
      printf '%s\n' "$current"
      return 0
    fi
    [[ "$current" == "/sys" ]] && break
    current="$(dirname "$current")"
  done
  return 1
}

is_turzx_display() {
  local metadata_path vendor_id product_id descriptors
  metadata_path="$(usb_metadata_path "$1" || true)"
  [[ -n "$metadata_path" ]] || return 1

  vendor_id="$(read_sysfs_value "$metadata_path/idVendor")"
  product_id="$(read_sysfs_value "$metadata_path/idProduct")"
  if [[ "${vendor_id,,}" == "$turzx_vendor_id" && "${product_id,,}" == "$turzx_product_id" ]]; then
    return 0
  fi

  descriptors="$(
    printf '%s %s %s' \
      "$(read_sysfs_value "$metadata_path/manufacturer")" \
      "$(read_sysfs_value "$metadata_path/product")" \
      "$(read_sysfs_value "$metadata_path/serial")"
  )"
  descriptors="${descriptors,,}"
  [[ "$descriptors" == *usbmonitor* || "$descriptors" == *usb35inchip* ]]
}

describe_device() {
  local metadata_path vendor_id product_id descriptors
  metadata_path="$(usb_metadata_path "$1" || true)"
  if [[ -z "$metadata_path" ]]; then
    printf '%s (unknown USB metadata)\n' "$1"
    return
  fi

  vendor_id="$(read_sysfs_value "$metadata_path/idVendor")"
  product_id="$(read_sysfs_value "$metadata_path/idProduct")"
  descriptors="$(
    printf '%s %s %s' \
      "$(read_sysfs_value "$metadata_path/manufacturer")" \
      "$(read_sysfs_value "$metadata_path/product")" \
      "$(read_sysfs_value "$metadata_path/serial")"
  )"
  printf '%s (%s:%s %s)\n' "$1" "${vendor_id:-unknown}" "${product_id:-unknown}" "$descriptors"
}

resolve_device() {
  local requested="${1:-AUTO}"
  if [[ "$requested" != "AUTO" ]]; then
    if [[ ! -e "$requested" ]]; then
      echo "Configured device does not exist: $requested" >&2
      exit 1
    fi
    readlink -f "$requested"
    return
  fi

  if [[ -e "$default_device" ]]; then
    readlink -f "$default_device"
    return
  fi

  shopt -s nullglob
  local all_devices=(/dev/ttyACM*)
  local matches=()
  local candidate
  for candidate in "${all_devices[@]}"; do
    if is_turzx_display "$candidate"; then
      matches+=("$candidate")
    fi
  done
  shopt -u nullglob

  if (( ${#matches[@]} == 1 )); then
    readlink -f "${matches[0]}"
    return
  fi

  if (( ${#matches[@]} > 1 )); then
    echo "Multiple TURZX/Turing displays were found; pass one path explicitly:" >&2
    printf '  %s\n' "${matches[@]}" >&2
    exit 1
  fi

  if (( ${#all_devices[@]} > 0 )); then
    echo "Found serial devices, but none match TURZX USB ID ${turzx_vendor_id}:${turzx_product_id}:" >&2
    for candidate in "${all_devices[@]}"; do
      printf '  ' >&2
      describe_device "$candidate" >&2
    done
    echo "Pass the display path explicitly if this is a compatible clone." >&2
    exit 1
  fi

  echo "No TURZX/Turing serial display found under /dev/ttyACM*." >&2
  exit 1
}

device="$(resolve_device "${1:-AUTO}")"

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
