#!/usr/bin/env bash
set -euo pipefail

data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
config_home="${XDG_CONFIG_HOME:-$HOME/.config}"

systemctl --user disable --now turzx-native-monitor.service >/dev/null 2>&1 || true
rm -f "$config_home/systemd/user/turzx-native-monitor.service"
rm -f "$HOME/.local/bin/turzx-native-monitor"
rm -f "$data_home/applications/turzx-native-monitor.desktop"
rm -rf "$data_home/turzx-native-monitor"
systemctl --user daemon-reload >/dev/null 2>&1 || true
update-desktop-database "$data_home/applications" >/dev/null 2>&1 || true

echo "Removed TURZX native monitor user install."
