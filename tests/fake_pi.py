"""A pretend Raspberry Pi for boot tests: real PirateBox scripts, fake kernel, fake systemd, no soldering."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
FAKE_BIN = Path(__file__).resolve().parent / "fake-pi.sh"
MOCKS = (
    "systemctl", "rfkill", "ip", "iw", "nmcli", "raspi-config", "dpkg-query", "dpkg", "apt-get",
    "sysctl", "iptables", "curl", "sleep", "udevadm", "python3", "python", "fakeboot",
)
# Distro units we do not ship, reduced to the ordering that matters (Debian trixie unit files).
DISTRO_ORDER = {
    "NetworkManager": {"after": set(), "before": {"network.target"}},
    "hostapd": {"after": {"network.target"}, "before": set()},
    "dnsmasq": {"after": {"network-online.target"}, "before": set()},
    "network.target": {"after": set(), "before": set()},
    "network-online.target": {"after": {"network.target"}, "before": set()},
}
# When systemd is free to choose, the worst case starts our unblock first and NetworkManager last.
ADVERSARIAL = ["piratebox-ap-ip", "piratebox-zero-firstboot", "hostapd", "dnsmasq", "piratebox",
               "piratebox-epaper", "piratebox-ap-health", "NetworkManager"]


class FakeKernel:
    """State files and mocks for running scripts against an imaginary radio."""

    def __init__(self, base: Path, fake_root: Optional[Path] = None):
        self.base = base
        self.bin = base / "fake" / "bin"
        self.state = base / "fake" / "state"
        self.sys = base / "fake" / "sys"
        for path in (self.bin, self.state / "units", self.sys):
            path.mkdir(parents=True, exist_ok=True)
        for name in MOCKS:
            target = self.bin / name
            if not target.exists():
                # A copy, not a symlink, so it still works once the sandbox hides the repo path.
                shutil.copy(FAKE_BIN, target)
                target.chmod(0o755)
        # Paths as the scripts will see them: inside the sandbox, or right here on the host.
        self.inner = fake_root or base / "fake"

    def env(self, **extra: str) -> dict[str, str]:
        """Environment that points every script at the fake kernel."""
        inner = self.inner
        env = {
            "PATH": f"{inner / 'bin'}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "HOME": "/root",
            "LANG": "C.UTF-8",
            "FAKE_STATE": str(inner / "state"),
            "FAKE_SYSFS": str(inner / "sys"),
            "FAKE_MOCKS": str(inner / "bin"),
            "PIRATEBOX_SYSFS": str(inner / "sys"),
        }
        env.update(extra)
        return env

    def put(self, name: str, value: str) -> None:
        (self.state / name).write_text(f"{value}\n")

    def get(self, name: str, default: str = "0") -> str:
        try:
            return (self.state / name).read_text().strip()
        except OSError:
            return default

    def unit(self, name: str, *, active: Optional[bool] = None, enabled: Optional[bool] = None,
             fail: Optional[bool] = None, failonce: Optional[bool] = None) -> None:
        for flag, value in (("active", active), ("enabled", enabled), ("fail", fail), ("failonce", failonce)):
            path = self.state / "units" / f"{name}.{flag}"
            if value is True:
                path.touch()
            elif value is False:
                path.unlink(missing_ok=True)

    def is_active(self, name: str) -> bool:
        return (self.state / "units" / f"{name}.active").exists()

    def is_enabled(self, name: str) -> bool:
        return (self.state / "units" / f"{name}.enabled").exists()

    def addresses(self) -> list[str]:
        return [line for line in self.get("addr", "").splitlines() if line]

    def calls(self) -> list[str]:
        try:
            return (self.state / "calls.log").read_text().splitlines()
        except OSError:
            return []

    def clear_calls(self) -> None:
        (self.state / "calls.log").write_text("")

    def ap_healthy(self) -> bool:
        """What a phone would say: SSID on air, gateway answering, DHCP handing out leases."""
        return (
            self.get("beacon") == "1"
            and self.get("rfkill", "1") == "0"
            and "10.0.0.1/24" in self.addresses()
            and self.is_active("dnsmasq")
        )

    def sync(self) -> None:
        """Refresh the fake sysfs after poking state files directly."""
        subprocess.run([str(self.bin / "fakeboot"), "noop"], env=self.env(), check=True)


def sandbox_available() -> bool:
    """User namespaces plus bind mounts, or no sandbox. Ubuntu CI usually says no; that is fine."""
    if not sys.platform.startswith("linux") or not shutil.which("unshare") or not shutil.which("rsync"):
        return False
    try:
        result = subprocess.run(
            ["unshare", "-rmpf", "--mount-proc", "sh", "-c",
             'd=$(mktemp -d) && mount -t tmpfs tmpfs "$d" && mount --bind /usr "$d" && chroot / true'],
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


SANDBOX_SH = r"""
set -e
T="$1"
shift
R="$T/root"
mount -t tmpfs tmpfs "$R"
mkdir -p "$R"/usr "$R"/etc "$R"/var "$R"/boot "$R"/opt "$R"/proc "$R"/dev "$R"/run "$R"/tmp "$R"/fake "$R"/src
for d in bin sbin lib lib64 lib32 libx32; do
  if [ -L "/$d" ]; then ln -s "$(readlink "/$d")" "$R/$d"
  elif [ -d "/$d" ]; then mkdir -p "$R/$d"; mount --rbind "/$d" "$R/$d"
  fi
done
mount --rbind /usr "$R/usr"
mount --bind "$T/usrlocal" "$R/usr/local"
for d in etc var boot opt fake; do mount --bind "$T/$d" "$R/$d"; done
mount --bind "$T/src" "$R/src"
mount --rbind /dev "$R/dev"
mount -t proc proc "$R/proc"
mount -t tmpfs tmpfs "$R/run"
mount -t tmpfs tmpfs "$R/tmp"
exec chroot "$R" "$@"
"""


class FakePi(FakeKernel):
    """A whole pretend Zero W: SD card on disk, RAM in tmpfs, systemd reduced to a for loop."""

    def __init__(self, base: Path, src: Path = ROOT, nm_wireless_enabled: bool = False):
        super().__init__(base, fake_root=Path("/fake"))
        self.root = base
        for name in ("root", "etc", "var", "boot", "opt", "usrlocal", "src"):
            (base / name).mkdir(exist_ok=True)
        (base / "sandbox.sh").write_text(SANDBOX_SH)
        # The source tree goes in by copy, so a stray write can never land in the real repo.
        shutil.copytree(src, base / "src", dirs_exist_ok=True, ignore=shutil.ignore_patterns(
            ".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "data", "dist", "site", ".idea",
        ))
        self._flash(nm_wireless_enabled)
        self.boots = 0

    def path(self, inner: str) -> Path:
        """Map a path on the pretend SD card to the real file behind it."""
        inner = inner.lstrip("/")
        if inner.startswith("usr/local/"):
            return self.root / "usrlocal" / inner[len("usr/local/"):]
        return self.root / inner

    def _flash(self, nm_wireless_enabled: bool) -> None:
        """Lay out what Raspberry Pi OS Lite trixie ships, minus the parts we fake."""
        for inner in (
            "etc/systemd/system", "etc/default", "etc/hostapd", "etc/dnsmasq.d", "etc/sysctl.d",
            "etc/NetworkManager/conf.d", "var/lib/systemd/rfkill", "var/log", "boot/firmware",
            "usr/local/sbin", "usr/local/bin",
        ):
            self.path(inner).mkdir(parents=True, exist_ok=True)
        self.path("boot/firmware/config.txt").write_text("# pretend config.txt\n[all]\n")
        self.path("boot/firmware/cmdline.txt").write_text("console=tty1 root=PARTUUID=0 rootwait\n")
        # pi-gen stage2/02-net-tweaks writes exactly this when no country is baked in.
        radio = "true" if nm_wireless_enabled else "false"
        self.path("var/lib/NetworkManager").mkdir(parents=True, exist_ok=True)
        self.path("var/lib/NetworkManager/NetworkManager.state").write_text(f"[main]\nWirelessEnabled={radio}\n")
        self.unit("NetworkManager", enabled=True)

    def run(self, *argv: str, crash_at: str = "", timeout: int = 120, **env: str) -> subprocess.CompletedProcess:
        """Run a command on the pretend Pi. A crash_at mock pulls the plug when it is called."""
        full_env = self.env(
            FAKE_RFKILL_SAVE="/var/lib/systemd/rfkill/platform-20300000.mmcnr:wlan",
            FAKE_HOSTAPD_CONF="/etc/hostapd/hostapd.conf",
            **env,
        )
        if crash_at:
            full_env["FAKE_CRASH_AT"] = crash_at
        return subprocess.run(
            ["unshare", "-rmpf", "--mount-proc", "sh", str(self.root / "sandbox.sh"), str(self.root),
             "/usr/bin/env", "-i", *[f"{key}={value}" for key, value in full_env.items()], *argv],
            capture_output=True,
            text=True,
            timeout=timeout,
            start_new_session=True,
        )

    def install(self, *args: str) -> subprocess.CompletedProcess:
        """Run install-zero.sh the way the image builder or a human would."""
        return self.run("/bin/bash", "/src/scripts/zero/install-zero.sh", *args)

    def set_boot_config(self, **values: str) -> None:
        """Edit piratebox.txt like someone with a laptop and an SD card reader."""
        target = self.path("boot/firmware/piratebox.txt")
        lines = target.read_text().splitlines()
        for key, value in values.items():
            lines = [f"{key}={value}" if line.startswith(f"{key}=") else line for line in lines]
        target.write_text("\n".join(lines) + "\n")

    def _unit_order(self, name: str) -> dict[str, set[str]]:
        if name in DISTRO_ORDER:
            return DISTRO_ORDER[name]
        order: dict[str, set[str]] = {"after": set(), "before": set()}
        unit_file = self.path(f"etc/systemd/system/{name}.service")
        if unit_file.exists():
            for line in unit_file.read_text().splitlines():
                match = re.match(r"^(After|Before)=(.*)$", line.strip())
                if match:
                    for dep in match.group(2).split():
                        order[match.group(1).lower()].add(dep.removesuffix(".service"))
        return order

    def boot_order(self, adversarial: bool = True) -> list[str]:
        """Topologically sort enabled units the way systemd would, breaking ties as badly as possible."""
        units = {path.stem for path in (self.state / "units").glob("*.enabled")}
        nodes = units | {"network.target", "network-online.target"}
        deps: dict[str, set[str]] = {node: set() for node in nodes}
        for node in nodes:
            order = self._unit_order(node)
            deps[node] |= {dep for dep in order["after"] if dep in nodes}
            for dep in order["before"]:
                if dep in nodes:
                    deps[dep].add(node)
        rank = ADVERSARIAL if adversarial else list(reversed(ADVERSARIAL))
        priority = {name: index for index, name in enumerate(rank)}
        done: list[str] = []
        while len(done) < len(nodes):
            ready = [node for node in nodes if node not in done and deps[node] <= set(done)]
            if not ready:
                raise AssertionError(f"ordering cycle among {sorted(set(nodes) - set(done))}")
            ready.sort(key=lambda node: (priority.get(node, len(rank) // 2), node))
            done.append(ready[0])
        return done

    def boot(self, adversarial: bool = True, crash_at: str = "", late_block: bool = False,
             **env: str) -> subprocess.CompletedProcess:
        """Power on: volatile state resets, then every enabled unit starts in systemd's order.

        late_block re-blocks the radio right after hostapd starts, standing in for anything that
        flips rfkill behind our back (a late phy restored from a stale saved state, an NM restart).
        The exit code only means something when a crash_at mock pulled the plug.
        """
        self.boots += 1
        for stale in self.state.glob("boot-*.rc"):
            stale.unlink()
        self.clear_calls()
        steps = ["fakeboot reset"]
        for unit in self.boot_order(adversarial):
            steps.append(f"fakeboot start {unit}")
            if late_block and unit == "hostapd":
                steps.append("rfkill block wlan")
        steps.append("true")
        (self.base / "fake" / "boot.sh").write_text("\n".join(steps) + "\n")
        return self.run("/bin/bash", "/fake/boot.sh", crash_at=crash_at, **env)

    def health_calls(self) -> list[str]:
        """Calls made from the moment the boot-time health check started."""
        calls = self.calls()
        marker = "fakeboot start piratebox-ap-health"
        return calls[calls.index(marker) + 1:] if marker in calls else []

    def boot_rc(self, unit: str) -> str:
        return self.get(f"boot-{unit}.rc", "not-run")

    def read(self, inner: str) -> str:
        return self.path(inner).read_text()

    def exists(self, inner: str) -> bool:
        return self.path(inner).exists()


def health_env(kernel: FakeKernel, tmp: Path, **extra: str) -> dict[str, str]:
    """Environment for running piratebox-ap-health directly on the host against a fake kernel."""
    env = kernel.env(
        PIRATEBOX_RUN_DIR=str(tmp / "run"),
        PIRATEBOX_AP_HEALTH_LOG=str(tmp / "ap-health.log"),
        PIRATEBOX_AP_BOOT_STATUS=str(tmp / "boot-status.txt"),
        PIRATEBOX_AP_HEALTH_SETTLE="0",
    )
    env.update(extra)
    return env
