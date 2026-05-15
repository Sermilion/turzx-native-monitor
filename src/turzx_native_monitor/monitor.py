from __future__ import annotations

import argparse
import glob
import os
import signal
import socket
import sys
import time
from pathlib import Path

import psutil
from PIL import Image, ImageDraw, ImageFont
from smartscreen_driver.lcd_comm import Orientation
from smartscreen_driver.lcd_comm_rev_a import LcdCommRevA


WIDTH = 320
HEIGHT = 480
DEFAULT_DEVICE_BY_ID = "/dev/serial/by-id/usb-Turing_UsbMonitor_USB35INCHIPSV2-if00"
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
DYNAMIC_REGIONS = (
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


def read_text(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except Exception:
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


def resolve_device(device: str) -> str:
    if device != "AUTO":
        return os.path.realpath(device)
    if os.path.exists(DEFAULT_DEVICE_BY_ID):
        return os.path.realpath(DEFAULT_DEVICE_BY_ID)
    for candidate in sorted(glob.glob("/dev/ttyACM*")):
        return candidate
    raise RuntimeError("TURZX display was not found at /dev/ttyACM*")


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


def fmt_temp(value: float | None) -> str:
    return "--C" if value is None else f"{value:.0f}C"


def fmt_pct(value: float | None) -> str:
    return "--%" if value is None else f"{value:.0f}%"


def load_font(paths: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = first_existing(paths)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


FONT_TITLE = load_font(FONT_CANDIDATES_BOLD, 25)
FONT_LABEL = load_font(FONT_CANDIDATES_BOLD, 17)
FONT_VALUE = load_font(FONT_CANDIDATES_REGULAR, 18)
FONT_SMALL = load_font(FONT_CANDIDATES_REGULAR, 14)
FONT_BIG = load_font(FONT_CANDIDATES_BOLD, 30)


def draw_bar(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    h: int,
    pct: float | None,
    color: tuple[int, int, int],
) -> None:
    draw.rounded_rectangle((x, y, x + w, y + h), radius=5, fill=(31, 37, 46), outline=(61, 70, 84), width=1)
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


def render_frame() -> Image.Image:
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

    draw.rounded_rectangle((12, 12, 308, 468), radius=10, outline=(74, 92, 112), width=2)
    draw.text((22, 24), "Linux Monitor", fill=(255, 255, 255), font=FONT_TITLE)
    draw.text((22, 56), socket.gethostname(), fill=(145, 204, 255), font=FONT_SMALL)
    draw_right(draw, 296, 48, time.strftime("%H:%M"), FONT_BIG, (255, 255, 255))

    draw_metric(draw, 104, "CPU", f"{cpu:.0f}%  {fmt_temp(cpu_temp)}", cpu, (73, 177, 255))
    draw_metric(draw, 165, "RAM", f"{mem.percent:.0f}%  {mem.used / 2**30:.1f}G", mem.percent, (109, 223, 139))
    draw_metric(draw, 226, "GPU", f"{fmt_pct(gpu_busy)}  {fmt_temp(gpu_temp)}", gpu_busy, (255, 182, 80))
    draw_metric(draw, 287, "VRAM", fmt_pct(vram_pct), vram_pct, (214, 138, 255))
    draw_metric(draw, 348, "Disk", f"{disk.percent:.0f}%  /", disk.percent, (255, 111, 133))

    draw.line((22, 422, 298, 422), fill=(74, 92, 112), width=1)
    draw.text((24, 436), f"load {load1:.2f}", fill=(210, 218, 226), font=FONT_SMALL)
    draw.text((142, 436), f"up {uptime_h}h {uptime_m}m", fill=(210, 218, 226), font=FONT_SMALL)
    draw.text((244, 436), "native", fill=(145, 204, 255), font=FONT_SMALL)
    return img


def run_monitor(args: argparse.Namespace) -> int:
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
        frame = render_frame()
        if not painted:
            lcd.paint(frame)
            painted = True
        else:
            for region in DYNAMIC_REGIONS:
                lcd.paint(frame.crop(region), pos=(region[0], region[1]))
        elapsed = time.monotonic() - start
        time.sleep(max(0.1, args.interval - elapsed))

    lcd.close_serial()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Native Linux monitor for TURZX/Turing 3.5-inch USB displays.")
    parser.add_argument("--interval", type=float, default=1.0, help="Refresh interval in seconds.")
    parser.add_argument("--brightness", type=int, default=100, help="Display brightness from 0 to 100.")
    parser.add_argument("--device", default="AUTO", help="Serial device path, or AUTO to detect the TURZX screen.")
    parser.add_argument("--rotate-180", action="store_true", help="Rotate the UI for an upside-down mounted display.")
    parser.add_argument("--preview", metavar="PATH", help="Render one frame to an image file instead of using the display.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.preview:
        render_frame().save(args.preview)
        return 0

    try:
        return run_monitor(args)
    except Exception as exc:
        print(f"turzx-native-monitor: {exc}", file=sys.stderr)
        raise
