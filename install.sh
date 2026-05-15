#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
install_dir="${data_home}/turzx-native-monitor"
venv_dir="${install_dir}/venv"
bin_dir="$HOME/.local/bin"
systemd_user_dir="${config_home}/systemd/user"
desktop_dir="${data_home}/applications"

interval="${INTERVAL:-1}"
brightness="${BRIGHTNESS:-100}"
device="${DEVICE:-AUTO}"
rotate="${ROTATE:-0}"

args=(--interval "$interval" --brightness "$brightness" --device "$device")
if [[ "$rotate" == "180" || "$rotate" == "true" || "$rotate" == "1" ]]; then
  args+=(--rotate-180)
fi

python_bin="${PYTHON:-python3}"

mkdir -p "$install_dir" "$bin_dir" "$systemd_user_dir" "$desktop_dir"
"$python_bin" -m venv "$venv_dir"
"$venv_dir/bin/python" -m ensurepip --upgrade >/dev/null 2>&1 || true
"$venv_dir/bin/python" -m pip install --upgrade pip
"$venv_dir/bin/python" -m pip install "$repo_dir"

cat > "$bin_dir/turzx-native-monitor" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec "$venv_dir/bin/turzx-native-monitor" "\$@"
EOF
chmod +x "$bin_dir/turzx-native-monitor"

exec_start="$bin_dir/turzx-native-monitor"
for arg in "${args[@]}"; do
  exec_start+=" $(printf '%q' "$arg")"
done

sed "s|@EXEC_START@|$exec_start|" \
  "$repo_dir/systemd/turzx-native-monitor.service.in" \
  > "$systemd_user_dir/turzx-native-monitor.service"

cat > "$desktop_dir/turzx-native-monitor.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=TURZX Native Monitor
Comment=Native Linux monitor for TURZX/Turing 3.5-inch USB displays
Exec=$bin_dir/turzx-native-monitor ${args[*]}
Terminal=false
Categories=Utility;
EOF

systemctl --user daemon-reload
systemctl --user enable turzx-native-monitor.service
systemctl --user restart turzx-native-monitor.service
update-desktop-database "$desktop_dir" >/dev/null 2>&1 || true

"$repo_dir/scripts/check-device.sh" || true

cat <<EOF

Installed TURZX native monitor.

Service:
  systemctl --user status turzx-native-monitor.service

Logs:
  journalctl --user -u turzx-native-monitor.service -f
EOF
