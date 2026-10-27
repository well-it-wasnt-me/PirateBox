#!/usr/bin/env python3
"""PirateBox e-Paper status display, because blinking LEDs are too optimistic."""

from __future__ import annotations

import argparse
import importlib
import inspect
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from PIL import Image, ImageDraw, ImageFont

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_LOGO = ROOT_DIR / "app" / "static" / "images" / "PirateBoxLogo2.png"
DEFAULT_BUTTON_PINS = "5,6,13,19"
DEFAULT_PAGES = "status,network,piratebox"
HOSTAPD_CONF = Path("/etc/hostapd/hostapd.conf")
# Written by piratebox-ap-health. In /run, so a previous boot's opinion cannot leak into this one.
AP_STATUS_FILE = Path("/run/piratebox/ap-status")
SYSFS = Path("/sys")
PROC_UDP = Path("/proc/net/udp")
# The Zero edition keeps its per-boot AP verdict on the FAT partition, where any laptop can read it.
BOOT_CONFIGS = (Path("/boot/firmware/piratebox.txt"), Path("/boot/piratebox.txt"))
# While not ready, sample this often. Sampling is free; it is the refreshes the panel minds.
STARTUP_POLL = 10
PAGE_HINTS = {"status": "STAT", "network": "NET", "piratebox": "BOX", "dashboard": "DASH"}

# Drivers fetched by the installers live here, next to the regret.
VENDOR_DIR = ROOT_DIR / "vendor"
if VENDOR_DIR.is_dir() and str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))

# Canvas palette indices. Mono panels print red as black and pretend it was intentional.
BLACK = 0
RED = 1
WHITE = 255


@dataclass
class DisplayDriver:
    """Thin wrapper for whatever EPD driver woke up today."""
    epd: object
    width: int
    height: int
    init: Callable[[], None]
    sleep: Callable[[], None]
    clear: Callable[[], None]
    draw: Callable[[Image.Image, Optional[Image.Image], bool], None]
    color: bool = False
    min_refresh: int = 0


def _debug(enabled: bool, message: str) -> None:
    """Emit debug output when someone explicitly asked for it."""
    if enabled:
        print(f"[epaper] {message}", file=sys.stderr, flush=True)


def _positional_arity(func: Callable[..., object]) -> int:
    """Count positional parameters, because driver APIs are a choose-your-own-adventure."""
    try:
        params = inspect.signature(func).parameters.values()
    except (TypeError, ValueError):
        return 0
    return sum(
        1
        for param in params
        if param.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    )


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

            def _draw(image: Image.Image, _red: Optional[Image.Image], full: bool) -> None:
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
            "epd2in13b_v4": "epd2in13b_V4",
            "2in13b_v4": "epd2in13b_V4",
            "2in13b": "epd2in13b_V4",
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
                awake = True
                partial_ready = False
                # Tri-color panels take a black and a red buffer. Mono ones take one and a hint.
                color = _positional_arity(epd.display) >= 2

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
                clear_method = _pick_method(("Clear", "clear"))
                if partial_display and not color:
                    _debug(debug, f"Partial refresh supported via {partial_display.__name__}")
                else:
                    _debug(debug, "Partial refresh not supported; using full refresh only")

                def _init() -> None:
                    """Wake the panel; any partial-refresh setup died with the last sleep."""
                    nonlocal awake, partial_ready
                    epd.init()
                    awake = True
                    partial_ready = False

                def _sleep() -> None:
                    """Deep-sleep the panel once. Twice just yells at a closed SPI bus."""
                    nonlocal awake
                    if awake:
                        epd.sleep()
                        awake = False

                def _clear() -> None:
                    """Clear the Waveshare display to white."""
                    if not awake:
                        _init()
                    if clear_method is None:
                        return
                    if _positional_arity(clear_method) >= 1:
                        clear_method(0xFF)
                    else:
                        clear_method()

                def _draw(image: Image.Image, red: Optional[Image.Image], full: bool) -> None:
                    """Draw using full or partial refresh when available."""
                    nonlocal partial_ready
                    if not awake:
                        _init()
                    if color:
                        red_layer = red if red is not None else Image.new("1", image.size, 255)
                        epd.display(epd.getbuffer(image), epd.getbuffer(red_layer))
                        # Tri-color panels want deep sleep between refreshes, or they slowly cook.
                        _sleep()
                        return
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

                _debug(debug, f"Using waveshare module: {module_name} (color={color})")
                return DisplayDriver(
                    epd=epd,
                    width=int(getattr(epd, "width", 176)),
                    height=int(getattr(epd, "height", 264)),
                    init=_init,
                    sleep=_sleep,
                    clear=_clear,
                    draw=_draw,
                    color=color,
                    # Waveshare says three-color panels need at least 180s between refreshes.
                    min_refresh=180 if color else 0,
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
    sleep_requested: bool = False

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


@dataclass
class ButtonHandler:
    """Map button presses to state changes; the main loop does the actual SPI talking."""
    state: State
    total_pages: int
    wake: threading.Event

    def on_status(self) -> None:
        """Jump to the first page."""
        self.state.set_page(0, self.total_pages)
        self.wake.set()

    def on_network(self) -> None:
        """Jump to the second page."""
        self.state.set_page(1, self.total_pages)
        self.wake.set()

    def on_piratebox(self) -> None:
        """Jump to the third page."""
        self.state.set_page(2, self.total_pages)
        self.wake.set()

    def on_sleep(self) -> None:
        """Ask the main loop to sleep or wake the display. Callbacks touching SPI cause ghosts."""
        self.state.sleep_requested = True
        self.wake.set()


class ButtonWatcher:
    """Watch GPIO buttons and invoke handlers with minimal drama."""
    def __init__(self, config: ButtonConfig, handler: ButtonHandler):
        self.config = config
        self.handler = handler
        self._gpio = None
        self._buttons = []
        self._last_states: list[int] = []
        self._callbacks = [
            handler.on_status,
            handler.on_network,
            handler.on_piratebox,
            handler.on_sleep,
        ]

        if not config.pins:
            return

        try:
            from gpiozero import Button  # type: ignore

            for pin in config.pins:
                btn = Button(pin, pull_up=config.pull_up, bounce_time=0.05)
                self._buttons.append(btn)

            for btn, cb in zip(self._buttons, self._callbacks):
                btn.when_pressed = cb
            return
        except Exception:
            # Half-built gpiozero buttons would hog the pins the fallback needs.
            for btn in self._buttons:
                try:
                    btn.close()
                except Exception:
                    pass
            self._buttons = []

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

    @property
    def active(self) -> bool:
        """Whether any button can actually be pressed, as opposed to merely configured."""
        return bool(self._buttons) or self._gpio is not None

    @property
    def poll_interval(self) -> Optional[float]:
        """Seconds between polls for the RPi.GPIO fallback; None when callbacks do the work."""
        return 0.05 if self._gpio else None

    def poll(self) -> None:
        """Poll buttons when using the RPi.GPIO fallback."""
        if not self._gpio:
            return

        for idx, pin in enumerate(self.config.pins):
            current = self._gpio.input(pin)
            if self._last_states[idx] == 1 and current == 0 and idx < len(self._callbacks):
                self._callbacks[idx]()
            self._last_states[idx] = current

    def close(self) -> None:
        """Release GPIO resources when exiting."""
        for btn in self._buttons:
            try:
                btn.close()
            except Exception:
                pass
        if self._gpio:
            self._gpio.cleanup()


def _wait(buttons: ButtonWatcher, wake: threading.Event, seconds: float) -> None:
    """Nap until the interval ends or a button demands attention."""
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        buttons.poll()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        step = buttons.poll_interval
        if wake.wait(remaining if step is None else min(remaining, step)):
            break
    wake.clear()


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
    chats: int = 0
    clients: Optional[int] = None
    ssid: str = ""
    # "up", "starting" or "offline". ip is only filled in when the AP is really up.
    ap_state: str = "up"
    ap_reason: str = ""
    # Boot checklist as (label, "ok" | "wait" | "fail"); empty means nothing to report.
    checks: tuple[tuple[str, str], ...] = ()
    provisioning: bool = False
    log_hint: str = ""


@dataclass
class ApStatus:
    """What the radio is doing, according to the kernel rather than to hope."""
    state: str
    ip: str
    reason: str


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
    """Check whether a delta crosses the threshold. Negative means stop caring entirely."""
    if threshold < 0:
        return False
    if threshold == 0:
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
        counts = (
            (curr.files, prev.files),
            (curr.threads, prev.threads),
            (curr.posts, prev.posts),
            (curr.chats, prev.chats),
        )
        for current, previous in counts:
            if abs(current - previous) >= policy.count_delta:
                return True
        if curr.clients is None or prev.clients is None:
            if curr.clients != prev.clients:
                return True
        elif abs(curr.clients - prev.clients) >= policy.count_delta:
            return True
    network = (curr.ip, curr.ap_state, curr.ap_reason, curr.checks, curr.provisioning)
    if policy.ip_change and network != (prev.ip, prev.ap_state, prev.ap_reason, prev.checks, prev.provisioning):
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
    try:
        raw = path.read_text().strip()
    except OSError:
        return None
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
    """Return used and total disk bytes for the path, or its closest parent that exists."""
    # A data dir that does not exist yet still lives on some disk. Crash-looping helps nobody.
    target = path
    while not target.exists() and target != target.parent:
        target = target.parent
    stat = os.statvfs(target)
    total = stat.f_frsize * stat.f_blocks
    used = total - stat.f_frsize * stat.f_bfree
    return used, total


def _read_uptime_seconds() -> float:
    """Return seconds since boot, the one clock a Zero without an RTC gets right."""
    with open("/proc/uptime", "r", encoding="utf-8") as handle:
        return float(handle.read().split()[0])


def _read_uptime() -> str:
    """Return uptime formatted for small screens."""
    seconds = _read_uptime_seconds()
    minutes, _ = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _read_iface_ipv4(iface: str) -> str:
    """Return the first IPv4 address on the AP interface, or nothing. Never /etc/hosts fan fiction."""
    try:
        output = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show", "dev", iface],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception:
        return ""
    for line in output.splitlines():
        parts = line.split()
        if "inet" in parts:
            index = parts.index("inet")
            if index + 1 < len(parts):
                return parts[index + 1].split("/", 1)[0]
    return ""


def _rfkill_blocked(net_dir: Path) -> bool:
    """True when the interface's own radio switch is off, soft or hard."""
    for switch in sorted(net_dir.glob("phy80211/rfkill*/soft")) + sorted(net_dir.glob("phy80211/rfkill*/hard")):
        try:
            if switch.read_text().strip() == "1":
                return True
        except OSError:
            continue
    return False


def _iface_up(net_dir: Path) -> bool:
    """Check IFF_UP. An address on a downed interface is a phone number nobody answers."""
    try:
        return bool(int((net_dir / "flags").read_text().strip(), 16) & 0x1)
    except (OSError, ValueError):
        return False


def _iw_broadcasting(iface: str) -> Optional[bool]:
    """True when iw sees an AP with an SSID, False when it does not, None when iw is not talking."""
    try:
        output = subprocess.check_output(
            ["iw", "dev", iface, "info"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception:
        return None
    lines = [line.strip() for line in output.splitlines()]
    return "type AP" in lines and any(line.startswith("ssid ") for line in lines)


def _read_health_file(status_file: Path) -> dict[str, str]:
    """Read piratebox-ap-health's KEY=value status file. Missing means nobody has said anything yet."""
    values: dict[str, str] = {}
    try:
        for line in status_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    except OSError:
        pass
    return values


def _within_grace(grace: Optional[float]) -> bool:
    """True during the first minutes after boot, when 'not yet' is still an acceptable answer."""
    if grace is None:
        grace = float(_read_env_int("PIRATEBOX_EPD_AP_GRACE", 300))
    try:
        return _read_uptime_seconds() < grace
    except (OSError, ValueError, IndexError):
        return False


def _still_starting(health: dict[str, str], grace: Optional[float] = None) -> bool:
    """Is someone still working on the AP? A health verdict, either way, ends the benefit of the doubt."""
    state = health.get("STATE", "")
    if state == "checking":
        return True
    if state:
        return False
    return _within_grace(grace)


@dataclass
class ApProbe:
    """Raw facts about the AP interface, before anyone draws conclusions."""
    present: bool
    blocked: bool
    up: bool
    ip: str
    broadcasting: Optional[bool]


def _probe_ap(iface: str, sysfs: Path = SYSFS) -> ApProbe:
    """Ask sysfs, ip and iw about the AP interface, once per sample."""
    net_dir = sysfs / "class" / "net" / iface
    if not net_dir.is_dir():
        return ApProbe(present=False, blocked=False, up=False, ip="", broadcasting=False)
    blocked = _rfkill_blocked(net_dir)
    up = _iface_up(net_dir)
    broadcasting = _iw_broadcasting(iface) if up and not blocked else False
    return ApProbe(present=True, blocked=blocked, up=up, ip=_read_iface_ipv4(iface), broadcasting=broadcasting)


def _read_ap_status(
    iface: str,
    sysfs: Path = SYSFS,
    status_file: Optional[Path] = None,
    grace: Optional[float] = None,
    probe: Optional[ApProbe] = None,
) -> ApStatus:
    """Decide whether the AP is up, still starting, or offline, and say why."""
    probe = probe or _probe_ap(iface, sysfs)
    reason = ""
    if not probe.present:
        reason = f"NO {iface.upper()}"
    elif probe.blocked:
        reason = "RFKILL"
    elif not probe.up:
        reason = f"{iface.upper()} DOWN"
    elif not probe.ip:
        reason = "NO IP"
    elif probe.broadcasting is False:
        reason = "NOT BROADCASTING"
    if not reason:
        return ApStatus(state="up", ip=probe.ip, reason="")

    # Down is down; the only question is whether anyone is still working on it.
    health = _read_health_file(status_file or AP_STATUS_FILE)
    state = "starting" if _still_starting(health, grace) else "offline"
    return ApStatus(state=state, ip="", reason=reason)


def _dhcp_listening(proc_udp: Path = PROC_UDP) -> Optional[bool]:
    """True when something listens on UDP 67. On a PirateBox that is dnsmasq or nobody."""
    try:
        lines = proc_udp.read_text().splitlines()[1:]
    except OSError:
        return None
    for line in lines:
        parts = line.split()
        if len(parts) > 1 and parts[1].rsplit(":", 1)[-1].upper() == "0043":
            return True
    return False


def _web_port() -> int:
    """Port the web app listens on, from the same PORT the app reads. 0 means do not check."""
    raw = os.getenv("PIRATEBOX_EPD_WEB_PORT") or os.getenv("PORT") or "80"
    try:
        port = int(raw)
    except ValueError:
        return 80
    return port if 0 < port < 65536 else 0


def _web_answering(port: int) -> bool:
    """Knock on the web app's door. Whether it answers is all we want to know, not what it says."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def _startup_checks(
    probe: ApProbe,
    ap: ApStatus,
    ssid: str,
    dhcp: Optional[bool],
    web: Optional[bool],
    ap_pending: bool,
    web_pending: bool,
) -> tuple[tuple[str, str], ...]:
    """The boot checklist as (label, ok|wait|fail). Waiting turns into failing once nobody is trying."""

    def mark(ok: bool, pending: bool) -> str:
        if ok:
            return "ok"
        return "wait" if pending else "fail"

    wifi_ok = probe.present and not probe.blocked and probe.up and probe.broadcasting is not False
    checks = [
        ("RADIO", mark(probe.present and not probe.blocked, ap_pending)),
        (f"WIFI {ssid}".strip(), mark(wifi_ok, ap_pending)),
        (f"IP {probe.ip}" if probe.ip else "IP", mark(bool(probe.ip), ap_pending)),
    ]
    if dhcp is not None:
        checks.append(("DHCP", mark(dhcp, ap_pending)))
    if web is not None:
        checks.append(("WEB", mark(web, web_pending)))
    return tuple(checks)


def _is_ready(stats: Stats) -> bool:
    """Ready means a phone can join, get a lease and load the page. Anything less is a work in progress."""
    return stats.ap_state == "up" and all(state == "ok" for _, state in stats.checks)


def _startup_text(stats: Stats) -> tuple[str, str, str]:
    """Pick the title, the why line and the hint for the not-ready screen."""
    failing = {label.split()[0] for label, state in stats.checks if state == "fail"}
    if stats.provisioning:
        return "FIRST BOOT SETUP", "A FEW MINUTES ON A ZERO. MAKE TEA.", "THE URL SHOWS UP HERE WHEN READY."
    if stats.ap_state == "offline":
        why = f"WHY {stats.ap_reason}" if stats.ap_reason else ""
        return "AP OFFLINE", why, stats.log_hint or "RUN: sudo piratebox-ap-health"
    if "DHCP" in failing:
        return "DHCP DOWN", "PHONES JOIN, NOBODY GETS AN ADDRESS.", "CHECK: systemctl status dnsmasq"
    if "WEB" in failing:
        return "WEB APP DOWN", "WI-FI WORKS, THE PAGE DOES NOT.", "LOG: journalctl -u piratebox"
    return "STARTING UP", "", "THE URL SHOWS UP HERE WHEN READY."


def _ready_bypass(enabled: bool, announced: bool, stats: Stats, last_drawn: Optional[Stats]) -> bool:
    """Draw the first 'ready' frame now instead of making people stare at 'starting' for 3 more minutes.

    Once per service start, and only for the not-ready -> ready flip. Waveshare's 180 seconds still
    rule everything else, so the panel gets one exception per boot, not a habit.
    """
    return (
        enabled
        and not announced
        and last_drawn is not None
        and _is_ready(stats)
        and not _is_ready(last_drawn)
    )


def _ap_label(stats: Stats) -> str:
    """The line that replaces the URL when there is no URL worth printing."""
    return "AP STARTING" if stats.ap_state == "starting" else "AP OFFLINE"


def _has_ap_ip(stats: Stats) -> bool:
    """Only an AP that is up gets to advertise an address."""
    return stats.ap_state == "up" and bool(stats.ip)


def _read_ssid(conf_path: Path = HOSTAPD_CONF) -> str:
    """Read the broadcast SSID from hostapd, the only witness that matters."""
    try:
        for line in conf_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("ssid="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return os.getenv("PIRATEBOX_SSID", "")


def _read_clients(iface: str) -> Optional[int]:
    """Count stations on the access point, or None when iw is not talking to us."""
    try:
        output = subprocess.check_output(
            ["iw", "dev", iface, "station", "dump"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception:
        return None
    return sum(1 for line in output.splitlines() if line.startswith("Station "))


def _read_counts(db_path: Path) -> tuple[int, int, int, int]:
    """Count files, threads, posts, and chat messages in the SQLite DB."""
    if not db_path.exists():
        return 0, 0, 0, 0
    try:
        with sqlite3.connect(db_path) as conn:
            files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            threads = conn.execute("SELECT COUNT(*) FROM forum_threads").fetchone()[0]
            posts = conn.execute("SELECT COUNT(*) FROM forum_posts").fetchone()[0]
            chats = conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]
        return int(files), int(threads), int(posts), int(chats)
    except sqlite3.Error:
        return 0, 0, 0, 0


def _format_bytes(value: int) -> str:
    """Format byte counts without crying about base-2 vs base-10."""
    step = 1024.0
    size = float(value)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < step:
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= step
    return f"{size:.1f}PB"


def _logo_box(width: int, height: int) -> tuple[int, int]:
    """Pick a logo bounding box that leaves room for actual information."""
    short_side = min(width, height)
    if short_side >= 176:
        return 64, 64
    # Small panels get a wide, short logo; the PNG is wider than it is tall anyway.
    return max(32, int(short_side * 0.6)), max(24, int(short_side * 0.36))


def _load_logo(path: Path, max_size: tuple[int, int]) -> Optional[Image.Image]:
    """Load and resize the logo for the EPD."""
    if not path.exists():
        return None
    try:
        logo = Image.open(path)
    except Exception:
        return None
    # Grayscale first: thumbnailing a palette image quietly falls back to nearest-neighbour.
    logo = logo.convert("L")
    logo.thumbnail(max_size, Image.LANCZOS)
    return logo.convert("1")


def _prepare_canvas(width: int, height: int) -> Image.Image:
    """Create a blank paletted canvas: white paper, black ink, red for emergencies."""
    canvas = Image.new("P", (width, height), WHITE)
    palette = [255, 255, 255] * 256
    palette[BLACK * 3:BLACK * 3 + 3] = [0, 0, 0]
    palette[RED * 3:RED * 3 + 3] = [255, 0, 0]
    canvas.putpalette(palette)
    return canvas


def _split_layers(canvas: Image.Image, color: bool) -> tuple[Image.Image, Optional[Image.Image]]:
    """Split the canvas into 1-bit black and red layers. Mono panels get red as black."""
    if not color:
        return canvas.point(lambda idx: 0 if idx in (BLACK, RED) else 255, "1"), None
    black = canvas.point(lambda idx: 0 if idx == BLACK else 255, "1")
    red = canvas.point(lambda idx: 0 if idx == RED else 255, "1")
    return black, red


def _paste_mono(image: Image.Image, mono: Image.Image, xy: tuple[int, int], ink: int = BLACK) -> None:
    """Stamp the black pixels of a 1-bit image onto the canvas in the given ink."""
    mask = Image.eval(mono.convert("L"), lambda value: 255 - value)
    x, y = xy
    image.paste(ink, (x, y, x + mono.width, y + mono.height), mask)


@dataclass
class Fonts:
    """Bundle fonts for reuse across rendering functions."""
    tiny: ImageFont.ImageFont
    small: ImageFont.ImageFont
    medium: ImageFont.ImageFont
    large: ImageFont.ImageFont
    huge: ImageFont.ImageFont
    bold: ImageFont.ImageFont


@dataclass
class Layout:
    """Layout metrics for header, footer, and margins."""
    margin: int
    gutter: int
    header_h: int
    footer_h: int
    footer_hints: tuple[str, ...] = ()


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


def _scale(width: int, height: int) -> float:
    """Scale factor relative to the 2.7 inch panel. Smaller panels shrink, sadly."""
    return min(width, height) / 176.0


def _load_fonts(width: int, height: int) -> Fonts:
    """Load fonts with reasonable fallbacks and size scaling."""
    base = _scale(width, height)
    sizes = {
        "tiny": max(8, int(10 * base)),
        "small": max(10, int(12 * base)),
        "medium": max(12, int(15 * base)),
        "large": max(14, int(20 * base)),
        "huge": max(18, int(26 * base)),
    }
    font_hint = os.getenv("PIRATEBOX_EPD_FONT", "").strip()
    candidates = [
        font_hint,
        "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    bold_candidates = [
        os.getenv("PIRATEBOX_EPD_FONT_BOLD", "").strip(),
        "DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ] + candidates

    def _try_font(size: int, paths: list[str] = candidates) -> ImageFont.ImageFont:
        """Load the first available font or fall back to default."""
        for path in paths:
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
        bold=_try_font(sizes["large"], bold_candidates),
    )


def _make_layout(fonts: Fonts, width: int, height: int, hints: tuple[str, ...] = ()) -> Layout:
    """Compute layout metrics based on screen size. No buttons, no footer, no lies."""
    base = _scale(width, height)
    margin = max(4, int(6 * base))
    gutter = max(3, int(4 * base))
    header_h = _font_height(fonts.medium) + gutter * 2
    footer_h = _font_height(fonts.tiny) + gutter * 2 if hints else 0
    return Layout(margin=margin, gutter=gutter, header_h=header_h, footer_h=footer_h, footer_hints=hints)


def _footer_hints(page_names: list[str], button_count: int) -> tuple[str, ...]:
    """Label the keys with what they actually do, not what they did in 2.7-inch land."""
    hints = [
        f"K{idx + 1} {PAGE_HINTS.get(name, name.upper())}"
        for idx, name in enumerate(page_names[:3])
        if idx < button_count
    ]
    if button_count >= 4:
        hints.append("K4 SLEEP")
    return tuple(hints)


def _draw_header(
    draw: ImageDraw.ImageDraw,
    width: int,
    title: str,
    timestamp: str,
    fonts: Fonts,
    layout: Layout,
    fill: int = BLACK,
) -> None:
    """Draw the header bar with title and timestamp."""
    draw.rectangle((0, 0, width, layout.header_h), fill=fill)
    title_y = (layout.header_h - _font_height(fonts.medium)) // 2
    draw.text((layout.margin, title_y), title, font=fonts.medium, fill=WHITE)
    if timestamp:
        stamp_w = _text_width(fonts.small, timestamp)
        stamp_y = (layout.header_h - _font_height(fonts.small)) // 2
        draw.text((width - layout.margin - stamp_w, stamp_y), timestamp, font=fonts.small, fill=WHITE)


def _draw_footer(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    fonts: Fonts,
    layout: Layout,
) -> None:
    """Draw the footer with button hints, if there are buttons worth hinting at."""
    if not layout.footer_hints:
        return
    y0 = height - layout.footer_h
    draw.rectangle((0, y0, width, height), fill=BLACK)
    text = "  ".join(layout.footer_hints)
    text_y = y0 + (layout.footer_h - _font_height(fonts.tiny)) // 2
    draw.text((layout.margin, text_y), text, font=fonts.tiny, fill=WHITE)


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
    fill: int = BLACK,
) -> int:
    """Draw a labeled usage bar and return the next y coordinate."""
    draw.text((x, y), label, font=fonts.small, fill=BLACK)
    if value_text:
        # Narrow columns next to a logo used to print label and value on top of each other.
        room = width - _text_width(fonts.small, label) - layout.gutter
        if _text_width(fonts.tiny, value_text) > room:
            value_text = f"{percent:.0f}%"
        value_font = _fit_font(value_text, room, fonts.small, fonts.tiny)
        value_w = _text_width(value_font, value_text)
        value_y = y + _font_height(fonts.small) - _font_height(value_font)
        draw.text((x + width - value_w, value_y), value_text, font=value_font, fill=BLACK)
    y += _font_height(fonts.small) + 1
    bar_h = max(6, _font_height(fonts.tiny))
    draw.rectangle((x, y, x + width, y + bar_h), outline=BLACK, fill=WHITE)
    clamped = max(0.0, min(percent, 100.0))
    fill_w = int(width * clamped / 100.0)
    if fill_w:
        draw.rectangle((x, y, x + fill_w, y + bar_h), fill=fill)
    return y + bar_h + layout.gutter


def _center_text(draw: ImageDraw.ImageDraw, width: int, y: int, text: str, font: ImageFont.ImageFont) -> int:
    """Center text horizontally and return the next y coordinate."""
    text_w = _text_width(font, text)
    x = max(0, (width - text_w) // 2)
    draw.text((x, y), text, font=font, fill=BLACK)
    return y + _font_height(font) + 1


def _fit_font(text: str, max_width: int, *fonts: ImageFont.ImageFont) -> ImageFont.ImageFont:
    """Return the first font that fits, or the last one and a prayer."""
    for font in fonts:
        if _text_width(font, text) <= max_width:
            return font
    return fonts[-1]


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
        _paste_mono(image, logo, (logo_x, logo_y))
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
        fill=RED if disk_percent >= 90 else BLACK,
    )

    draw.text((x, y), f"UP {stats.uptime}", font=fonts.small, fill=BLACK)
    y += _font_height(fonts.small) + layout.gutter
    if _has_ap_ip(stats):
        draw.text((x, y), f"IP {stats.ip}", font=fonts.small, fill=BLACK)
    else:
        draw.text((x, y), f"{_ap_label(stats)} {stats.ap_reason}".strip(), font=fonts.small, fill=RED)


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
    draw.text((layout.margin, y), f"HOST {hostname}", font=fonts.small, fill=BLACK)
    y += _font_height(fonts.small) + layout.gutter

    if _has_ap_ip(stats):
        y = _center_text(draw, image.width, y, stats.ip, fonts.huge)
        y += layout.gutter
        draw.text((layout.margin, y), f"HTTP http://{stats.ip}", font=fonts.small, fill=BLACK)
    else:
        label = _ap_label(stats)
        label_font = _fit_font(label, image.width - 2 * layout.margin, fonts.huge, fonts.large, fonts.medium)
        y = _center_text(draw, image.width, y, label, label_font)
        y += layout.gutter
        draw.text((layout.margin, y), f"WHY {stats.ap_reason or '-'}", font=fonts.small, fill=RED)
    y += _font_height(fonts.small) + layout.gutter
    draw.text((layout.margin, y), "INTERNET: NO, BY DESIGN.", font=fonts.small, fill=BLACK)


def _render_piratebox(image: Image.Image, stats: Stats, fonts: Fonts, layout: Layout) -> None:
    """Render the PirateBox stats page."""
    draw = ImageDraw.Draw(image)
    _draw_header(draw, image.width, "BOX", stats.timestamp, fonts, layout)
    _draw_footer(draw, image.width, image.height, fonts, layout)

    y = layout.header_h + layout.gutter
    for label, value in (("FILES", stats.files), ("THREADS", stats.threads), ("POSTS", stats.posts)):
        draw.text((layout.margin, y), label, font=fonts.small, fill=BLACK)
        y += _font_height(fonts.small)
        draw.text((layout.margin, y), str(value), font=fonts.large, fill=BLACK)
        y += _font_height(fonts.large) + layout.gutter

    y = min(y, image.height - layout.footer_h - _font_height(fonts.small) - layout.gutter)
    draw.text((layout.margin, y), "LOCAL ONLY. NO CLOUD.", font=fonts.small, fill=BLACK)


def _ellipsize(text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    """Trim text until it fits. A 32-character SSID and a 120-pixel column will not negotiate."""
    if _text_width(font, text) <= max_width:
        return text
    while text and _text_width(font, text + "...") > max_width:
        text = text[:-1]
    return text + "..."


def _render_startup(
    image: Image.Image,
    stats: Stats,
    fonts: Fonts,
    layout: Layout,
) -> None:
    """Render the not-ready screen: what is done, what is pending, and what to do about the rest."""
    draw = ImageDraw.Draw(image)
    width, height = image.size
    margin, gutter = layout.margin, layout.gutter
    name = os.getenv("PIRATEBOX_NAME", "PirateBox").upper()
    _draw_header(draw, width, name, f"UP {stats.uptime}", fonts, layout, fill=RED)
    _draw_footer(draw, width, height, fonts, layout)
    title, why, hint = _startup_text(stats)
    full_width = width - 2 * margin

    y = layout.header_h + gutter
    title_font = _fit_font(title, full_width, fonts.bold, fonts.medium, fonts.small)
    draw.text((margin, y), title, font=title_font, fill=RED)
    y += _font_height(title_font) + gutter

    # Two columns of checkboxes: black is done, hollow is waiting, red is broken. No glyphs needed.
    row_h = _font_height(fonts.small) + gutter
    box = max(5, _font_height(fonts.small) - 4)
    col_w = full_width // 2
    for index, (label, state) in enumerate(stats.checks):
        x = margin + (index % 2) * col_w
        row_y = y + (index // 2) * row_h
        box_y = row_y + (_font_height(fonts.small) - box) // 2 + 1
        if state == "ok":
            draw.rectangle((x, box_y, x + box, box_y + box), fill=BLACK)
        elif state == "fail":
            draw.rectangle((x, box_y, x + box, box_y + box), fill=RED)
        else:
            draw.rectangle((x, box_y, x + box, box_y + box), outline=BLACK, fill=WHITE)
        text_x = x + box + gutter
        text = _ellipsize(label, fonts.small, col_w - box - gutter * 2)
        draw.text((text_x, row_y), text, font=fonts.small, fill=RED if state == "fail" else BLACK)
    y += ((len(stats.checks) + 1) // 2) * row_h

    if why:
        draw.text((margin, y), _ellipsize(why, fonts.small, full_width), font=fonts.small,
                  fill=BLACK if stats.provisioning else RED)
        y += row_h

    hint_y = max(y, height - layout.footer_h - margin - _font_height(fonts.tiny))
    if hint and hint_y + _font_height(fonts.tiny) <= height - layout.footer_h:
        draw.text((margin, hint_y), _ellipsize(hint, fonts.tiny, full_width), font=fonts.tiny, fill=BLACK)


def _render_dashboard(
    image: Image.Image,
    stats: Stats,
    logo: Optional[Image.Image],
    fonts: Fonts,
    layout: Layout,
) -> None:
    """Render everything on one page, for small screens with no buttons and no patience."""
    if not _is_ready(stats):
        # Counts and disk bars can wait. Until a phone can actually use the box, say what is missing.
        _render_startup(image, stats, fonts, layout)
        return
    draw = ImageDraw.Draw(image)
    width, height = image.size
    margin, gutter = layout.margin, layout.gutter
    name = os.getenv("PIRATEBOX_NAME", "PirateBox").upper()
    _draw_header(draw, width, name, f"UP {stats.uptime}", fonts, layout, fill=RED)
    _draw_footer(draw, width, height, fonts, layout)

    y = layout.header_h + gutter
    text_right = width - margin
    if logo:
        logo_x = width - margin - logo.width
        _paste_mono(image, logo, (logo_x, y))
        text_right = logo_x - gutter
    column = max(10, text_right - margin)
    small_h = _font_height(fonts.small)

    # The address is the only line anyone reads, so it gets the bold red treatment.
    # When the AP is down, that line says so instead of printing an address nobody can reach.
    has_ip = _has_ap_ip(stats)
    url = f"http://{stats.ip}" if has_ip else _ap_label(stats)
    if has_ip and _text_width(fonts.bold, url) > column:
        url = stats.ip
    url_font = _fit_font(url, column, fonts.bold, fonts.medium, fonts.small)
    draw.text((margin, y), url, font=url_font, fill=RED)
    y += _font_height(url_font) + gutter

    wifi = f"WIFI {stats.ssid or '-'}"
    draw.text((margin, y), wifi, font=_fit_font(wifi, column, fonts.small, fonts.tiny), fill=BLACK)
    y += small_h + gutter

    if has_ip:
        online = "-" if stats.clients is None else str(stats.clients)
        draw.text((margin, y), f"ONLINE {online}", font=fonts.small, fill=BLACK)
    else:
        why = f"WHY {stats.ap_reason or '-'}"
        draw.text((margin, y), why, font=_fit_font(why, column, fonts.small, fonts.tiny), fill=RED)
    y += small_h + gutter
    if logo:
        y = max(y, layout.header_h + gutter + logo.height + gutter)

    counts = f"FILES {stats.files}  CHAT {stats.chats}  POSTS {stats.posts}"
    full_width = width - 2 * margin
    draw.text((margin, y), counts, font=_fit_font(counts, full_width, fonts.small, fonts.tiny), fill=BLACK)
    y += small_h + gutter

    # Disk bar and temperature share a row. Red means "deal with this", not "decoration".
    disk_percent = _percent(stats.disk_used, stats.disk_total)
    temp_hot = stats.cpu_temp is not None and stats.cpu_temp >= 70
    temp_text = "" if stats.cpu_temp is None else f"{stats.cpu_temp:.0f}C"
    label = "DISK"
    value = f"{disk_percent:.0f}%"
    label_w = _text_width(fonts.small, label) + gutter
    value_w = _text_width(fonts.small, value) + gutter
    temp_w = _text_width(fonts.small, temp_text) + gutter * 2 if temp_text else 0
    bar_x0 = margin + label_w
    bar_x1 = max(bar_x0 + 10, width - margin - value_w - temp_w)
    bar_y0 = y + 2
    bar_y1 = y + small_h
    draw.text((margin, y), label, font=fonts.small, fill=BLACK)
    draw.rectangle((bar_x0, bar_y0, bar_x1, bar_y1), outline=BLACK, fill=WHITE)
    fill_w = int((bar_x1 - bar_x0) * max(0.0, min(disk_percent, 100.0)) / 100.0)
    if fill_w:
        draw.rectangle((bar_x0, bar_y0, bar_x0 + fill_w, bar_y1), fill=RED if disk_percent >= 90 else BLACK)
    draw.text((bar_x1 + gutter, y), value, font=fonts.small, fill=BLACK)
    if temp_text:
        temp_x = width - margin - _text_width(fonts.small, temp_text)
        draw.text((temp_x, y), temp_text, font=fonts.small, fill=RED if temp_hot else BLACK)
    y += small_h + gutter

    tagline = "NO INTERNET. BY DESIGN."
    tagline_y = max(y, height - layout.footer_h - margin - _font_height(fonts.tiny))
    if tagline_y + _font_height(fonts.tiny) <= height - layout.footer_h:
        _center_text(draw, width, tagline_y, tagline, fonts.tiny)


RENDERERS: dict[str, Callable[..., None]] = {
    "status": _render_status,
    "network": _render_network,
    "piratebox": _render_piratebox,
    "dashboard": _render_dashboard,
}


def _parse_pages(raw: str) -> list[tuple[str, Callable[..., None]]]:
    """Turn a comma list of page names into renderers, ignoring the imaginary ones."""
    pages = []
    for chunk in raw.split(","):
        name = chunk.strip().lower()
        if not name:
            continue
        if name not in RENDERERS:
            print(f"[epaper] Unknown page '{name}', skipping. Options: {', '.join(RENDERERS)}", file=sys.stderr)
            continue
        pages.append((name, RENDERERS[name]))
    if not pages:
        return _parse_pages(DEFAULT_PAGES)
    return pages


def _render_page(
    name: str,
    renderer: Callable[..., None],
    canvas: Image.Image,
    stats: Stats,
    logo: Optional[Image.Image],
    hostname: str,
    fonts: Fonts,
    layout: Layout,
) -> None:
    """Call a renderer with the arguments it insists on."""
    if name in ("status", "dashboard"):
        renderer(canvas, stats, logo, fonts, layout)
    elif name == "network":
        renderer(canvas, stats, hostname, fonts, layout)
    else:
        renderer(canvas, stats, fonts, layout)


def _canvas_size(width: int, height: int, orientation: str) -> tuple[int, int]:
    """Pick canvas dimensions; Waveshare's getbuffer rotates whichever way we hand it."""
    short_side, long_side = sorted((width, height))
    if orientation == "landscape":
        return long_side, short_side
    if orientation == "portrait":
        return short_side, long_side
    return width, height


def _collect_stats(db_path: Path, data_path: Path, prev_cpu: Optional[tuple[int, int]]) -> tuple[Stats, tuple[int, int]]:
    """Collect system stats and return a Stats object plus CPU state."""
    cpu_usage, cpu_state = _read_cpu_usage(prev_cpu)
    cpu_temp = _read_cpu_temp()
    mem_used, mem_total = _read_mem()
    disk_used, disk_total = _read_disk(data_path)
    uptime = _read_uptime()
    iface = os.getenv("PIRATEBOX_WIFI_IFACE", "wlan0")
    probe = _probe_ap(iface)
    ap = _read_ap_status(iface, probe=probe)
    ssid = _read_ssid()
    health = _read_health_file(AP_STATUS_FILE)
    provisioning = health.get("STATE") == "checking" and health.get("REASON") == "provisioning"
    port = _web_port()
    checks = _startup_checks(
        probe,
        ap,
        ssid,
        dhcp=_dhcp_listening(),
        web=_web_answering(port) if port else None,
        ap_pending=_still_starting(health),
        web_pending=provisioning or _within_grace(None),
    )
    log_hint = "SD CARD: piratebox-ap-status.txt" if any(path.exists() for path in BOOT_CONFIGS) else ""
    files, threads, posts, chats = _read_counts(db_path)
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
            ip=ap.ip,
            files=files,
            threads=threads,
            posts=posts,
            timestamp=timestamp,
            chats=chats,
            clients=_read_clients(iface),
            ssid=ssid,
            ap_state=ap.state,
            ap_reason=ap.reason,
            checks=checks,
            provisioning=provisioning,
            log_hint=log_hint,
        ),
        cpu_state,
    )


def _parse_size(raw: str) -> tuple[int, int]:
    """Parse WxH, because argparse has no type for 'a rectangle, please'."""
    try:
        width, height = (int(part) for part in raw.lower().split("x", 1))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected WxH, got {raw!r}") from exc
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError(f"expected positive WxH, got {raw!r}")
    return width, height


def _exit_on_sigterm(_signum: int, _frame: object) -> None:
    """Turn systemd's SIGTERM into a normal exit so the panel gets put to bed."""
    raise SystemExit(0)


def main() -> None:
    """CLI entry point that drives the e-paper refresh loop."""
    parser = argparse.ArgumentParser(description="PirateBox e-Paper status screen")
    parser.add_argument("--interval", type=int, default=_read_env_int("PIRATEBOX_EPD_INTERVAL", 30))
    parser.add_argument("--rotate", type=int, default=_read_env_int("PIRATEBOX_EPD_ROTATE", 0))
    parser.add_argument("--logo", type=str, default=os.getenv("PIRATEBOX_EPD_LOGO", str(DEFAULT_LOGO)))
    parser.add_argument("--buttons", type=str, default=os.getenv("PIRATEBOX_EPD_BUTTON_PINS", DEFAULT_BUTTON_PINS))
    parser.add_argument(
        "--pages",
        type=str,
        default=os.getenv("PIRATEBOX_EPD_PAGES", DEFAULT_PAGES),
        help=f"Comma-separated pages to show: {', '.join(RENDERERS)}",
    )
    parser.add_argument(
        "--orientation",
        choices=("auto", "portrait", "landscape"),
        default=os.getenv("PIRATEBOX_EPD_ORIENTATION", "auto").strip().lower() or "auto",
        help="Canvas orientation (auto uses the driver's native one)",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--driver",
        type=str,
        default=os.getenv("PIRATEBOX_EPD_DRIVER", "auto"),
        help="EPD driver to use: auto, rpi_epd2in7, waveshare_epd",
    )
    parser.add_argument(
        "--preview",
        type=str,
        default="",
        help="Render each page to PNG in this directory and exit. No hardware, no excuses.",
    )
    parser.add_argument(
        "--preview-size",
        type=_parse_size,
        default=_parse_size("250x122"),
        help="Canvas size for --preview, as WxH (default: 250x122)",
    )
    args = parser.parse_args()

    full_refresh_every = max(0, _read_env_int("PIRATEBOX_EPD_FULL_REFRESH_EVERY", 10))
    max_age = max(0, _read_env_int("PIRATEBOX_EPD_MAX_AGE", 3600))
    ready_refresh = os.getenv("PIRATEBOX_EPD_READY_REFRESH", "1") != "0"
    refresh_policy = _parse_refresh_policy()
    pages = _parse_pages(args.pages)
    page_names = [name for name, _ in pages]

    db_path = Path(os.getenv("PIRATEBOX_DB_PATH", ROOT_DIR / "data" / "piratebox.db"))
    data_path = Path(os.getenv("PIRATEBOX_DATA_DIR", ROOT_DIR / "data"))
    hostname = socket.gethostname()

    if args.preview:
        width, height = args.preview_size
        fonts = _load_fonts(width, height)
        layout = _make_layout(fonts, width, height)
        logo = _load_logo(Path(args.logo), _logo_box(width, height))
        stats, _ = _collect_stats(db_path, data_path, None)
        out_dir = Path(args.preview)
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, renderer in pages:
            canvas = _prepare_canvas(width, height)
            _render_page(name, renderer, canvas, stats, logo, hostname, fonts, layout)
            target = out_dir / f"{name}.png"
            canvas.convert("RGB").save(target)
            print(f"Wrote {target}")
        return

    _debug(
        args.debug,
        "Starting epaper: interval="
        f"{args.interval}s rotate={args.rotate} driver={args.driver} buttons={args.buttons or 'none'} "
        f"pages={','.join(page_names)} full_refresh_every={full_refresh_every} "
        f"refresh_on_change={int(refresh_policy.on_change)} max_age={max_age}s",
    )
    signal.signal(signal.SIGTERM, _exit_on_sigterm)
    driver = _load_driver(args.driver, debug=args.debug)
    min_refresh = max(0, _read_env_int("PIRATEBOX_EPD_MIN_REFRESH", driver.min_refresh))
    driver.clear()

    canvas_w, canvas_h = _canvas_size(driver.width, driver.height, args.orientation)
    wake = threading.Event()
    state = State(force_refresh=True)
    handler = ButtonHandler(state=state, total_pages=len(pages), wake=wake)
    # Buttons: four chances to do something useful, or at least entertaining.
    button_config = _parse_buttons(args.buttons)
    buttons = ButtonWatcher(button_config, handler)
    hints = _footer_hints(page_names, len(button_config.pins)) if buttons.active else ()
    # Without buttons, nobody can flip pages, so the pages flip themselves.
    cycle_pages = not buttons.active and len(pages) > 1

    fonts = _load_fonts(canvas_w, canvas_h)
    layout = _make_layout(fonts, canvas_w, canvas_h, hints)

    logo = _load_logo(Path(args.logo), _logo_box(canvas_w, canvas_h))
    _debug(args.debug, f"Logo: {args.logo} ({'loaded' if logo else 'missing'})")
    _debug(args.debug, f"Canvas: {canvas_w}x{canvas_h} color={driver.color} min_refresh={min_refresh}s")
    _debug(args.debug, f"DB path: {db_path}")
    _debug(args.debug, f"Data path: {data_path}")

    prev_cpu: Optional[tuple[int, int]] = None
    draw_count = 0
    last_drawn: Optional[Stats] = None
    last_page: Optional[int] = None
    last_draw_at: Optional[float] = None
    ready_announced = False

    try:
        # Main loop: keep the paper fresh so it does not look like last week's news.
        while True:
            if state.sleep_requested:
                state.sleep_requested = False
                if state.sleeping:
                    driver.init()
                    state.sleeping = False
                    state.force_refresh = True
                else:
                    driver.sleep()
                    state.sleeping = True
            if state.sleeping:
                _wait(buttons, wake, args.interval)
                continue

            stats, prev_cpu = _collect_stats(db_path, data_path, prev_cpu)
            page_name, renderer = pages[state.page]
            forced = state.force_refresh or _ready_bypass(ready_refresh, ready_announced, stats, last_drawn)
            age = None if last_draw_at is None else time.monotonic() - last_draw_at
            _debug(args.debug, f"Render page={page_name} force={forced} sleeping={state.sleeping}")

            changed = (
                forced
                or state.page != last_page
                or not refresh_policy.on_change
                or _stats_changed(last_drawn, stats, refresh_policy)
            )
            stale = max_age > 0 and age is not None and age >= max_age
            if not (changed or stale):
                _debug(args.debug, "No significant change; skipping refresh")
            elif not forced and age is not None and age < min_refresh:
                _debug(args.debug, f"Panel needs {min_refresh - age:.0f}s more rest; deferring refresh")
            else:
                canvas = _prepare_canvas(canvas_w, canvas_h)
                _render_page(page_name, renderer, canvas, stats, logo, hostname, fonts, layout)

                if args.rotate:
                    canvas = canvas.rotate(args.rotate, expand=True)

                full_refresh = forced
                if full_refresh_every:
                    full_refresh = full_refresh or draw_count % full_refresh_every == 0
                black, red = _split_layers(canvas, driver.color)
                driver.draw(black, red, full_refresh)
                state.force_refresh = False
                last_drawn = stats
                last_page = state.page
                last_draw_at = time.monotonic()
                draw_count += 1
                ready_announced = ready_announced or _is_ready(stats)
                if cycle_pages:
                    state.page = (state.page + 1) % len(pages)
            # Look more often while the box is still getting ready, so "ready" is noticed promptly.
            _wait(buttons, wake, args.interval if _is_ready(stats) else min(args.interval, STARTUP_POLL))
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
