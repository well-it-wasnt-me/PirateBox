"""E-paper tests: proving the screen logic works without owning the screen."""

import importlib.util
import sys
import threading
import time
import types
from pathlib import Path

import pytest

pytest.importorskip("PIL")

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("epaper_hat", ROOT / "scripts" / "epaper_hat.py")
epaper = importlib.util.module_from_spec(SPEC)
# Dataclasses look themselves up in sys.modules, so register before executing.
sys.modules[SPEC.name] = epaper
SPEC.loader.exec_module(epaper)


def _stats(**overrides):
    """Build a Stats snapshot that looks like a quiet afternoon on the box."""
    values = dict(
        cpu_usage=12.0,
        cpu_temp=48.0,
        mem_used=200 * 1024 * 1024,
        mem_total=512 * 1024 * 1024,
        disk_used=3 * 1024**3,
        disk_total=16 * 1024**3,
        uptime="3h 12m",
        ip="10.0.0.1",
        files=12,
        threads=3,
        posts=31,
        timestamp="12:00",
        chats=230,
        clients=4,
        ssid="PirateBox",
    )
    values.update(overrides)
    return epaper.Stats(**values)


class FakeTriColorEPD:
    """A pretend 2.13 inch B V4 that writes down everything it is told."""

    def __init__(self):
        self.width = 122
        self.height = 250
        self.calls = []

    def init(self):
        self.calls.append("init")

    def sleep(self):
        self.calls.append("sleep")

    def Clear(self):
        self.calls.append("clear")

    def getbuffer(self, image):
        return image

    def display(self, imageblack, imagered):
        self.calls.append(("display", imageblack.size, imagered.size))


class FakeMonoEPD:
    """A pretend mono panel with partial refresh, the kind that forgets on sleep."""

    def __init__(self):
        self.width = 176
        self.height = 264
        self.calls = []

    def init(self):
        self.calls.append("init")

    def init_fast(self):
        self.calls.append("init_fast")

    def sleep(self):
        self.calls.append("sleep")

    def Clear(self, color):
        self.calls.append(("clear", color))

    def getbuffer(self, image):
        return image

    def display(self, image):
        self.calls.append("display")

    def display_fast(self, image):
        self.calls.append("display_fast")


@pytest.fixture()
def fake_waveshare(monkeypatch):
    """Install a fake waveshare_epd package so the loader has something to load."""
    package = types.ModuleType("waveshare_epd")
    package.__path__ = []
    tri = types.ModuleType("waveshare_epd.fake_tri")
    tri.EPD = FakeTriColorEPD
    mono = types.ModuleType("waveshare_epd.fake_mono")
    mono.EPD = FakeMonoEPD
    monkeypatch.setitem(sys.modules, "waveshare_epd", package)
    monkeypatch.setitem(sys.modules, "waveshare_epd.fake_tri", tri)
    monkeypatch.setitem(sys.modules, "waveshare_epd.fake_mono", mono)


def test_split_layers_color_and_mono():
    """Red ink goes to the red layer on color panels and to black on mono ones."""
    canvas = epaper._prepare_canvas(4, 1)
    canvas.putpixel((0, 0), epaper.BLACK)
    canvas.putpixel((1, 0), epaper.RED)

    black, red = epaper._split_layers(canvas, color=True)
    assert [black.getpixel((x, 0)) for x in range(3)] == [0, 255, 255]
    assert [red.getpixel((x, 0)) for x in range(3)] == [255, 0, 255]

    mono, none = epaper._split_layers(canvas, color=False)
    assert none is None
    assert [mono.getpixel((x, 0)) for x in range(3)] == [0, 0, 255]


def test_dashboard_renders_red_and_black():
    """The dashboard should fit 250x122 and actually use the red it paid for."""
    width, height = 250, 122
    fonts = epaper._load_fonts(width, height)
    layout = epaper._make_layout(fonts, width, height)
    canvas = epaper._prepare_canvas(width, height)
    logo = epaper._load_logo(epaper.DEFAULT_LOGO, epaper._logo_box(width, height))

    epaper._render_dashboard(canvas, _stats(), logo, fonts, layout)

    black, red = epaper._split_layers(canvas, color=True)
    assert canvas.size == (width, height)
    assert red.getpixel((1, 1)) == 0  # red header bar
    assert black.getextrema()[0] == 0  # some black ink made it too


def test_negative_threshold_disables_a_metric():
    """Negative deltas mean "never refresh for this", which tri-color panels appreciate."""
    assert epaper._delta_exceeds(50.0, -1) is False
    assert epaper._delta_exceeds(0.5, 0) is True
    assert epaper._delta_exceeds(4.0, 5) is False

    policy = epaper.RefreshPolicy(
        on_change=True, cpu_delta=-1, temp_delta=5, mem_delta=-1,
        disk_delta=1, count_delta=1, ip_change=True,
    )
    assert not epaper._stats_changed(_stats(), _stats(cpu_usage=95.0), policy)
    assert epaper._stats_changed(_stats(), _stats(chats=231), policy)
    assert epaper._stats_changed(_stats(), _stats(clients=5), policy)


def test_parse_pages_skips_unknown_and_falls_back():
    """Typos get skipped; an empty list falls back to the classic three pages."""
    assert [name for name, _ in epaper._parse_pages("dashboard, nope")] == ["dashboard"]
    assert [name for name, _ in epaper._parse_pages("nope")] == ["status", "network", "piratebox"]


def test_canvas_size_orientation():
    """Landscape and portrait swap sides; auto keeps whatever the driver claims."""
    assert epaper._canvas_size(122, 250, "landscape") == (250, 122)
    assert epaper._canvas_size(250, 122, "portrait") == (122, 250)
    assert epaper._canvas_size(122, 250, "auto") == (122, 250)


def test_footer_hints_follow_pages_and_buttons():
    """Hints describe the configured pages, and vanish when there are no buttons."""
    assert epaper._footer_hints(["status", "network", "piratebox"], 4) == (
        "K1 STAT", "K2 NET", "K3 BOX", "K4 SLEEP",
    )
    assert epaper._footer_hints(["dashboard"], 0) == ()


def test_read_disk_falls_back_to_existing_parent(tmp_path):
    """A missing data dir should not crash-loop the display service."""
    used, total = epaper._read_disk(tmp_path / "not" / "there" / "yet")
    assert total > 0
    assert used >= 0


def test_tricolor_driver_sleeps_after_each_draw(fake_waveshare, monkeypatch):
    """Tri-color panels draw both layers, then deep-sleep, then wake for the next frame."""
    monkeypatch.setenv("PIRATEBOX_EPD_WAVESHARE_MODULE", "fake_tri")
    driver = epaper._load_driver("waveshare")
    epd = driver.epd
    assert driver.color is True
    assert driver.min_refresh == 180

    driver.clear()
    black = epaper.Image.new("1", (250, 122), 255)
    driver.draw(black, None, True)
    driver.draw(black, black, False)
    driver.sleep()

    assert epd.calls == [
        "init",
        "clear",
        ("display", (250, 122), (250, 122)),
        "sleep",
        "init",
        ("display", (250, 122), (250, 122)),
        "sleep",
    ]


def test_mono_partial_refresh_reinitializes_after_wake(fake_waveshare, monkeypatch):
    """Waking from sleep must redo partial-refresh setup instead of trusting a stale flag."""
    monkeypatch.setenv("PIRATEBOX_EPD_WAVESHARE_MODULE", "fake_mono")
    driver = epaper._load_driver("waveshare")
    epd = driver.epd
    image = epaper.Image.new("1", (176, 264), 255)

    driver.clear()
    driver.draw(image, None, False)
    driver.sleep()
    driver.init()
    driver.draw(image, None, False)

    assert epd.calls == [
        "init",
        ("clear", 0xFF),
        "init_fast",
        "display_fast",
        "sleep",
        "init",
        "init_fast",
        "display_fast",
    ]


def test_sleep_button_only_sets_flags():
    """Button callbacks run on another thread, so they must not touch the SPI bus."""
    wake = threading.Event()
    state = epaper.State()
    handler = epaper.ButtonHandler(state=state, total_pages=3, wake=wake)

    handler.on_sleep()
    handler.on_network()

    assert state.sleep_requested is True
    assert state.page == 1
    assert wake.is_set()


def test_wait_returns_early_on_wake():
    """A button press should cut the nap short instead of waiting out the interval."""
    wake = threading.Event()
    handler = epaper.ButtonHandler(state=epaper.State(), total_pages=1, wake=wake)
    buttons = epaper.ButtonWatcher(epaper.ButtonConfig(pins=[], pull_up=True), handler)
    threading.Timer(0.05, wake.set).start()

    started = time.monotonic()
    epaper._wait(buttons, wake, 5)

    assert time.monotonic() - started < 2
    assert not wake.is_set()
