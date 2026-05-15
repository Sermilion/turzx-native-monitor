# TURZX Native Monitor

Native Linux monitor UI for TURZX/Turing 3.5-inch USB smart screens.

This avoids the vendor Windows app and talks to the display through the Linux
CDC serial device, usually `/dev/ttyACM0`.

## Supported Device

Tested with:

- USB ID: `1a86:5722`
- Serial: `USB35INCHIPSV2`
- Kernel driver: `cdc_acm`
- Resolution: `320x480`

## Install

```bash
git clone https://github.com/Sermilion/turzx-native-monitor.git
cd turzx-native-monitor
INTERVAL=1 ./install.sh
```

The installer creates:

- a virtualenv under `~/.local/share/turzx-native-monitor/venv`
- a launcher at `~/.local/bin/turzx-native-monitor`
- a user service at `~/.config/systemd/user/turzx-native-monitor.service`
- an app menu entry named `TURZX Native Monitor`

## Device Permissions

If the service cannot open the display, check the serial device:

```bash
./scripts/check-device.sh
```

On Arch-based systems the group is usually `uucp`; on Debian/Ubuntu it is
usually `dialout`. Use the group reported by the script:

```bash
sudo usermod -aG uucp "$USER"
```

Then log out and back in.

For a temporary current-session fix:

```bash
sudo setfacl -m u:$USER:rw /dev/ttyACM0
```

## Service Commands

```bash
systemctl --user status turzx-native-monitor.service
systemctl --user restart turzx-native-monitor.service
systemctl --user stop turzx-native-monitor.service
journalctl --user -u turzx-native-monitor.service -f
```

## Configuration

Set install-time environment variables:

```bash
INTERVAL=1 BRIGHTNESS=90 ROTATE=0 DEVICE=AUTO ./install.sh
```

Available variables:

- `INTERVAL`: refresh interval in seconds, default `1`
- `BRIGHTNESS`: display brightness from `0` to `100`, default `100`
- `ROTATE`: set to `180`, `true`, or `1` for upside-down mounting; default `0`
- `DEVICE`: serial device path, or `AUTO`, default `AUTO`

You can also run directly:

```bash
turzx-native-monitor --interval 1
```

Generate a preview image without touching the display:

```bash
turzx-native-monitor --preview /tmp/turzx-preview.png
```

## Uninstall

```bash
./uninstall.sh
```

## License

GPL-3.0-or-later. This project depends on `smartscreen-driver`, which is GPL-licensed.
