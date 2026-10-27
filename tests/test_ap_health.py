"""AP health check tests: a fake kernel, a real script, and no patience for exit codes that lie."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fake_pi import ROOT, FakeKernel, health_env

HEALTH = ROOT / "scripts" / "piratebox-ap-health.sh"

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux") or shutil.which("bash") is None,
    reason="the health check is bash for Linux, and so is its test",
)


@pytest.fixture
def kernel(tmp_path):
    """A provisioned box after a clean boot: everything enabled, running and on air."""
    k = FakeKernel(tmp_path)
    for unit in ("hostapd", "dnsmasq"):
        k.unit(unit, enabled=True, active=True)
    k.put("wlan", "1")
    k.put("rfkill", "0")
    k.put("beacon", "1")
    k.put("addr", "10.0.0.1/24")
    k.sync()
    k.clear_calls()
    return k


def run_health(kernel, tmp_path, *args):
    """Run the real script against the fake kernel, quickly."""
    return subprocess.run(
        ["bash", str(HEALTH), "--interval", "0", *args],
        env=health_env(kernel, tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
    )


def repairs(kernel):
    """Calls that change something. Probes do not count; meddling does."""
    meddling = ("systemctl restart", "systemctl start", "rfkill unblock", "ip addr replace")
    return [call for call in kernel.calls() if call.startswith(meddling)]


def status(tmp_path):
    return dict(line.split("=", 1) for line in (tmp_path / "run" / "ap-status").read_text().splitlines())


def test_healthy_ap_is_left_alone(kernel, tmp_path):
    """A working AP gets checked, logged, and not restarted for company."""
    result = run_health(kernel, tmp_path)
    assert result.returncode == 0, result.stdout
    assert repairs(kernel) == []
    assert status(tmp_path)["STATE"] == "healthy"
    assert "STATE=healthy" in (tmp_path / "boot-status.txt").read_text()
    assert "No repairs needed" in (tmp_path / "ap-health.log").read_text()


def test_rfkill_block_is_lifted_without_restarting_hostapd(kernel, tmp_path):
    """The field failure: hostapd 'active', radio soft-blocked. Unblocking is the whole fix."""
    kernel.put("rfkill", "1")
    kernel.put("beacon", "0")
    kernel.sync()
    result = run_health(kernel, tmp_path)
    assert result.returncode == 0, result.stdout
    assert repairs(kernel) == ["rfkill unblock wlan"]
    assert kernel.ap_healthy()
    log = (tmp_path / "ap-health.log").read_text()
    assert "rfkill-blocked" in log and "rfkill-unblock: ok" in log


def test_dead_hostapd_is_restarted_and_nothing_else(kernel, tmp_path):
    kernel.unit("hostapd", active=False)
    kernel.put("beacon", "0")
    kernel.sync()
    result = run_health(kernel, tmp_path)
    assert result.returncode == 0, result.stdout
    assert repairs(kernel) == ["systemctl restart hostapd.service"]


def test_dead_dnsmasq_is_restarted_and_hostapd_is_not(kernel, tmp_path):
    kernel.unit("dnsmasq", active=False)
    result = run_health(kernel, tmp_path)
    assert result.returncode == 0, result.stdout
    assert repairs(kernel) == ["systemctl restart dnsmasq.service"]


def test_missing_address_is_put_back(kernel, tmp_path):
    kernel.put("addr", "")
    result = run_health(kernel, tmp_path)
    assert result.returncode == 0, result.stdout
    assert repairs(kernel) == ["ip addr replace 10.0.0.1/24 dev wlan0"]
    assert "10.0.0.1/24" in kernel.addresses()


def test_similar_address_does_not_count(kernel, tmp_path):
    """10.0.0.10 is a DHCP client, not the gateway."""
    kernel.put("addr", "10.0.0.10/24")
    result = run_health(kernel, tmp_path)
    assert result.returncode == 0
    assert "ip addr replace 10.0.0.1/24 dev wlan0" in repairs(kernel)


def test_persistent_failure_gives_up_after_bounded_attempts(kernel, tmp_path):
    """A broken hostapd gets four tries, a clear verdict, and then peace and quiet."""
    kernel.unit("hostapd", active=False, fail=True)
    kernel.put("beacon", "0")
    kernel.sync()
    result = run_health(kernel, tmp_path, "--attempts", "4")
    assert result.returncode == 1
    assert repairs(kernel) == ["systemctl restart hostapd.service"] * 4
    assert status(tmp_path) == {
        "STATE": "offline", "REASON": "hostapd-inactive", "IFACE": "wlan0", "ADDRESS": "10.0.0.1/24",
    }
    boot_status = (tmp_path / "boot-status.txt").read_text()
    assert "STATE=offline" in boot_status and "PROBLEMS_LEFT=hostapd-inactive" in boot_status
    assert "Giving up until the next boot" in (tmp_path / "ap-health.log").read_text()


def test_transient_failure_recovers_on_retry(kernel, tmp_path):
    kernel.unit("hostapd", active=False, failonce=True)
    kernel.put("beacon", "0")
    kernel.sync()
    result = run_health(kernel, tmp_path)
    assert result.returncode == 0, result.stdout
    assert repairs(kernel) == ["systemctl restart hostapd.service"] * 2
    assert kernel.ap_healthy()


def test_disabled_hostapd_is_reported_not_overruled(kernel, tmp_path):
    """Someone ran 'systemctl disable hostapd'. The health check notes it and keeps its hands off."""
    kernel.unit("hostapd", enabled=False, active=False)
    kernel.put("beacon", "0")
    kernel.sync()
    result = run_health(kernel, tmp_path, "--attempts", "2")
    assert result.returncode == 1
    assert repairs(kernel) == []
    assert status(tmp_path)["REASON"] == "hostapd-disabled"


def test_missing_interface_restarts_nothing(kernel, tmp_path):
    kernel.put("wlan", "0")
    kernel.sync()
    result = run_health(kernel, tmp_path, "--attempts", "2")
    assert result.returncode == 1
    assert repairs(kernel) == []
    assert status(tmp_path)["REASON"] == "no-interface"


def test_zero_attempts_only_checks(kernel, tmp_path):
    kernel.put("rfkill", "1")
    kernel.put("beacon", "0")
    kernel.sync()
    result = run_health(kernel, tmp_path, "--attempts", "0")
    assert result.returncode == 1
    assert repairs(kernel) == []


def test_repeated_boots_on_a_healthy_box_never_restart_anything(kernel, tmp_path):
    """Five boots, five checks, zero restarts. /run is wiped each time, like the real one."""
    for _ in range(5):
        shutil.rmtree(tmp_path / "run", ignore_errors=True)
        assert run_health(kernel, tmp_path).returncode == 0
    assert repairs(kernel) == []
    assert (tmp_path / "ap-health.log").read_text().count("No repairs needed") == 5


def test_bad_arguments_are_rejected(kernel, tmp_path):
    assert run_health(kernel, tmp_path, "--attempts", "lots").returncode == 2
    assert run_health(kernel, tmp_path, "--address", "10.0.0.1").returncode == 2


def test_health_check_writes_nothing_outside_its_own_files(kernel, tmp_path):
    """Recovery is runtime work. Configuration is somebody else's job."""
    before = {path for path in tmp_path.rglob("*") if "fake" not in path.parts}
    kernel.unit("dnsmasq", active=False)
    run_health(kernel, tmp_path)
    after = {path for path in tmp_path.rglob("*") if "fake" not in path.parts}
    allowed = {tmp_path / name for name in ("run", "ap-health.log", "boot-status.txt")}
    allowed |= {tmp_path / "run" / name for name in ("ap-status", "ap-health.lock")}
    assert after - before <= allowed


def test_status_file_path_is_what_the_epaper_reads():
    """Two programs, one file name, zero chance of drifting apart quietly."""
    script = HEALTH.read_text()
    epaper = (ROOT / "scripts" / "epaper_hat.py").read_text()
    assert "RUN_DIR=${PIRATEBOX_RUN_DIR:-/run/piratebox}" in script
    assert 'STATUS_FILE="${RUN_DIR}/ap-status"' in script
    assert 'Path("/run/piratebox/ap-status")' in epaper


def test_generated_units_order_rfkill_after_networkmanager():
    """The unblock has to land after NetworkManager re-applies its radio switch, or it is decoration."""
    setup = (ROOT / "scripts" / "pi-setup.sh").read_text()
    ap_ip = setup.split("piratebox-ap-ip.service\n", 1)[1].split("EOF", 1)[0]
    assert "After=sys-subsystem-net-devices-${WIFI_IFACE}.device NetworkManager.service" in ap_ip
    assert "Before=hostapd.service dnsmasq.service" in ap_ip
    health = setup.split("piratebox-ap-health.service\n", 1)[1].split("EOF", 1)[0]
    assert "After=NetworkManager.service piratebox-ap-ip.service hostapd.service dnsmasq.service" in health
    assert "piratebox-zero-firstboot.service" in health
    assert "Type=oneshot" in health and "Restart=" not in health
    nm_branch = setup.split("if unit_on NetworkManager; then", 1)[1].split("elif unit_on dhcpcd", 1)[0]
    assert nm_branch.index("enable_nm_radio") < nm_branch.index("systemctl restart NetworkManager")


def test_fake_kernel_is_not_the_repo(tmp_path):
    """Sanity: the fixture writes into tmp_path, never into the checkout."""
    k = FakeKernel(tmp_path)
    assert Path(k.state).is_relative_to(tmp_path)
