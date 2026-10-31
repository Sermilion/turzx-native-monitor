"""Render and publish a Linux monitor dashboard to a TURZX USB display."""

from __future__ import annotations

import argparse
import glob
import os
import signal
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import psutil
from PIL import Image, ImageDraw, ImageFont
from smartscreen_driver.lcd_comm import Orientation
from smartscreen_driver.lcd_comm_rev_a import LcdCommRevA


WIDTH = 320
HEIGHT = 480
DEFAULT_DEVICE_BY_ID = "/dev/serial/by-id/usb-Turing_UsbMonitor_USB35INCHIPSV2-if00"
TURZX_USB_VENDOR_ID = "1a86"
TURZX_USB_PRODUCT_ID = "5722"
TURZX_DESCRIPTOR_MARKERS = ("usbmonitor", "usb35inchip")
FONT_CANDIDATES_REGULAR = (
    "/usr/share/fonts/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)
FONT_CANDIDATES_BOLD = (
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
)
FAN_EXCLUDED_CHIPS = ("amdgpu",)
DEFAULT_MAX_RPM = 3000.0
THEMES = ("default", "cyberpunk")
DEFAULT_DYNAMIC_REGIONS = (
    (198, 44, 300, 86),
    (176, 98, 300, 126),
    (22, 132, 298, 147),
    (176, 159, 300, 187),
    (22, 193, 298, 208),
    (176, 220, 300, 248),
    (22, 254, 298, 269),
    (176, 281, 300, 309),
    (22, 315, 298, 330),
    (176, 342, 300, 370),
    (22, 376, 298, 391),
    (22, 434, 102, 456),
    (140, 434, 228, 456),
    (244, 434, 292, 456),
)


def read_text(path: str | Path) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None


def read_number(path: str | None) -> float | None:
    if not path:
        return None
    text = read_text(path)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def first_existing(paths: tuple[str, ...]) -> str | None:
    for path in paths:
        if os.path.exists(path):
            return path
    return None


def text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    left, _, right, _ = draw.textbbox((0, 0), text, font=font)
    return right - left


def draw_right(
    draw: ImageDraw.ImageDraw,
    right: int,
    y: int,
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
) -> None:
    draw.text((right - text_width(draw, text, font), y), text, fill=fill, font=font)


def tty_usb_metadata(device: str) -> dict[str, str]:
    sysfs_device = Path("/sys/class/tty") / Path(device).name / "device"
    try:
        current = sysfs_device.resolve(strict=True)
    except OSError:
        return {}

    for path in (current, *current.parents):
        vendor_id = read_text(path / "idVendor")
        product_id = read_text(path / "idProduct")
        if vendor_id or product_id:
            return {
                "idVendor": vendor_id or "",
                "idProduct": product_id or "",
                "manufacturer": read_text(path / "manufacturer") or "",
                "product": read_text(path / "product") or "",
                "serial": read_text(path / "serial") or "",
            }
    return {}


def is_turzx_display(device: str) -> bool:
    metadata = tty_usb_metadata(device)
    vendor_id = metadata.get("idVendor", "").lower()
    product_id = metadata.get("idProduct", "").lower()
    if vendor_id == TURZX_USB_VENDOR_ID and product_id == TURZX_USB_PRODUCT_ID:
        return True

    descriptors = " ".join(
        metadata.get(field, "") for field in ("manufacturer", "product", "serial")
    ).lower()
    return any(marker in descriptors for marker in TURZX_DESCRIPTOR_MARKERS)


def describe_serial_candidate(device: str) -> str:
    metadata = tty_usb_metadata(device)
    vendor_id = metadata.get("idVendor") or "unknown"
    product_id = metadata.get("idProduct") or "unknown"
    descriptors = " ".join(
        value
        for value in (
            metadata.get("manufacturer"),
            metadata.get("product"),
            metadata.get("serial"),
        )
        if value
    )
    suffix = f" {descriptors}" if descriptors else ""
    return f"{device} ({vendor_id}:{product_id}{suffix})"


def resolve_device(device: str) -> str:
    if device != "AUTO":
        resolved = os.path.realpath(os.path.expanduser(device))
        if not os.path.exists(resolved):
            raise RuntimeError(f"Configured display device does not exist: {device}")
        return resolved

    if os.path.exists(DEFAULT_DEVICE_BY_ID):
        return os.path.realpath(DEFAULT_DEVICE_BY_ID)

    serial_devices = sorted(glob.glob("/dev/ttyACM*"))
    matches = [device for device in serial_devices if is_turzx_display(device)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        options = ", ".join(matches)
        raise RuntimeError(
            f"Multiple TURZX displays found ({options}); set --device explicitly."
        )
    if serial_devices:
        options = ", ".join(
            describe_serial_candidate(candidate) for candidate in serial_devices
        )
        raise RuntimeError(
            "Found serial devices, but none match TURZX USB ID "
            f"{TURZX_USB_VENDOR_ID}:{TURZX_USB_PRODUCT_ID}; set --device explicitly. "
            f"Candidates: {options}"
        )
    raise RuntimeError("TURZX display was not found at /dev/ttyACM*")


def print_detected_device(device: str) -> int:
    print(resolve_device(device))
    return 0


def validate_args(args: argparse.Namespace) -> None:
    if not 0 <= args.brightness <= 100:
        raise RuntimeError("--brightness must be between 0 and 100")
    if args.interval <= 0:
        raise RuntimeError("--interval must be greater than 0")
    if args.pump_max_rpm <= 0 or args.fan_max_rpm <= 0:
        raise RuntimeError("--pump-max-rpm and --fan-max-rpm must be greater than 0")


def find_hwmon_by_name(name: str) -> Path | None:
    for hwmon in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
        if read_text(str(hwmon / "name")) == name:
            return hwmon
    return None


def cpu_temp_c() -> float | None:
    hwmon = find_hwmon_by_name("k10temp")
    if not hwmon:
        return None
    for temp in ("temp1_input", "temp3_input"):
        value = read_number(str(hwmon / temp))
        if value is not None:
            return value / 1000.0
    return None


def gpu_paths() -> dict[str, str | None]:
    cards: list[tuple[int, Path]] = []
    for card in sorted(Path("/sys/class/drm").glob("card[0-9]*")):
        device = card / "device"
        total = read_number(str(device / "mem_info_vram_total")) or 0
        if total:
            cards.append((int(total), device))
    if not cards:
        return {}

    device = sorted(cards, key=lambda item: item[0], reverse=True)[0][1]
    hwmon = None
    for hw in sorted((device / "hwmon").glob("hwmon*")):
        if read_text(str(hw / "name")) == "amdgpu":
            hwmon = hw
            break

    return {
        "busy": str(device / "gpu_busy_percent"),
        "vram_used": str(device / "mem_info_vram_used"),
        "vram_total": str(device / "mem_info_vram_total"),
        "temp": str(hwmon / "temp1_input") if hwmon else None,
    }


GPU_PATHS = gpu_paths()


def gpu_stats() -> tuple[float | None, float | None, float | None]:
    busy = read_number(GPU_PATHS.get("busy")) if GPU_PATHS else None
    used = read_number(GPU_PATHS.get("vram_used")) if GPU_PATHS else None
    total = read_number(GPU_PATHS.get("vram_total")) if GPU_PATHS else None
    temp = read_number(GPU_PATHS.get("temp")) if GPU_PATHS else None
    vram_pct = (used / total * 100.0) if used is not None and total else None
    return busy, vram_pct, (temp / 1000.0 if temp is not None else None)


@dataclass(frozen=True)
class FanSensors:
    """Fan inputs and full-scale RPM values shown by the cyberpunk theme."""

    pump: Path | None = None
    fan: Path | None = None
    pump_max_rpm: float = DEFAULT_MAX_RPM
    fan_max_rpm: float = DEFAULT_MAX_RPM


def fan_index(path: Path) -> int:
    digits = "".join(ch for ch in path.name if ch.isdigit())
    return int(digits) if digits else 0


def hwmon_fans() -> dict[str, Path]:
    fans: dict[str, Path] = {}
    for hwmon in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
        chip = read_text(hwmon / "name") or hwmon.name
        if chip in FAN_EXCLUDED_CHIPS:
            continue
        for fan_input in sorted(hwmon.glob("fan[0-9]*_input"), key=fan_index):
            fans[f"{chip}/{fan_input.name.removesuffix('_input')}"] = fan_input
    return fans


def resolve_fan(spec: str, fans: dict[str, Path], exclude: Path | None = None) -> Path | None:
    if spec.upper() == "NONE":
        return None
    if spec.upper() == "AUTO":
        speeds = [
            (read_number(str(path)) or 0.0, path)
            for path in fans.values()
            if path != exclude
        ]
        spinning = [item for item in speeds if item[0] > 0]
        return max(spinning, key=lambda item: item[0])[1] if spinning else None

    matches = [
        path for name, path in fans.items() if name == spec or name.endswith(f"/{spec}")
    ]
    if len(matches) == 1:
        return matches[0]
    available = ", ".join(fans) or "none"
    if matches:
        raise RuntimeError(
            f"Fan '{spec}' matches several chips; use chip/fanN. Available: {available}"
        )
    raise RuntimeError(f"Fan '{spec}' was not found. Available: {available}")


def resolve_fan_sensors(args: argparse.Namespace) -> FanSensors:
    fans = hwmon_fans()
    pump = resolve_fan(args.pump_fan, fans)
    fan = resolve_fan(args.fan, fans, exclude=pump)
    return FanSensors(pump, fan, args.pump_max_rpm, args.fan_max_rpm)


def print_fans() -> int:
    fans = hwmon_fans()
    if not fans:
        print("No fan sensors found under /sys/class/hwmon")
        return 1
    for name, path in fans.items():
        rpm = read_number(str(path))
        print(f"{name}: {'--' if rpm is None else f'{rpm:.0f}'} RPM")
    return 0


def fan_rpm(path: Path | None) -> float | None:
    return read_number(str(path)) if path else None


def rpm_pct(rpm: float | None, max_rpm: float) -> float | None:
    return None if rpm is None else min(100.0, rpm / max_rpm * 100.0)


def fmt_temp(value: float | None) -> str:
    return "--C" if value is None else f"{value:.0f}C"


def fmt_pct(value: float | None) -> str:
    return "--%" if value is None else f"{value:.0f}%"


def fmt_rpm(value: float | None) -> str:
    return "-- RPM" if value is None else f"{value:.0f} RPM"


def load_font(paths: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = first_existing(paths)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


FONT_TITLE = load_font(FONT_CANDIDATES_BOLD, 25)
FONT_LABEL = load_font(FONT_CANDIDATES_BOLD, 17)
FONT_VALUE = load_font(FONT_CANDIDATES_REGULAR, 18)
FONT_SMALL = load_font(FONT_CANDIDATES_REGULAR, 14)
FONT_BIG = load_font(FONT_CANDIDATES_BOLD, 30)
FONT_CP_LABEL = load_font(FONT_CANDIDATES_BOLD, 15)
FONT_CP_VALUE = load_font(FONT_CANDIDATES_REGULAR, 16)

CP_BG = (6, 6, 12)
CP_SCANLINE = (14, 14, 24)
CP_YELLOW = (252, 238, 10)
CP_CYAN = (0, 240, 255)
CP_RED = (255, 0, 60)
CP_TEXT = (226, 226, 214)
CP_SEGMENT_OFF = (38, 36, 14)
CP_ROW_Y = tuple(100 + 44 * index for index in range(7))
CP_DYNAMIC_REGIONS = (
    (198, 44, 300, 86),
    *(
        region
        for y in CP_ROW_Y
        for region in ((140, y - 2, 300, y + 20), (22, y + 21, 299, y + 33))
    ),
    (22, 434, 102, 456),
    (140, 434, 228, 456),
)


def draw_bar(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    h: int,
    pct: float | None,
    color: tuple[int, int, int],
) -> None:
    draw.rounded_rectangle(
        (x, y, x + w, y + h),
        radius=5,
        fill=(31, 37, 46),
        outline=(61, 70, 84),
        width=1,
    )
    if pct is not None:
        fill_w = max(4, min(w, int(w * pct / 100.0)))
        draw.rounded_rectangle((x, y, x + fill_w, y + h), radius=5, fill=color)


def draw_metric(
    draw: ImageDraw.ImageDraw,
    y: int,
    label: str,
    value: str,
    pct: float | None,
    color: tuple[int, int, int],
) -> None:
    draw.text((22, y), label, fill=(222, 230, 238), font=FONT_LABEL)
    draw_right(draw, 296, y - 2, value, FONT_VALUE, (255, 255, 255))
    draw_bar(draw, 22, y + 28, 276, 14, pct, color)


def render_default(_fans: FanSensors) -> Image.Image:
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    cpu_temp = cpu_temp_c()
    gpu_busy, vram_pct, gpu_temp = gpu_stats()
    load1, _, _ = os.getloadavg()
    uptime = int(time.time() - psutil.boot_time())
    uptime_h = uptime // 3600
    uptime_m = (uptime % 3600) // 60

    img = Image.new("RGB", (WIDTH, HEIGHT), (10, 14, 20))
    draw = ImageDraw.Draw(img)

    for y in range(HEIGHT):
        shade = int(24 + 18 * y / HEIGHT)
        draw.line((0, y, WIDTH, y), fill=(8, shade, 30))

    draw.rounded_rectangle(
        (12, 12, 308, 468), radius=10, outline=(74, 92, 112), width=2
    )
    draw.text((22, 24), "Linux Monitor", fill=(255, 255, 255), font=FONT_TITLE)
    draw.text((22, 56), socket.gethostname(), fill=(145, 204, 255), font=FONT_SMALL)
    draw_right(draw, 296, 48, time.strftime("%H:%M"), FONT_BIG, (255, 255, 255))

    draw_metric(
        draw, 104, "CPU", f"{cpu:.0f}%  {fmt_temp(cpu_temp)}", cpu, (73, 177, 255)
    )
    draw_metric(
        draw,
        165,
        "RAM",
        f"{mem.percent:.0f}%  {mem.used / 2**30:.1f}G",
        mem.percent,
        (109, 223, 139),
    )
    draw_metric(
        draw,
        226,
        "GPU",
        f"{fmt_pct(gpu_busy)}  {fmt_temp(gpu_temp)}",
        gpu_busy,
        (255, 182, 80),
    )
    draw_metric(draw, 287, "VRAM", fmt_pct(vram_pct), vram_pct, (214, 138, 255))
    draw_metric(
        draw, 348, "Disk", f"{disk.percent:.0f}%  /", disk.percent, (255, 111, 133)
    )

    draw.line((22, 422, 298, 422), fill=(74, 92, 112), width=1)
    draw.text((24, 436), f"load {load1:.2f}", fill=(210, 218, 226), font=FONT_SMALL)
    draw.text((142, 436), f"up {uptime_h}h {uptime_m}m", fill=(210, 218, 226), font=FONT_SMALL)
    draw.text((244, 436), "native", fill=(145, 204, 255), font=FONT_SMALL)
    return img


def draw_segment_bar(
    draw: ImageDraw.ImageDraw,
    y: int,
    pct: float | None,
    color: tuple[int, int, int],
) -> None:
    segment_w, gap = 4, 2
    segments = 276 // (segment_w + gap)
    lit = 0 if pct is None else round(segments * max(0.0, min(pct, 100.0)) / 100.0)
    for index in range(segments):
        x = 22 + index * (segment_w + gap)
        fill = color if index < lit else CP_SEGMENT_OFF
        draw.rectangle((x, y, x + segment_w - 1, y + 10), fill=fill)


def draw_cp_row(
    draw: ImageDraw.ImageDraw,
    y: int,
    label: str,
    value: str,
    pct: float | None,
    color: tuple[int, int, int],
    alert_at: float = 90.0,
) -> None:
    alert = pct is not None and pct >= alert_at
    draw.text((22, y), label, fill=CP_YELLOW, font=FONT_CP_LABEL)
    draw_right(draw, 296, y - 1, value, FONT_CP_VALUE, CP_RED if alert else CP_TEXT)
    draw_segment_bar(draw, y + 21, pct, CP_RED if alert else color)


def render_cyberpunk(fans: FanSensors) -> Image.Image:
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    cpu_temp = cpu_temp_c()
    gpu_busy, vram_pct, gpu_temp = gpu_stats()
    pump_rpm = fan_rpm(fans.pump)
    case_rpm = fan_rpm(fans.fan)

    img = Image.new("RGB", (WIDTH, HEIGHT), CP_BG)
    draw = ImageDraw.Draw(img)
    for y in range(0, HEIGHT, 3):
        draw.line((0, y, WIDTH, y), fill=CP_SCANLINE)

    draw.polygon(
        ((12, 12), (284, 12), (308, 36), (308, 468), (36, 468), (12, 444)),
        outline=CP_YELLOW,
        width=2,
    )
    draw.polygon(((12, 12), (196, 12), (180, 40), (12, 40)), fill=CP_YELLOW)
    draw.text((22, 16), "SYS//MONITOR", fill=CP_BG, font=FONT_LABEL)
    draw.text((22, 56), socket.gethostname().upper(), fill=CP_CYAN, font=FONT_SMALL)
    draw_right(draw, 296, 48, time.strftime("%H:%M"), FONT_BIG, CP_CYAN)
    draw.line((22, 88, 298, 88), fill=CP_RED, width=1)
    draw.rectangle((22, 86, 70, 90), fill=CP_RED)

    rows = (
        ("CPU", f"{cpu:.0f}%", cpu, CP_CYAN, 90.0),
        ("CPU TEMP", fmt_temp(cpu_temp), cpu_temp, CP_YELLOW, 80.0),
        ("RAM", f"{mem.percent:.0f}%  {mem.used / 2**30:.1f}G", mem.percent, CP_CYAN, 90.0),
        ("GPU", f"{fmt_pct(gpu_busy)}  {fmt_temp(gpu_temp)}", gpu_busy, CP_CYAN, 90.0),
        ("VRAM", fmt_pct(vram_pct), vram_pct, CP_CYAN, 90.0),
        ("PUMP", fmt_rpm(pump_rpm), rpm_pct(pump_rpm, fans.pump_max_rpm), CP_YELLOW, 101.0),
        ("FAN", fmt_rpm(case_rpm), rpm_pct(case_rpm, fans.fan_max_rpm), CP_YELLOW, 101.0),
    )
    for y, row in zip(CP_ROW_Y, rows):
        draw_cp_row(draw, y, *row)

    draw_cp_footer(draw)
    return img


def draw_cp_footer(draw: ImageDraw.ImageDraw) -> None:
    load1, _, _ = os.getloadavg()
    uptime = int(time.time() - psutil.boot_time())
    draw.line((22, 422, 298, 422), fill=CP_YELLOW, width=1)
    draw.text((24, 436), f"load {load1:.2f}", fill=CP_CYAN, font=FONT_SMALL)
    draw.text(
        (142, 436), f"up {uptime // 3600}h {uptime % 3600 // 60}m", fill=CP_CYAN, font=FONT_SMALL
    )
    draw.text((244, 436), "2077", fill=CP_RED, font=FONT_SMALL)


RENDERERS = {"default": render_default, "cyberpunk": render_cyberpunk}
DYNAMIC_REGIONS = {"default": DEFAULT_DYNAMIC_REGIONS, "cyberpunk": CP_DYNAMIC_REGIONS}


def render_frame(theme: str, fans: FanSensors) -> Image.Image:
    return RENDERERS[theme](fans)


def run_monitor(args: argparse.Namespace, fans: FanSensors) -> int:
    running = True

    def stop(_signum, _frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    device = resolve_device(args.device)
    lcd = LcdCommRevA(com_port=device, display_width=WIDTH, display_height=HEIGHT)
    lcd.initialize_comm()
    lcd.screen_on()
    lcd.set_brightness(args.brightness)
    orientation = Orientation.REVERSE_PORTRAIT if args.rotate_180 else Orientation.PORTRAIT
    lcd.set_orientation(orientation)

    psutil.cpu_percent(interval=None)
    painted = False

    while running:
        start = time.monotonic()
        frame = render_frame(args.theme, fans)
        if not painted:
            lcd.paint(frame)
            painted = True
        else:
            for region in DYNAMIC_REGIONS[args.theme]:
                lcd.paint(frame.crop(region), pos=(region[0], region[1]))
        elapsed = time.monotonic() - start
        time.sleep(max(0.1, args.interval - elapsed))

    lcd.close_serial()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Native Linux monitor for TURZX/Turing 3.5-inch USB displays."
    )
    parser.add_argument(
        "--interval", type=float, default=1.0, help="Refresh interval in seconds."
    )
    parser.add_argument(
        "--brightness", type=int, default=100, help="Display brightness from 0 to 100."
    )
    parser.add_argument(
        "--device",
        default="AUTO",
        help="Serial device path, or AUTO to detect the TURZX screen.",
    )
    parser.add_argument(
        "--rotate-180",
        action="store_true",
        help="Rotate the UI for an upside-down mounted display.",
    )
    parser.add_argument(
        "--theme", choices=THEMES, default="default", help="Dashboard layout."
    )
    parser.add_argument(
        "--pump-fan",
        default="AUTO",
        help="Pump fan sensor such as fan6 or it8689/fan6, AUTO for the fastest fan, or NONE.",
    )
    parser.add_argument(
        "--fan",
        default="AUTO",
        help="Case fan sensor such as fan2, AUTO for the fastest non-pump fan, or NONE.",
    )
    parser.add_argument(
        "--pump-max-rpm",
        type=float,
        default=DEFAULT_MAX_RPM,
        help="RPM shown as a full pump bar.",
    )
    parser.add_argument(
        "--fan-max-rpm",
        type=float,
        default=DEFAULT_MAX_RPM,
        help="RPM shown as a full fan bar.",
    )
    parser.add_argument(
        "--list-fans",
        action="store_true",
        help="Print detected fan sensors with their current RPM and exit.",
    )
    parser.add_argument(
        "--preview",
        metavar="PATH",
        help="Render one frame to an image file instead of using the display.",
    )
    parser.add_argument(
        "--print-device",
        action="store_true",
        help="Print the detected display device and exit.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(args)

    if args.print_device:
        return print_detected_device(args.device)

    if args.list_fans:
        return print_fans()

    fans = resolve_fan_sensors(args)
    if args.preview:
        render_frame(args.theme, fans).save(args.preview)
        return 0

    try:
        return run_monitor(args, fans)
    except Exception as exc:
        print(f"turzx-native-monitor: {exc}", file=sys.stderr)
        raise
