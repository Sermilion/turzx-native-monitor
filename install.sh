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
dry_run="${TURZX_INSTALL_DRY_RUN:-0}"

args=(--interval "$interval" --brightness "$brightness" --device "$device")
if [[ "$rotate" == "180" || "$rotate" == "true" || "$rotate" == "1" ]]; then
  args+=(--rotate-180)
fi

python_bin="${PYTHON:-python3}"

reject_newlines() {
  local value="$1"
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    echo "Install paths and arguments cannot contain newlines." >&2
    exit 1
  fi
}

systemd_quote_arg() {
  local value="$1"
  reject_newlines "$value"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//\$/\$\$}"
  value="${value//%/%%}"
  printf '"%s"' "$value"
}

desktop_quote_arg() {
  local value="$1"
  reject_newlines "$value"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//\`/\\\`}"
  value="${value//\$/\\\$}"
  value="${value//%/%%}"
  printf '"%s"' "$value"
}

join_args() {
  local quote_function="$1"
  shift
  local first=1
  local arg
  for arg in "$@"; do
    if (( first )); then
      first=0
    else
      printf ' '
    fi
    "$quote_function" "$arg"
  done
}

mkdir -p "$install_dir" "$bin_dir" "$systemd_user_dir" "$desktop_dir"
if [[ "$dry_run" != "1" ]]; then
  "$python_bin" -m venv "$venv_dir"
  "$venv_dir/bin/python" -m ensurepip --upgrade >/dev/null 2>&1 || true
  "$venv_dir/bin/python" -m pip install --upgrade pip
  "$venv_dir/bin/python" -m pip install "$repo_dir"
fi

{
  printf '%s\n' '#!/usr/bin/env bash'
  printf '%s\n' 'set -euo pipefail'
  printf 'exec %q "$@"\n' "$venv_dir/bin/turzx-native-monitor"
} > "$bin_dir/turzx-native-monitor"
chmod +x "$bin_dir/turzx-native-monitor"

systemd_exec="$(join_args systemd_quote_arg "$bin_dir/turzx-native-monitor" "${args[@]}")"
desktop_exec="$(join_args desktop_quote_arg "$bin_dir/turzx-native-monitor" "${args[@]}")"

cat > "$systemd_user_dir/turzx-native-monitor.service" <<EOF
[Unit]
Description=TURZX native Linux monitor
After=graphical-session.target

[Service]
Type=simple
ExecStart=$systemd_exec
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF

cat > "$desktop_dir/turzx-native-monitor.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=TURZX Native Monitor
Comment=Native Linux monitor for TURZX/Turing 3.5-inch USB displays
Exec=$desktop_exec
Terminal=false
Categories=Utility;
EOF

if [[ "$dry_run" == "1" ]]; then
  echo "Generated install files with TURZX_INSTALL_DRY_RUN=1; service was not changed."
  exit 0
fi

systemctl --user daemon-reload
systemctl --user enable turzx-native-monitor.service
systemctl --user restart turzx-native-monitor.service
update-desktop-database "$desktop_dir" >/dev/null 2>&1 || true

"$repo_dir/scripts/check-device.sh" "$device" || true

cat <<EOF

Installed TURZX native monitor.

Service:
  systemctl --user status turzx-native-monitor.service

Logs:
  journalctl --user -u turzx-native-monitor.service -f
EOF
