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


def _fake_sysfs(tmp_path, *, present=True, up=True, blocked=False):
    """Build just enough /sys for one Wi-Fi interface to have opinions."""
    net = tmp_path / "sys" / "class" / "net" / "wlan0"
    if present:
        (net / "phy80211" / "rfkill0").mkdir(parents=True)
        (net / "flags").write_text("0x1003\n" if up else "0x1002\n")
        (net / "phy80211" / "rfkill0" / "soft").write_text("1\n" if blocked else "0\n")
        (net / "phy80211" / "rfkill0" / "hard").write_text("0\n")
    return tmp_path / "sys"


def _fake_tools(monkeypatch, *, addr="10.0.0.1/24", mode="AP"):
    """Stand-ins for ip and iw. Also makes sure nobody consults hostname resolution behind our back."""

    def check_output(argv, **_kwargs):
        if argv[:2] == ["ip", "-4"]:
            return f"3: wlan0    inet {addr} scope global wlan0\n" if addr else ""
        if argv[:2] == ["iw", "dev"] and argv[-1] == "info":
            ssid = "\tssid PirateBox\n" if mode == "AP" else ""
            return f"Interface wlan0\n\ttype {mode}\n{ssid}"
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(epaper.subprocess, "check_output", check_output)
    monkeypatch.setattr(epaper.socket, "gethostbyname", lambda _name: "127.0.1.1")


def test_ap_status_reports_the_ap_address_when_broadcasting(tmp_path, monkeypatch):
    _fake_tools(monkeypatch)
    status = epaper._read_ap_status("wlan0", sysfs=_fake_sysfs(tmp_path), status_file=tmp_path / "none")
    assert status == epaper.ApStatus(state="up", ip="10.0.0.1", reason="")


@pytest.mark.parametrize(
    ("sysfs_kwargs", "tool_kwargs", "reason"),
    [
        ({"present": False}, {}, "NO WLAN0"),
        ({"blocked": True}, {}, "RFKILL"),
        ({"up": False}, {}, "WLAN0 DOWN"),
        ({}, {"addr": ""}, "NO IP"),
        ({}, {"mode": "managed"}, "NOT BROADCASTING"),
    ],
)
def test_ap_status_never_falls_back_to_loopback(tmp_path, monkeypatch, sysfs_kwargs, tool_kwargs, reason):
    """The field report: a dead AP shown as 127.0.1.1. Now it is shown as dead, with a reason."""
    _fake_tools(monkeypatch, **tool_kwargs)
    status = epaper._read_ap_status(
        "wlan0", sysfs=_fake_sysfs(tmp_path, **sysfs_kwargs), status_file=tmp_path / "none", grace=0,
    )
    assert status == epaper.ApStatus(state="offline", ip="", reason=reason)


def test_ap_status_starting_versus_offline(tmp_path, monkeypatch):
    """Down during the health check is 'starting'; down after its verdict, or long after boot, is 'offline'."""
    _fake_tools(monkeypatch)
    sysfs = _fake_sysfs(tmp_path, blocked=True)
    status_file = tmp_path / "ap-status"

    status_file.write_text("STATE=checking\nREASON=rfkill-blocked\n")
    assert epaper._read_ap_status("wlan0", sysfs=sysfs, status_file=status_file).state == "starting"
    for verdict in ("offline", "healthy"):
        status_file.write_text(f"STATE={verdict}\n")
        assert epaper._read_ap_status("wlan0", sysfs=sysfs, status_file=status_file).state == "offline"

    missing = tmp_path / "missing"
    assert epaper._read_ap_status("wlan0", sysfs=sysfs, status_file=missing, grace=1e9).state == "starting"
    assert epaper._read_ap_status("wlan0", sysfs=sysfs, status_file=missing, grace=0).state == "offline"


def _drawn_text(monkeypatch):
    """Record every string a renderer draws, so tests can read the panel without squinting."""
    drawn = []
    real_text = epaper.ImageDraw.ImageDraw.text

    def spy(self, xy, text, *args, **kwargs):
        drawn.append(text)
        return real_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(epaper.ImageDraw.ImageDraw, "text", spy)
    return drawn


def _render(name, stats, width=250, height=122):
    fonts = epaper._load_fonts(width, height)
    layout = epaper._make_layout(fonts, width, height)
    canvas = epaper._prepare_canvas(width, height)
    renderer = dict(epaper._parse_pages(name))[name]
    epaper._render_page(name, renderer, canvas, stats, None, "piratebox", fonts, layout)
    return canvas


@pytest.mark.parametrize("state", ["offline", "starting"])
def test_pages_say_ap_down_instead_of_an_address(state, monkeypatch):
    """No URL, no loopback, just the bad news in red."""
    drawn = _drawn_text(monkeypatch)
    stats = _stats(ip="", ap_state=state, ap_reason="RFKILL", clients=None)
    for name in ("status", "network"):
        _render(name, stats)
    label = "AP OFFLINE" if state == "offline" else "AP STARTING"
    assert label in drawn or f"{label} RFKILL" in drawn
    assert "WHY RFKILL" in drawn
    assert not any("127.0.1.1" in text or "http://" in text for text in drawn)


CHECKS_BOOTING = (("RADIO", "ok"), ("WIFI PirateBox", "wait"), ("IP", "wait"), ("DHCP", "wait"), ("WEB", "ok"))
CHECKS_READY = (("RADIO", "ok"), ("WIFI PirateBox", "ok"), ("IP 10.0.0.1", "ok"), ("DHCP", "ok"), ("WEB", "ok"))


def test_dashboard_shows_the_checklist_until_ready(monkeypatch):
    """Counts and disk bars can wait; until a phone can use the box, the panel lists what is missing."""
    drawn = _drawn_text(monkeypatch)
    _render("dashboard", _stats(ip="", ap_state="starting", checks=CHECKS_BOOTING, provisioning=True))
    assert "FIRST BOOT SETUP" in drawn
    assert {"RADIO", "WIFI PirateBox", "IP", "DHCP", "WEB"} <= set(drawn)
    assert not any(text.startswith(("http://", "FILES")) for text in drawn)

    drawn.clear()
    _render("dashboard", _stats(checks=CHECKS_READY))
    assert "http://10.0.0.1" in drawn
    assert "RADIO" not in drawn


def test_failed_checks_are_red_and_explain_themselves(monkeypatch):
    drawn = _drawn_text(monkeypatch)
    checks = (("RADIO", "fail"), ("WIFI PirateBox", "fail"), ("IP 10.0.0.1", "ok"), ("DHCP", "ok"), ("WEB", "ok"))
    stats = _stats(ip="", ap_state="offline", ap_reason="RFKILL", checks=checks,
                   log_hint="SD CARD: piratebox-ap-status.txt")
    canvas = _render("dashboard", stats)
    assert {"AP OFFLINE", "WHY RFKILL", "SD CARD: piratebox-ap-status.txt"} <= set(drawn)
    _black, red = epaper._split_layers(canvas, color=True)
    assert red.getextrema()[0] == 0


@pytest.mark.parametrize(
    ("overrides", "title"),
    [
        ({"provisioning": True, "ap_state": "starting"}, "FIRST BOOT SETUP"),
        ({"ap_state": "starting"}, "STARTING UP"),
        ({"ap_state": "offline", "ap_reason": "NO IP"}, "AP OFFLINE"),
        ({"checks": (("WEB", "fail"),)}, "WEB APP DOWN"),
        ({"checks": (("DHCP", "fail"), ("WEB", "fail"))}, "DHCP DOWN"),
        ({"checks": (("WEB", "wait"),)}, "STARTING UP"),
    ],
)
def test_startup_titles(overrides, title):
    assert epaper._startup_text(_stats(**overrides))[0] == title


def test_ap_offline_hint_without_an_sd_card_status_file():
    _title, _why, hint = epaper._startup_text(_stats(ap_state="offline", log_hint=""))
    assert hint == "RUN: sudo piratebox-ap-health"


def test_startup_checks_wait_while_pending_and_fail_after():
    probe = epaper.ApProbe(present=True, blocked=True, up=False, ip="10.0.0.1", broadcasting=False)
    ap = epaper.ApStatus(state="starting", ip="", reason="RFKILL")
    pending = epaper._startup_checks(probe, ap, "PirateBox", dhcp=False, web=None, ap_pending=True, web_pending=True)
    assert pending == (("RADIO", "wait"), ("WIFI PirateBox", "wait"), ("IP 10.0.0.1", "ok"), ("DHCP", "wait"))
    given_up = epaper._startup_checks(probe, ap, "PirateBox", dhcp=True, web=False, ap_pending=False, web_pending=False)
    assert given_up == (
        ("RADIO", "fail"), ("WIFI PirateBox", "fail"), ("IP 10.0.0.1", "ok"), ("DHCP", "ok"), ("WEB", "fail"),
    )


def test_ready_needs_every_check():
    assert epaper._is_ready(_stats(checks=CHECKS_READY))
    assert epaper._is_ready(_stats())  # nothing to check is not a failure
    assert not epaper._is_ready(_stats(checks=CHECKS_READY[:-1] + (("WEB", "wait"),)))
    assert not epaper._is_ready(_stats(ap_state="starting", ip="", checks=CHECKS_READY))


def test_ready_frame_skips_the_queue_exactly_once():
    """The not-ready -> ready flip refreshes right away, once per service start, and only if enabled."""
    booting, ready = _stats(ip="", ap_state="starting", checks=CHECKS_BOOTING), _stats(checks=CHECKS_READY)
    assert epaper._ready_bypass(True, False, ready, booting)
    assert not epaper._ready_bypass(True, True, ready, booting)
    assert not epaper._ready_bypass(False, False, ready, booting)
    assert not epaper._ready_bypass(True, False, ready, ready)
    assert not epaper._ready_bypass(True, False, booting, booting)
    assert not epaper._ready_bypass(True, False, ready, None)


def test_dhcp_listening_reads_proc_net_udp(tmp_path):
    header = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
    proc = tmp_path / "udp"
    proc.write_text(header + "  12: 00000000:0043 00000000:0000 07 00000000:00000000 00:00000000 00000000 0 0 1\n")
    assert epaper._dhcp_listening(proc) is True
    proc.write_text(header + "  12: 0100007F:0035 00000000:0000 07 00000000:00000000 00:00000000 00000000 0 0 1\n")
    assert epaper._dhcp_listening(proc) is False
    assert epaper._dhcp_listening(tmp_path / "missing") is None


def test_web_check_knocks_on_the_configured_port(monkeypatch):
    import socket as real_socket

    with real_socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert epaper._web_answering(port) is True
    assert epaper._web_answering(port) is False

    monkeypatch.delenv("PIRATEBOX_EPD_WEB_PORT", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    assert epaper._web_port() == 80
    monkeypatch.setenv("PORT", "8080")
    assert epaper._web_port() == 8080
    monkeypatch.setenv("PIRATEBOX_EPD_WEB_PORT", "0")
    assert epaper._web_port() == 0


def test_collect_stats_notices_first_boot(tmp_path, monkeypatch):
    """firstboot writes REASON=provisioning; the panel should say setup, not failure."""
    status = tmp_path / "ap-status"
    status.write_text("STATE=checking\nREASON=provisioning\n")
    monkeypatch.setattr(epaper, "AP_STATUS_FILE", status)
    monkeypatch.setattr(epaper, "BOOT_CONFIGS", (tmp_path / "piratebox.txt",))
    monkeypatch.setattr(epaper, "_probe_ap", lambda _iface: epaper.ApProbe(True, False, False, "", False))
    monkeypatch.setattr(epaper, "_dhcp_listening", lambda: False)
    monkeypatch.setattr(epaper, "_web_answering", lambda _port: False)
    monkeypatch.setattr(epaper, "_read_ssid", lambda: "PirateBox")
    monkeypatch.setattr(epaper, "_read_clients", lambda _iface: None)

    stats, _ = epaper._collect_stats(tmp_path / "db", tmp_path, None)
    assert stats.provisioning is True
    assert stats.ap_state == "starting"
    assert all(state == "wait" for _, state in stats.checks if _ != "RADIO")
    assert epaper._startup_text(stats)[0] == "FIRST BOOT SETUP"


def test_ap_state_change_triggers_a_refresh():
    policy = epaper._parse_refresh_policy()
    up = _stats()
    down = _stats(ip="", ap_state="offline", ap_reason="RFKILL")
    assert epaper._stats_changed(up, down, policy)
    assert epaper._stats_changed(down, _stats(ip="", ap_state="offline", ap_reason="NO IP"), policy)
    assert not epaper._stats_changed(down, _stats(ip="", ap_state="offline", ap_reason="RFKILL"), policy)
    assert epaper._stats_changed(_stats(checks=CHECKS_BOOTING), _stats(checks=CHECKS_READY), policy)
