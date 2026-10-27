"""Boot lifecycle tests: the real installer, first boot and pi-setup, booted over and over on a fake Pi.

Runs inside an unprivileged user namespace with a pretend SD card (see fake_pi.py). Where user
namespaces are locked down, as on stock Ubuntu CI runners, these skip instead of lying.
Simulated hardware, real scripts: a pass here is evidence about the logic, not about radio waves.
"""

import pytest
from fake_pi import FakePi, sandbox_available

pytestmark = pytest.mark.skipif(not sandbox_available(), reason="no unprivileged user namespaces here")

MARKER = "var/lib/piratebox/.zero_firstboot_done"
NM_STATE = "var/lib/NetworkManager/NetworkManager.state"
# Disabled units are never started; enabled ones with the marker skip. Either way: not re-run.
NOT_RERUN = ("not-run", "skipped")


def provisioned(tmp_path, *install_args, country="", nm_wireless_enabled=False):
    """Flash, install, optionally set a country, and survive the first boot."""
    pi = FakePi(tmp_path, nm_wireless_enabled=nm_wireless_enabled)
    result = pi.install(*(install_args or ("--image-build",)))
    assert result.returncode == 0, result.stdout + result.stderr
    if country:
        pi.set_boot_config(COUNTRY=country)
    result = pi.boot()
    assert result.returncode == 0, result.stdout + result.stderr
    assert pi.boot_rc("piratebox-zero-firstboot") == "0", pi.read("var/log/piratebox-zero-firstboot.log")
    return pi


def health_repairs(pi):
    """What the boot-time health check changed. The AP address unit's own unblock does not count."""
    meddling = ("systemctl restart", "rfkill unblock", "ip addr replace")
    return [call for call in pi.health_calls() if call.startswith(meddling)]


def test_first_boot_provisions_and_verifies_the_ap(tmp_path):
    pi = provisioned(tmp_path)
    assert pi.ap_healthy()
    assert pi.exists(MARKER)
    assert not pi.is_enabled("piratebox-zero-firstboot")
    for unit in ("hostapd", "dnsmasq", "piratebox-ap-ip", "piratebox-ap-health", "piratebox", "piratebox-epaper"):
        assert pi.is_enabled(unit), unit
    # The base image said "radio off"; provisioning has to say otherwise, durably.
    assert "WirelessEnabled=true" in pi.read(NM_STATE)
    log = pi.read("var/log/piratebox-zero-firstboot.log")
    assert "Checked, not assumed" in log
    assert "world regulatory domain" in log
    assert "STATE=healthy" in pi.read("boot/firmware/piratebox-ap-status.txt")


def test_every_later_boot_brings_the_ap_back_without_reprovisioning(tmp_path):
    """The reported bug: fine on boot one, silent on boot two. Here, NetworkManager always starts last."""
    pi = provisioned(tmp_path)
    hostapd_conf = pi.read("etc/hostapd/hostapd.conf")
    for _ in range(5):
        result = pi.boot(adversarial=True)
        assert result.returncode == 0, result.stdout + result.stderr
        assert pi.boot_rc("piratebox-zero-firstboot") in NOT_RERUN
        assert pi.boot_rc("piratebox-ap-health") == "0"
        assert pi.ap_healthy()
        assert health_repairs(pi) == [], pi.calls()
    assert pi.read("etc/hostapd/hostapd.conf") == hostapd_conf
    assert pi.read("var/log/piratebox-zero-firstboot.log").count("First boot provisioning complete") == 1


def test_unblock_is_ordered_after_networkmanager(tmp_path):
    """Whatever order systemd picks, the AP address unit runs after NetworkManager has had its say."""
    pi = provisioned(tmp_path)
    for adversarial in (True, False):
        order = pi.boot_order(adversarial)
        assert order.index("NetworkManager") < order.index("piratebox-ap-ip") < order.index("hostapd")
        assert order.index("piratebox-ap-health") > max(order.index("hostapd"), order.index("dnsmasq"))


@pytest.mark.parametrize("country", ["IT", "UK"])
def test_country_is_used_as_given(tmp_path, country):
    """A valid code reaches raspi-config; an invalid one is reported, never swapped for another."""
    pi = provisioned(tmp_path, country=country)
    assert f"country_code={country}" in pi.read("etc/hostapd/hostapd.conf")
    log = pi.read("var/log/piratebox-zero-firstboot.log")
    if country == "IT":
        assert pi.get("regdom", "") == "IT"
    else:
        assert "raspi-config rejected country 'UK'" in log
        assert pi.get("regdom", "") == ""
    for _ in range(3):
        pi.boot()
        assert pi.ap_healthy()


def test_garbage_country_is_ignored_not_invented(tmp_path):
    pi = provisioned(tmp_path, country="1X")
    assert "country_code" not in pi.read("etc/hostapd/hostapd.conf")
    assert "is not a two-letter code" in pi.read("var/log/piratebox-zero-firstboot.log")
    pi.boot()
    assert pi.ap_healthy()


@pytest.mark.parametrize("radio", ["true", "false"])
def test_marker_with_unhealthy_ap_recovers_without_touching_the_marker(tmp_path, radio):
    """Marker present, radio re-blocked after hostapd started. The health check, not first boot, fixes it."""
    pi = provisioned(tmp_path)
    state = pi.path(NM_STATE)
    state.write_text(state.read_text().replace("WirelessEnabled=true", f"WirelessEnabled={radio}"))
    pi.boot(adversarial=True, late_block=True)
    assert pi.boot_rc("piratebox-zero-firstboot") in NOT_RERUN
    assert pi.boot_rc("piratebox-ap-health") == "0"
    assert pi.ap_healthy()
    assert health_repairs(pi) == ["rfkill unblock wlan"]
    assert pi.exists(MARKER)
    assert "rfkill-unblock: ok" in pi.read("var/log/piratebox-ap-health.log")


def test_legacy_radio_off_state_is_harmless_with_the_new_ordering(tmp_path):
    """Even if NetworkManager still remembers 'radio off', the AP address unit now unblocks after it."""
    pi = provisioned(tmp_path)
    state = pi.path(NM_STATE)
    state.write_text(state.read_text().replace("WirelessEnabled=true", "WirelessEnabled=false"))
    for _ in range(3):
        pi.boot(adversarial=True)
        assert pi.ap_healthy()
        assert health_repairs(pi) == []


def test_networkmanager_restart_later_does_not_kill_the_ap(tmp_path):
    """An apt upgrade restarts NetworkManager mid-afternoon. With the radio remembered as on, nobody notices."""
    pi = provisioned(tmp_path)
    pi.boot()
    script = pi.base / "fake" / "later.sh"
    script.write_text("fakeboot reset\n" + "\n".join(f"fakeboot start {u}" for u in pi.boot_order()) + "\n"
                      "systemctl restart NetworkManager\n")
    pi.run("/bin/bash", "/fake/later.sh")
    assert pi.ap_healthy()


def test_transient_hostapd_failure_recovers_in_the_same_boot(tmp_path):
    pi = provisioned(tmp_path)
    pi.unit("hostapd", failonce=True)
    pi.boot()
    assert pi.ap_healthy()
    assert pi.boot_rc("piratebox-ap-health") == "0"
    assert health_repairs(pi) == ["systemctl restart hostapd.service"]


def test_broken_hostapd_is_reported_bounded_and_survivable(tmp_path):
    """A hostapd that will not start gets a fixed number of retries and a note on the FAT partition."""
    pi = provisioned(tmp_path)
    pi.unit("hostapd", fail=True)
    pi.boot()
    assert not pi.ap_healthy()
    assert pi.boot_rc("piratebox-ap-health") == "1"
    assert health_repairs(pi) == ["systemctl restart hostapd.service"] * 4
    boot_status = pi.read("boot/firmware/piratebox-ap-status.txt")
    assert "STATE=offline" in boot_status and "hostapd-inactive" in boot_status
    # Fix the "hardware" and reboot: back on air, still without re-running provisioning.
    pi.unit("hostapd", fail=False)
    pi.boot()
    assert pi.ap_healthy()
    assert pi.boot_rc("piratebox-zero-firstboot") in NOT_RERUN
    assert "STATE=healthy" in pi.read("boot/firmware/piratebox-ap-status.txt")


def test_dnsmasq_failure_is_retried(tmp_path):
    pi = provisioned(tmp_path)
    pi.unit("dnsmasq", failonce=True)
    pi.boot()
    assert pi.ap_healthy()
    assert health_repairs(pi) == ["systemctl restart dnsmasq.service"]


def test_missing_ap_address_is_restored(tmp_path):
    pi = provisioned(tmp_path)
    pi.unit("piratebox-ap-ip", failonce=True)
    pi.boot()
    assert pi.ap_healthy()
    assert health_repairs(pi) == ["ip addr replace 10.0.0.1/24 dev wlan0"]


@pytest.mark.parametrize("crash_at", ["sysctl", "iw"])
def test_power_cut_during_provisioning_is_finished_on_the_next_boot(tmp_path, crash_at):
    """Pull the plug mid pi-setup (sysctl) or mid health check (iw). The next boot finishes the job."""
    pi = FakePi(tmp_path)
    assert pi.install("--image-build").returncode == 0
    result = pi.boot(crash_at=crash_at)
    assert result.returncode != 0
    assert not pi.exists(MARKER)
    assert pi.is_enabled("piratebox-zero-firstboot")

    pi.boot()
    assert pi.boot_rc("piratebox-zero-firstboot") == "0"
    assert pi.exists(MARKER)
    assert pi.ap_healthy()
    # Re-running provisioning rewrites the same files instead of stacking duplicates.
    assert pi.read("etc/dnsmasq.d/piratebox.conf").count("interface=wlan0") == 1
    assert pi.read("etc/NetworkManager/conf.d/99-piratebox.conf").count("unmanaged-devices") == 1
    pi.boot()
    assert pi.ap_healthy()
    assert pi.boot_rc("piratebox-zero-firstboot") in NOT_RERUN


@pytest.mark.parametrize("nm_wireless_enabled", [True, False])
def test_installer_path_matches_the_image_path(tmp_path, nm_wireless_enabled):
    """install-zero.sh on a live system (Option B) must end up where the prebuilt image does."""
    pi = provisioned(tmp_path, "--no-reboot", nm_wireless_enabled=nm_wireless_enabled)
    assert pi.ap_healthy()
    for _ in range(3):
        pi.boot()
        assert pi.ap_healthy()
        assert pi.boot_rc("piratebox-zero-firstboot") in NOT_RERUN


def test_user_configuration_survives_boots_and_recovery(tmp_path):
    """Recovery fixes the radio. It does not get to rewrite anyone's settings on the way."""
    pi = provisioned(tmp_path)
    tuned = {
        "etc/default/piratebox": "# PirateBox Zero W app settings, tuned\nPIRATEBOX_MAX_UPLOAD_MB=64\n",
        "etc/default/piratebox-epaper": "# PirateBox Zero W e-paper settings, tuned\nPIRATEBOX_EPD_INTERVAL=120\n",
        "boot/firmware/piratebox.txt": pi.read("boot/firmware/piratebox.txt") + "# my note\n",
    }
    for inner, content in tuned.items():
        pi.path(inner).write_text(content)
    pi.unit("hostapd", failonce=True)
    for _ in range(3):
        pi.boot()
        assert pi.ap_healthy()
    for inner, content in tuned.items():
        assert pi.read(inner) == content, inner
