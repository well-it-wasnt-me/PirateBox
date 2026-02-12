#!/usr/bin/env python3
"""PirateBox e-Paper status display, because blinking LEDs are too optimistic."""

from __future__ import annotations

import argparse
import importlib
import os
import socket
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from PIL import Image, ImageDraw, ImageFont

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_LOGO = ROOT_DIR / "app" / "static" / "images" / "PirateBoxLogo2.png"
DEFAULT_BUTTON_PINS = "5,6,13,19"
FOOTER_HINTS = ("K1 STAT", "K2 NET", "K3 BOX", "K4 SLEEP")


@dataclass
class DisplayDriver:
    """Thin wrapper for whatever EPD driver woke up today."""
    epd: object
    width: int
    height: int
    init: Callable[[], None]
    sleep: Callable[[], None]
    clear: Callable[[], None]
    draw: Callable[[Image.Image, bool], None]


def _debug(enabled: bool, message: str) -> None:
    """Emit debug output when someone explicitly asked for it."""
    if enabled:
        print(f"[epaper] {message}", file=sys.stderr, flush=True)


def _load_driver(preferred: str, debug: bool = False) -> DisplayDriver:
    """Load the requested EPD driver or die trying."""
    errors: list[str] = []

    def _record(exc: Exception, label: str) -> None:
        """Remember an error and log it when debug is enabled."""
        errors.append(f"{label}: {exc.__class__.__name__}: {exc}")
        _debug(debug, f"{label} failed: {exc.__class__.__name__}: {exc}")

    def _try_rpi_epd2in7() -> Optional[DisplayDriver]:
        """Attempt to initialize the rpi_epd2in7 driver."""
        _debug(debug, "Trying driver: rpi_epd2in7")
        try:
            from rpi_epd2in7.epd import EPD  # type: ignore
        except Exception as exc:
            _record(exc, "rpi_epd2in7 import")
            return None

        try:
            epd = EPD()
            epd.init()

            def _clear() -> None:
                """Clear the display buffer for rpi_epd2in7."""
                if hasattr(epd, "clear"):
                    epd.clear()
                elif hasattr(epd, "clear_screen"):
                    epd.clear_screen()

            def _draw(image: Image.Image, full: bool) -> None:
                """Draw an image using whatever rpi_epd2in7 exposes."""
                if full and hasattr(epd, "display_frame"):
                    epd.display_frame(image)
                elif hasattr(epd, "smart_update"):
                    epd.smart_update(image)
                elif hasattr(epd, "display_frame"):
                    epd.display_frame(image)
                else:
                    raise RuntimeError("EPD driver has no display method")

            return DisplayDriver(
                epd=epd,
                width=int(getattr(epd, "width", 176)),
                height=int(getattr(epd, "height", 264)),
                init=epd.init,
                sleep=epd.sleep,
                clear=_clear,
                draw=_draw,
            )
        except Exception as exc:
            _record(exc, "rpi_epd2in7 init")
            return None

    def _try_waveshare_epd() -> Optional[DisplayDriver]:
        """Attempt to initialize Waveshare drivers with smart fallbacks."""
        module_hint = os.getenv("PIRATEBOX_EPD_WAVESHARE_MODULE", "").strip()
        module_map = {
            "epd2in7": "epd2in7",
            "epd2in7_v2": "epd2in7_V2",
            "v2": "epd2in7_V2",
        }
        if module_hint:
            module_name = module_map.get(module_hint.lower(), module_hint)
            module_names = [module_name]
        else:
            module_names = ["epd2in7", "epd2in7_V2"]

        _debug(debug, f"Trying driver: waveshare_epd ({', '.join(module_names)})")

        for module_name in module_names:
            try:
                module = importlib.import_module(f"waveshare_epd.{module_name}")
            except Exception as exc:
                _record(exc, f"waveshare_epd {module_name} import")
                continue

            try:
                epd = module.EPD()  # type: ignore[attr-defined]
                epd.init()
                partial_ready = False

                def _pick_method(names: tuple[str, ...]) -> Optional[Callable[..., None]]:
                    """Find the first callable method in a list of names."""
                    for name in names:
                        method = getattr(epd, name, None)
                        if callable(method):
                            return method
                    return None

                partial_init = _pick_method(
                    ("init_partial", "init_part", "Init_Partial", "init_fast", "Init_Fast")
                )
                partial_display = _pick_method(
                    ("display_partial", "display_Partial", "display_fast", "display_Fast", "displayPartial")
                )
                if partial_display:
                    _debug(debug, f"Partial refresh supported via {partial_display.__name__}")
                else:
                    _debug(debug, "Partial refresh not supported; using full refresh only")

                def _clear() -> None:
                    """Clear the Waveshare display to white."""
                    epd.Clear(0xFF)

                def _draw(image: Image.Image, full: bool) -> None:
                    """Draw using full or partial refresh when available."""
                    nonlocal partial_ready
                    buffer = epd.getbuffer(image)
                    if not full and partial_display:
                        if partial_init and not partial_ready:
                            partial_init()
                            partial_ready = True
                        partial_display(buffer)
                        return
                    if full and partial_ready:
                        epd.init()
                        partial_ready = False
                    epd.display(buffer)

                _debug(debug, f"Using waveshare module: {module_name}")
                return DisplayDriver(
                    epd=epd,
                    width=int(getattr(epd, "width", 176)),
                    height=int(getattr(epd, "height", 264)),
                    init=epd.init,
                    sleep=epd.sleep,
                    clear=_clear,
                    draw=_draw,
                )
            except Exception as exc:
                _record(exc, f"waveshare_epd {module_name} init")
                continue

        return None

    def _fail() -> None:
        """Raise a helpful error message after driver attempts fail."""
        hint_lines = []
        joined = "\n".join(errors)
        if "Cannot determine SOC peripheral base address" in joined:
            hint_lines.append(
                "Detected a GPIO backend that cannot read the Pi SoC base; on Pi 5 use rpi-lgpio (python3-rpi-lgpio) instead of RPi.GPIO."
            )
        if "RPi" in joined or "RPi.GPIO" in joined:
            hint_lines.append(
                "Missing RPi.GPIO (try: sudo apt install python3-rpi.gpio or pip install RPi.GPIO)"
            )
        if "spidev" in joined:
            hint_lines.append(
                "Missing spidev (try: sudo apt install python3-spidev or pip install spidev)"
            )
        if not Path("/proc/device-tree").exists():
            hint_lines.append(
                "/proc/device-tree is missing; you're likely in a container or non-Pi host."
            )
        if sys.version_info >= (3, 13) and hint_lines:
            hint_lines.append(
                "Python 3.13 may not have GPIO wheels yet; try system python 3.11/3.12."
            )
        detail = "\n".join(f"- {line}" for line in errors) if errors else "- (no details)"
        hints = "\n".join(f"- {line}" for line in hint_lines) if hint_lines else "- none"
        raise SystemExit(
            "No supported EPD driver found.\n"
            "Details:\n"
            f"{detail}\n"
            "Hints:\n"
            f"{hints}"
        )

    preferred = preferred.lower().strip()
    if preferred in {"rpi_epd2in7", "rpi"}:
        driver = _try_rpi_epd2in7()
        if driver:
            _debug(debug, f"Using driver: rpi_epd2in7 ({driver.width}x{driver.height})")
            return driver
        _fail()
    if preferred in {"waveshare_epd", "waveshare"}:
        driver = _try_waveshare_epd()
        if driver:
            _debug(debug, f"Using driver: waveshare_epd ({driver.width}x{driver.height})")
            return driver
        _fail()

    for candidate in (_try_rpi_epd2in7, _try_waveshare_epd):
        driver = candidate()
        if driver:
            _debug(debug, f"Using driver: {driver.epd.__class__.__name__} ({driver.width}x{driver.height})")
            return driver

    _fail()


@dataclass
class ButtonConfig:
    pins: list[int]
    pull_up: bool


def _parse_buttons(raw: str) -> ButtonConfig:
    """Parse button pin list and pull-up config from env or args."""
    raw = raw.strip()
    if not raw or raw.lower() in {"none", "off", "0", "false"}:
        return ButtonConfig(pins=[], pull_up=os.getenv("PIRATEBOX_EPD_BUTTON_PULL_UP", "1") != "0")

    pins: list[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        pins.append(int(chunk))
    pull_up = os.getenv("PIRATEBOX_EPD_BUTTON_PULL_UP", "1") != "0"
    return ButtonConfig(pins=pins, pull_up=pull_up)


@dataclass
class State:
    """Track the current page and sleep state for the display."""
    page: int = 0
    sleeping: bool = False
    force_refresh: bool = False

    def set_page(self, page: int, total: int) -> None:
        """Set the page within bounds and force a refresh."""
        if 0 <= page < total:
            self.page = page
        self.force_refresh = True

    def next_page(self, total: int) -> None:
        """Advance to the next page, wrapping around."""
        self.page = (self.page + 1) % total
        self.force_refresh = True

    def prev_page(self, total: int) -> None:
        """Go back one page, wrapping around."""
        self.page = (self.page - 1) % total
        self.force_refresh = True

    def toggle_sleep(self, driver: DisplayDriver) -> None:
        """Toggle sleep state and reset the display if waking up."""
        if self.sleeping:
            driver.init()
            self.sleeping = False
        else:
            driver.sleep()
            self.sleeping = True
        self.force_refresh = True


@dataclass
class ButtonHandler:
    """Map button presses to state transitions."""
    state: State
    total_pages: int
    driver: DisplayDriver

    def on_status(self) -> None:
        """Jump to the status page."""
        self.state.set_page(0, self.total_pages)

    def on_network(self) -> None:
        """Jump to the network page."""
        self.state.set_page(1, self.total_pages)

    def on_piratebox(self) -> None:
        """Jump to the PirateBox stats page."""
        self.state.set_page(2, self.total_pages)

    def on_sleep(self) -> None:
        """Put the display to sleep or wake it back up."""
        self.state.toggle_sleep(self.driver)


class ButtonWatcher:
    """Watch GPIO buttons and invoke handlers with minimal drama."""
    def __init__(self, config: ButtonConfig, handler: ButtonHandler):
        self.config = config
        self.handler = handler
        self._gpio = None
        self._buttons = []
        self._last_states: list[int] = []

        if not config.pins:
            return

        try:
            from gpiozero import Button  # type: ignore

            for pin in config.pins:
                btn = Button(pin, pull_up=config.pull_up, bounce_time=0.05)
                self._buttons.append(btn)

            callbacks = [
                handler.on_status,
                handler.on_network,
                handler.on_piratebox,
                handler.on_sleep,
            ]
            for btn, cb in zip(self._buttons, callbacks):
                btn.when_pressed = cb
            return
        except Exception:
            pass

        try:
            import RPi.GPIO as GPIO  # type: ignore

            self._gpio = GPIO
            GPIO.setmode(GPIO.BCM)
            for pin in config.pins:
                GPIO.setup(
                    pin,
                    GPIO.IN,
                    pull_up_down=GPIO.PUD_UP if config.pull_up else GPIO.PUD_DOWN,
                )
            self._last_states = [GPIO.input(pin) for pin in config.pins]
        except Exception:
            self._gpio = None

    def poll(self) -> None:
        """Poll buttons when using the RPi.GPIO fallback."""
        if not self._gpio:
            return

        callbacks = [
            self.handler.on_status,
            self.handler.on_network,
            self.handler.on_piratebox,
            self.handler.on_sleep,
        ]
        for idx, pin in enumerate(self.config.pins):
            current = self._gpio.input(pin)
            if self._last_states[idx] == 1 and current == 0:
                callbacks[idx]()
            self._last_states[idx] = current

    def close(self) -> None:
        """Release GPIO resources when exiting."""
        if self._gpio:
            self._gpio.cleanup()


@dataclass
class Stats:
    """Snapshot of system and PirateBox stats at a point in time."""
    cpu_usage: float
    cpu_temp: Optional[float]
    mem_used: int
    mem_total: int
    disk_used: int
    disk_total: int
    uptime: str
    ip: str
    files: int
    threads: int
    posts: int
    timestamp: str


@dataclass
class RefreshPolicy:
    """Refresh thresholds to avoid ghosting and needless redraws."""
    on_change: bool
    cpu_delta: float
    temp_delta: float
    mem_delta: float
    disk_delta: float
    count_delta: int
    ip_change: bool


def _read_env_float(name: str, default: float) -> float:
    """Read a float from the environment or return the default."""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _read_env_int(name: str, default: int) -> int:
    """Read an int from the environment or return the default."""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _parse_refresh_policy() -> RefreshPolicy:
    """Build a refresh policy from environment settings."""
    return RefreshPolicy(
        on_change=os.getenv("PIRATEBOX_EPD_REFRESH_ON_CHANGE", "1") != "0",
        cpu_delta=_read_env_float("PIRATEBOX_EPD_REFRESH_CPU_DELTA", 5.0),
        temp_delta=_read_env_float("PIRATEBOX_EPD_REFRESH_TEMP_DELTA", 1.0),
        mem_delta=_read_env_float("PIRATEBOX_EPD_REFRESH_MEM_DELTA", 2.0),
        disk_delta=_read_env_float("PIRATEBOX_EPD_REFRESH_DISK_DELTA", 1.0),
        count_delta=_read_env_int("PIRATEBOX_EPD_REFRESH_COUNT_DELTA", 1),
        ip_change=os.getenv("PIRATEBOX_EPD_REFRESH_IP", "1") != "0",
    )


def _percent(value: int, total: int) -> float:
    """Return a percent value while pretending division is harmless."""
    if total <= 0:
        return 0.0
    return (float(value) / float(total)) * 100.0


def _delta_exceeds(delta: float, threshold: float) -> bool:
    """Check whether a delta crosses the configured threshold."""
    if threshold <= 0:
        return delta != 0.0
    return abs(delta) >= threshold


def _stats_changed(prev: Optional[Stats], curr: Stats, policy: RefreshPolicy) -> bool:
    """Decide whether the screen should refresh based on stats deltas."""
    if prev is None:
        return True
    if _delta_exceeds(curr.cpu_usage - prev.cpu_usage, policy.cpu_delta):
        return True
    if curr.cpu_temp is None or prev.cpu_temp is None:
        if curr.cpu_temp != prev.cpu_temp:
            return True
    elif _delta_exceeds(curr.cpu_temp - prev.cpu_temp, policy.temp_delta):
        return True
    mem_delta = _percent(curr.mem_used, curr.mem_total) - _percent(prev.mem_used, prev.mem_total)
    if _delta_exceeds(mem_delta, policy.mem_delta):
        return True
    disk_delta = _percent(curr.disk_used, curr.disk_total) - _percent(prev.disk_used, prev.disk_total)
    if _delta_exceeds(disk_delta, policy.disk_delta):
        return True
    if policy.count_delta > 0:
        if abs(curr.files - prev.files) >= policy.count_delta:
            return True
        if abs(curr.threads - prev.threads) >= policy.count_delta:
            return True
        if abs(curr.posts - prev.posts) >= policy.count_delta:
            return True
    if policy.ip_change and curr.ip != prev.ip:
        return True
    return False


def _read_cpu_usage(prev: Optional[tuple[int, int]]) -> tuple[float, tuple[int, int]]:
    """Compute CPU usage from /proc/stat deltas."""
    with open("/proc/stat", "r", encoding="utf-8") as handle:
        parts = handle.readline().split()[1:]
    values = [int(v) for v in parts]
    idle = values[3] + values[4]
    total = sum(values)

    if prev is None:
        return 0.0, (total, idle)

    prev_total, prev_idle = prev
    delta_total = total - prev_total
    delta_idle = idle - prev_idle
    usage = 0.0 if delta_total == 0 else (1.0 - delta_idle / delta_total) * 100
    return usage, (total, idle)


def _read_cpu_temp() -> Optional[float]:
    """Read CPU temperature if the kernel exposes it."""
    path = Path("/sys/class/thermal/thermal_zone0/temp")
    if not path.exists():
        return None
    raw = path.read_text().strip()
    try:
        return float(raw) / 1000.0
    except ValueError:
        return None


def _read_mem() -> tuple[int, int]:
    """Return used and total memory in bytes."""
    total = 0
    available = 0
    with open("/proc/meminfo", "r", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("MemTotal"):
                total = int(line.split()[1]) * 1024
            elif line.startswith("MemAvailable"):
                available = int(line.split()[1]) * 1024
    used = max(total - available, 0)
    return used, total


def _read_disk(path: Path) -> tuple[int, int]:
    """Return used and total disk bytes for the given path."""
    stat = os.statvfs(path)
    total = stat.f_frsize * stat.f_blocks
    used = total - stat.f_frsize * stat.f_bfree
    return used, total


def _read_uptime() -> str:
    """Return uptime formatted for small screens."""
    with open("/proc/uptime", "r", encoding="utf-8") as handle:
        seconds = float(handle.read().split()[0])
    minutes, _ = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _read_ip() -> str:
    """Attempt to find a local IP without committing to the truth."""
    try:
        result = subprocess.check_output(["hostname", "-I"], text=True).strip()
        if result:
            return result.split()[0]
    except Exception:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "unknown"


def _read_counts(db_path: Path) -> tuple[int, int, int]:
    """Count files, threads, and posts in the SQLite DB."""
    if not db_path.exists():
        return 0, 0, 0
    try:
        with sqlite3.connect(db_path) as conn:
            files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            threads = conn.execute("SELECT COUNT(*) FROM forum_threads").fetchone()[0]
            posts = conn.execute("SELECT COUNT(*) FROM forum_posts").fetchone()[0]
        return int(files), int(threads), int(posts)
    except sqlite3.Error:
        return 0, 0, 0


def _format_bytes(value: int) -> str:
    """Format byte counts without crying about base-2 vs base-10."""
    step = 1024.0
    size = float(value)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < step:
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= step
    return f"{size:.1f}PB"


def _load_logo(path: Path, max_size: tuple[int, int]) -> Optional[Image.Image]:
    """Load and resize the logo for the EPD."""
    if not path.exists():
        return None
    try:
        logo = Image.open(path)
    except Exception:
        return None
    logo.thumbnail(max_size, Image.LANCZOS)
    return logo.convert("1")


def _prepare_canvas(width: int, height: int) -> Image.Image:
    """Create a blank 1-bit canvas."""
    return Image.new("1", (width, height), 255)


@dataclass
class Fonts:
    """Bundle fonts for reuse across rendering functions."""
    tiny: ImageFont.ImageFont
    small: ImageFont.ImageFont
    medium: ImageFont.ImageFont
    large: ImageFont.ImageFont
    huge: ImageFont.ImageFont


@dataclass
class Layout:
    """Layout metrics for header, footer, and margins."""
    margin: int
    gutter: int
    header_h: int
    footer_h: int


def _font_height(font: ImageFont.ImageFont) -> int:
    """Measure font height with a fallback for older PIL."""
    try:
        bbox = font.getbbox("Ag")
        return max(1, int(bbox[3] - bbox[1]))
    except Exception:
        return font.getsize("Ag")[1]


def _text_width(font: ImageFont.ImageFont, text: str) -> int:
    """Measure text width with a fallback for older PIL."""
    try:
        bbox = font.getbbox(text)
        return max(1, int(bbox[2] - bbox[0]))
    except Exception:
        return font.getsize(text)[0]


def _load_fonts(width: int, height: int) -> Fonts:
    """Load fonts with reasonable fallbacks and size scaling."""
    base = max(1.0, min(width, height) / 176.0)
    sizes = {
        "tiny": max(9, int(10 * base)),
        "small": max(11, int(12 * base)),
        "medium": max(13, int(15 * base)),
        "large": max(16, int(20 * base)),
        "huge": max(20, int(26 * base)),
    }
    font_hint = os.getenv("PIRATEBOX_EPD_FONT", "").strip()
    candidates = [
        font_hint,
        "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]

    def _try_font(size: int) -> ImageFont.ImageFont:
        """Load the first available font or fall back to default."""
        for path in candidates:
            if not path:
                continue
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
        return ImageFont.load_default()

    return Fonts(
        tiny=_try_font(sizes["tiny"]),
        small=_try_font(sizes["small"]),
        medium=_try_font(sizes["medium"]),
        large=_try_font(sizes["large"]),
        huge=_try_font(sizes["huge"]),
    )


def _make_layout(fonts: Fonts, width: int, height: int) -> Layout:
    """Compute layout metrics based on screen size."""
    base = max(1.0, min(width, height) / 176.0)
    margin = max(4, int(6 * base))
    gutter = max(3, int(4 * base))
    header_h = _font_height(fonts.medium) + gutter * 2
    footer_h = _font_height(fonts.tiny) + gutter * 2
    return Layout(margin=margin, gutter=gutter, header_h=header_h, footer_h=footer_h)


def _draw_header(
    draw: ImageDraw.ImageDraw,
    width: int,
    title: str,
    timestamp: str,
    fonts: Fonts,
    layout: Layout,
) -> None:
    """Draw the header bar with title and timestamp."""
    draw.rectangle((0, 0, width, layout.header_h), fill=0)
    title_y = (layout.header_h - _font_height(fonts.medium)) // 2
    draw.text((layout.margin, title_y), title, font=fonts.medium, fill=255)
    if timestamp:
        stamp_w = _text_width(fonts.small, timestamp)
        stamp_y = (layout.header_h - _font_height(fonts.small)) // 2
        draw.text((width - layout.margin - stamp_w, stamp_y), timestamp, font=fonts.small, fill=255)


def _draw_footer(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    fonts: Fonts,
    layout: Layout,
    hints: tuple[str, ...] = FOOTER_HINTS,
) -> None:
    """Draw the footer with button hints."""
    y0 = height - layout.footer_h
    draw.rectangle((0, y0, width, height), fill=0)
    text = "  ".join(hints)
    text_y = y0 + (layout.footer_h - _font_height(fonts.tiny)) // 2
    draw.text((layout.margin, text_y), text, font=fonts.tiny, fill=255)


def _draw_labeled_bar(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    label: str,
    value_text: str,
    percent: float,
    fonts: Fonts,
    layout: Layout,
) -> int:
    """Draw a labeled usage bar and return the next y coordinate."""
    draw.text((x, y), label, font=fonts.small, fill=0)
    if value_text:
        value_w = _text_width(fonts.small, value_text)
        draw.text((x + width - value_w, y), value_text, font=fonts.small, fill=0)
    y += _font_height(fonts.small) + 1
    bar_h = max(6, _font_height(fonts.tiny))
    draw.rectangle((x, y, x + width, y + bar_h), outline=0, fill=255)
    clamped = max(0.0, min(percent, 100.0))
    fill_w = int(width * clamped / 100.0)
    if fill_w:
        draw.rectangle((x, y, x + fill_w, y + bar_h), fill=0)
    return y + bar_h + layout.gutter


def _center_text(draw: ImageDraw.ImageDraw, width: int, y: int, text: str, font: ImageFont.ImageFont) -> int:
    """Center text horizontally and return the next y coordinate."""
    text_w = _text_width(font, text)
    x = max(0, (width - text_w) // 2)
    draw.text((x, y), text, font=font, fill=0)
    return y + _font_height(font) + 1


def _render_status(
    image: Image.Image,
    stats: Stats,
    logo: Optional[Image.Image],
    fonts: Fonts,
    layout: Layout,
) -> None:
    """Render the status page with CPU, mem, disk, and uptime."""
    draw = ImageDraw.Draw(image)
    _draw_header(draw, image.width, "STATUS", stats.timestamp, fonts, layout)
    _draw_footer(draw, image.width, image.height, fonts, layout)

    x = layout.margin
    y = layout.header_h + layout.gutter
    right_edge = image.width - layout.margin
    if logo:
        logo_x = image.width - layout.margin - logo.width
        logo_y = y
        image.paste(logo, (logo_x, logo_y))
        right_edge = max(x + 10, logo_x - layout.gutter)

    col_width = max(10, right_edge - x)
    cpu_value = f"{stats.cpu_usage:.0f}%"
    if stats.cpu_temp is not None:
        cpu_value = f"{stats.cpu_usage:.0f}% {stats.cpu_temp:.1f}C"
    y = _draw_labeled_bar(
        draw,
        x,
        y,
        col_width,
        "CPU",
        cpu_value,
        stats.cpu_usage,
        fonts,
        layout,
    )
    mem_percent = 0.0 if stats.mem_total == 0 else (stats.mem_used / stats.mem_total) * 100
    y = _draw_labeled_bar(
        draw,
        x,
        y,
        col_width,
        "MEM",
        f"{_format_bytes(stats.mem_used)}/{_format_bytes(stats.mem_total)}",
        mem_percent,
        fonts,
        layout,
    )
    disk_percent = 0.0 if stats.disk_total == 0 else (stats.disk_used / stats.disk_total) * 100
    y = _draw_labeled_bar(
        draw,
        x,
        y,
        col_width,
        "DISK",
        f"{_format_bytes(stats.disk_used)}/{_format_bytes(stats.disk_total)}",
        disk_percent,
        fonts,
        layout,
    )

    draw.text((x, y), f"UP {stats.uptime}", font=fonts.small, fill=0)
    y += _font_height(fonts.small) + layout.gutter
    draw.text((x, y), f"IP {stats.ip}", font=fonts.small, fill=0)


def _render_network(
    image: Image.Image,
    stats: Stats,
    hostname: str,
    fonts: Fonts,
    layout: Layout,
) -> None:
    """Render the network page with host and IP info."""
    draw = ImageDraw.Draw(image)
    _draw_header(draw, image.width, "NETWORK", stats.timestamp, fonts, layout)
    _draw_footer(draw, image.width, image.height, fonts, layout)

    y = layout.header_h + layout.gutter
    draw.text((layout.margin, y), f"HOST {hostname}", font=fonts.small, fill=0)
    y += _font_height(fonts.small) + layout.gutter

    ip_text = stats.ip if stats.ip else "unknown"
    y = _center_text(draw, image.width, y, ip_text, fonts.huge)
    y += layout.gutter

    http_line = f"http://{ip_text}" if ip_text != "unknown" else "http://pirate.box"
    draw.text((layout.margin, y), f"HTTP {http_line}", font=fonts.small, fill=0)
    y += _font_height(fonts.small) + layout.gutter
    draw.text((layout.margin, y), "INTERNET: NO, BY DESIGN.", font=fonts.small, fill=0)


def _render_piratebox(image: Image.Image, stats: Stats, fonts: Fonts, layout: Layout) -> None:
    """Render the PirateBox stats page."""
    draw = ImageDraw.Draw(image)
    _draw_header(draw, image.width, "BOX", stats.timestamp, fonts, layout)
    _draw_footer(draw, image.width, image.height, fonts, layout)

    y = layout.header_h + layout.gutter
    for label, value in (("FILES", stats.files), ("THREADS", stats.threads), ("POSTS", stats.posts)):
        draw.text((layout.margin, y), label, font=fonts.small, fill=0)
        y += _font_height(fonts.small)
        draw.text((layout.margin, y), str(value), font=fonts.large, fill=0)
        y += _font_height(fonts.large) + layout.gutter

    y = min(y, image.height - layout.footer_h - _font_height(fonts.small) - layout.gutter)
    draw.text((layout.margin, y), "LOCAL ONLY. NO CLOUD.", font=fonts.small, fill=0)


PAGES = (
    ("status", _render_status),
    ("network", _render_network),
    ("piratebox", _render_piratebox),
)


def _collect_stats(db_path: Path, data_path: Path, prev_cpu: Optional[tuple[int, int]]) -> tuple[Stats, tuple[int, int]]:
    """Collect system stats and return a Stats object plus CPU state."""
    cpu_usage, cpu_state = _read_cpu_usage(prev_cpu)
    cpu_temp = _read_cpu_temp()
    mem_used, mem_total = _read_mem()
    disk_used, disk_total = _read_disk(data_path)
    uptime = _read_uptime()
    ip = _read_ip()
    files, threads, posts = _read_counts(db_path)
    timestamp = time.strftime("%H:%M")

    return (
        Stats(
            cpu_usage=cpu_usage,
            cpu_temp=cpu_temp,
            mem_used=mem_used,
            mem_total=mem_total,
            disk_used=disk_used,
            disk_total=disk_total,
            uptime=uptime,
            ip=ip,
            files=files,
            threads=threads,
            posts=posts,
            timestamp=timestamp,
        ),
        cpu_state,
    )


def main() -> None:
    """CLI entry point that drives the e-paper refresh loop."""
    parser = argparse.ArgumentParser(description="PirateBox e-Paper status screen")
    parser.add_argument("--interval", type=int, default=int(os.getenv("PIRATEBOX_EPD_INTERVAL", "30")))
    parser.add_argument("--rotate", type=int, default=int(os.getenv("PIRATEBOX_EPD_ROTATE", "0")))
    parser.add_argument("--logo", type=str, default=os.getenv("PIRATEBOX_EPD_LOGO", str(DEFAULT_LOGO)))
    parser.add_argument("--buttons", type=str, default=os.getenv("PIRATEBOX_EPD_BUTTON_PINS", DEFAULT_BUTTON_PINS))
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--driver",
        type=str,
        default=os.getenv("PIRATEBOX_EPD_DRIVER", "auto"),
        help="EPD driver to use: auto, rpi_epd2in7, waveshare_epd",
    )
    args = parser.parse_args()

    try:
        full_refresh_every = int(os.getenv("PIRATEBOX_EPD_FULL_REFRESH_EVERY", "10"))
    except ValueError:
        full_refresh_every = 10
    if full_refresh_every < 0:
        full_refresh_every = 0

    refresh_policy = _parse_refresh_policy()

    _debug(
        args.debug,
        "Starting epaper: interval="
        f"{args.interval}s rotate={args.rotate} driver={args.driver} buttons={args.buttons or 'none'} "
        f"full_refresh_every={full_refresh_every} refresh_on_change={int(refresh_policy.on_change)}",
    )
    driver = _load_driver(args.driver, debug=args.debug)
    driver.clear()

    fonts = _load_fonts(driver.width, driver.height)
    layout = _make_layout(fonts, driver.width, driver.height)

    state = State(force_refresh=True)
    handler = ButtonHandler(state=state, total_pages=len(PAGES), driver=driver)
    # Buttons: four chances to do something useful, or at least entertaining.
    buttons = ButtonWatcher(_parse_buttons(args.buttons), handler)

    logo = _load_logo(Path(args.logo), (64, 64))
    _debug(args.debug, f"Logo: {args.logo} ({'loaded' if logo else 'missing'})")

    db_path = Path(os.getenv("PIRATEBOX_DB_PATH", ROOT_DIR / "data" / "piratebox.db"))
    data_path = Path(os.getenv("PIRATEBOX_DATA_DIR", ROOT_DIR / "data"))
    _debug(args.debug, f"DB path: {db_path}")
    _debug(args.debug, f"Data path: {data_path}")

    prev_cpu: Optional[tuple[int, int]] = None
    hostname = socket.gethostname()
    loop_count = 0
    last_drawn: Optional[Stats] = None

    try:
        # Main loop: keep the paper fresh so it does not look like last week's news.
        while True:
            buttons.poll()
            if state.sleeping:
                time.sleep(args.interval)
                continue

            stats, prev_cpu = _collect_stats(db_path, data_path, prev_cpu)
            page_name, renderer = PAGES[state.page]
            _debug(args.debug, f"Render page={page_name} force={state.force_refresh} sleeping={state.sleeping}")

            if not state.force_refresh and refresh_policy.on_change:
                if not _stats_changed(last_drawn, stats, refresh_policy):
                    _debug(args.debug, "No significant change; skipping refresh")
                    time.sleep(args.interval)
                    continue

            canvas = _prepare_canvas(driver.width, driver.height)
            if page_name == "status":
                renderer(canvas, stats, logo, fonts, layout)
            elif page_name == "network":
                renderer(canvas, stats, hostname, fonts, layout)
            else:
                renderer(canvas, stats, fonts, layout)

            if args.rotate:
                canvas = canvas.rotate(args.rotate, expand=True)

            full_refresh = state.force_refresh
            if full_refresh_every:
                full_refresh = full_refresh or loop_count % full_refresh_every == 0
            driver.draw(canvas, full_refresh)
            state.force_refresh = False
            last_drawn = stats
            loop_count += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        buttons.close()
        try:
            driver.sleep()
        except Exception:
            pass


if __name__ == "__main__":
    main()
