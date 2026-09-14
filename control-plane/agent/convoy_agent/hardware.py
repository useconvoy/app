"""Hardware inventory and live sensors (stdlib). Real Jetson readers and a deterministic simulator.
Missing sensors are reported as None (never 0). Nothing here assumes 8 GB or a GPU."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

_L4T_RE = re.compile(r"R(\d+)\s*\(release\),\s*REVISION:\s*([\d.]+)")


def read_meminfo() -> dict[str, float | None]:
    try:
        text = Path("/proc/meminfo").read_text()
    except OSError:
        return {"mem_total_mb": None, "mem_available_mb": None, "swap_total_mb": None, "swap_free_mb": None}
    vals: dict[str, float] = {}
    for line in text.splitlines():
        k, _, rest = line.partition(":")
        num = rest.strip().split(" ")[0]
        if num.isdigit():
            vals[k] = int(num) / 1024.0
    return {
        "mem_total_mb": vals.get("MemTotal"),
        "mem_available_mb": vals.get("MemAvailable"),
        "swap_total_mb": vals.get("SwapTotal"),
        "swap_free_mb": vals.get("SwapFree"),
    }


def parse_l4t_line(text: str) -> tuple[int, str] | None:
    """`# R36 (release), REVISION: 4.7, GCID: ..., BOARD: generic, EABI: aarch64, ...` -> (36, "36.4.7").
    The GCID/BOARD/DATE fields vary per image and are ignored; only the release and revision matter."""
    m = _L4T_RE.search(text or "")
    if not m:
        return None
    return int(m.group(1)), f"{m.group(1)}.{m.group(2)}"


def read_l4t() -> dict[str, Any]:
    out: dict[str, Any] = {
        "l4t_release": None,
        "l4t_major": None,
        "jetson_model": None,
        "cuda_version": None,
        "nvcc": None,
    }
    try:
        parsed = parse_l4t_line(Path("/etc/nv_tegra_release").read_text())
        if parsed:
            out["l4t_major"], out["l4t_release"] = parsed
    except OSError:
        pass
    for p in ("/proc/device-tree/model", "/sys/firmware/devicetree/base/model"):
        try:
            out["jetson_model"] = Path(p).read_bytes().rstrip(b"\0").decode("utf-8", "replace")
            break
        except OSError:
            continue
    try:
        import json

        v = json.loads(Path("/usr/local/cuda/version.json").read_text())
        out["cuda_version"] = (v.get("cuda") or {}).get("version")
    except Exception:
        try:
            text = Path("/usr/local/cuda/version.txt").read_text()
            m = re.search(r"(\d+\.\d+(\.\d+)?)", text)
            out["cuda_version"] = m.group(1) if m else None
        except OSError:
            pass
    out["nvcc"] = shutil.which("nvcc")
    return out


def query_gpu(binary: str | None, timeout_s: float = 5.0) -> dict[str, Any]:
    """Measured GPU identity from the driver: `nvidia-smi --query-gpu=name,compute_cap` (on the Jetson
    Orin Nano this answers "Orin (nvgpu), 8.7"). Memory is NOT taken from here (it reports N/A on the
    unified-memory Jetson; /proc/meminfo is the source). Missing tool or any failure -> both None."""
    out: dict[str, Any] = {"gpu_name": None, "compute_capability": None}
    if not binary:
        return out
    try:
        r = subprocess.run(
            [binary, "--query-gpu=name,compute_cap", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return out
    if r.returncode != 0:
        return out
    line = (r.stdout or "").strip().splitlines()
    if not line:
        return out
    parts = [x.strip() for x in line[0].split(",")]
    if len(parts) >= 2 and re.fullmatch(r"\d+\.\d+", parts[1]):
        out["gpu_name"] = parts[0] or None
        out["compute_capability"] = parts[1]
    return out


def inventory() -> dict[str, Any]:
    mem = read_meminfo()
    inv = {
        "arch": platform.machine(),
        "os": platform.system().lower(),
        "kernel": platform.release(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "mem_total_mb": mem["mem_total_mb"],
        "swap_total_mb": mem["swap_total_mb"],
        "nvidia_smi": shutil.which("nvidia-smi"),
        "tegrastats": shutil.which("tegrastats"),
        "simulated": False,
    }
    inv.update(read_l4t())
    inv.update(query_gpu(inv["nvidia_smi"]))
    return inv


def boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return f"pid-{os.getpid()}-{int(time.time())}"


def disk_free_mb(path: str) -> float | None:
    try:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize / (1024 * 1024)
    except OSError:
        return None


def read_sysfs_text(path: Path, limit: int = 256) -> str | None:
    """One bounded raw read of a sysfs attribute, None when it cannot be read. sysfs attributes are
    read with os.read rather than a text stream: on L4T some thermal zones answer a read with an
    error or no data, and a TextIOWrapper then fails inside the decoder with a TypeError ("cannot
    concat NoneType to bytes", seen on L4T 36.4.7) instead of an OSError. Every failure mode of one
    attribute (OSError, ValueError, TypeError, decode error) is contained here."""
    fd = None
    try:
        fd = os.open(str(path), os.O_RDONLY)
        data = os.read(fd, limit)
        if not data:
            return None
        return data.decode("utf-8", "replace").strip()
    except (OSError, ValueError, TypeError):
        return None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def read_thermal(base: Path = Path("/sys/class/thermal")) -> dict[str, float]:
    """Thermal zones that answered with a readable integer millidegree value. A zone that cannot be
    read (absent, unsupported, EAGAIN, permission) is left out: it is unknown, never 0, and never
    prevents the other zones from being reported."""
    out: dict[str, float] = {}
    try:
        zones = sorted(base.glob("thermal_zone*"))
    except OSError:
        return out
    for z in zones:
        try:
            raw = read_sysfs_text(z / "temp")
            name = read_sysfs_text(z / "type")
            if raw is None or not name:
                continue
            out[name] = int(raw) / 1000.0
        except Exception:  # noqa: BLE001 - one zone's failure is that zone's unknown, nothing more
            continue
    return out


_TEGRA_RAM = re.compile(r"RAM (\d+)/(\d+)MB")
_TEGRA_GR3D = re.compile(r"GR3D_FREQ (\d+)%")
_TEGRA_POWER = re.compile(r"(VDD_IN|VDD_CPU_GPU_CV|VDD_SOC|POM_5V_IN) (\d+)mW")
_TEGRA_TEMP = re.compile(r"([A-Za-z0-9_]+)@([\d.]+)C")


def parse_tegrastats(line: str) -> dict[str, Any]:
    """Parse one tegrastats line. Values not present are None."""
    out: dict[str, Any] = {
        "ram_used_mb": None,
        "ram_total_mb": None,
        "gpu_pct": None,
        "power_w": None,
        "temps": {},
        "power_rails_mw": {},
    }
    m = _TEGRA_RAM.search(line)
    if m:
        out["ram_used_mb"], out["ram_total_mb"] = float(m.group(1)), float(m.group(2))
    m = _TEGRA_GR3D.search(line)
    if m:
        out["gpu_pct"] = float(m.group(1))
    for name, mw in _TEGRA_POWER.findall(line):
        out["power_rails_mw"][name] = float(mw)
    if "VDD_IN" in out["power_rails_mw"]:
        out["power_w"] = out["power_rails_mw"]["VDD_IN"] / 1000.0
    elif "POM_5V_IN" in out["power_rails_mw"]:
        out["power_w"] = out["power_rails_mw"]["POM_5V_IN"] / 1000.0
    for name, c in _TEGRA_TEMP.findall(line):
        out["temps"][name] = float(c)
    return out


def read_tegrastats_sample(binary: str, interval_ms: int = 500, timeout_s: float = 2.5) -> str | None:
    """tegrastats is a continuous collector that never exits: start it, read ONE complete line with a
    bounded wait, then terminate and reap it. Returns None only when no complete line arrived (R30)."""
    import select

    try:
        proc = subprocess.Popen(
            [binary, "--interval", str(interval_ms)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return None
    line: str | None = None
    try:
        deadline = time.monotonic() + timeout_s
        buf = b""
        while time.monotonic() < deadline and proc.stdout is not None:
            ready, _, _ = select.select([proc.stdout], [], [], max(0.0, deadline - time.monotonic()))
            if not ready:
                break
            chunk = os.read(proc.stdout.fileno(), 4096)
            if not chunk:
                break
            buf += chunk
            if b"\n" in buf:
                line = buf.split(b"\n")[0].decode("utf-8", "replace").strip()
                break
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=1.0)
            except Exception:
                pass
    return line or None


class Sensors:
    """Live sampler. On a Jetson it runs `tegrastats --interval` once per sample (bounded), reads
    /proc/meminfo and thermal zones. Elsewhere it reports what exists and None for the rest."""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.tegrastats = shutil.which("tegrastats")
        self._last_cpu: tuple[float, float] | None = None

    def cpu_pct(self) -> float | None:
        try:
            parts = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
            vals = [float(x) for x in parts]
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
            total = sum(vals)
        except (OSError, ValueError, IndexError):
            return None
        prev = self._last_cpu
        self._last_cpu = (idle, total)
        if not prev or total == prev[1]:
            return None
        return max(0.0, min(100.0, 100.0 * (1.0 - (idle - prev[0]) / (total - prev[1]))))

    def sample(self, runtime_state: str | None = None) -> dict[str, Any]:
        """One sample. Each sensor family is read on its own: a reader that raises leaves its fields
        None (unknown) and its error text under `sensor_errors`, and never stops the report."""
        errors: dict[str, str] = {}

        def guarded(name: str, fn: Callable[[], Any], default: Any) -> Any:
            try:
                return fn()
            except Exception as e:  # noqa: BLE001 - one bad sensor must not kill every report
                errors[name] = f"{type(e).__name__}: {e}"[:200]
                return default

        mem = guarded("meminfo", read_meminfo, {"mem_total_mb": None, "mem_available_mb": None, "swap_total_mb": None, "swap_free_mb": None})  # fmt: skip
        temps: dict[str, float] = guarded("thermal", read_thermal, {})
        s: dict[str, Any] = {
            "mem_total_mb": mem["mem_total_mb"],
            "mem_available_mb": mem["mem_available_mb"],
            "swap_used_mb": (mem["swap_total_mb"] - mem["swap_free_mb"])
            if (mem["swap_total_mb"] is not None and mem["swap_free_mb"] is not None)
            else None,
            "cpu_pct": guarded("cpu", self.cpu_pct, None),
            "gpu_pct": None,
            "power_w": None,
            "temp_max_c": max(temps.values()) if temps else None,
            "temps": temps,
            "disk_free_mb": guarded("disk", lambda: disk_free_mb(self.data_dir), None),
            "runtime_state": runtime_state,
            "clock_confidence": "unknown",
        }
        if self.tegrastats:
            line = guarded("tegrastats", lambda: read_tegrastats_sample(self.tegrastats), None)
            if line:
                t = guarded("tegrastats_parse", lambda: parse_tegrastats(line), None)
                if t:
                    s["gpu_pct"] = t["gpu_pct"]
                    s["power_w"] = t["power_w"]
                    if t["temps"]:
                        s["temps"].update(t["temps"])
                        s["temp_max_c"] = max(s["temps"].values())
        if errors:
            s["sensor_errors"] = errors
        return s


class SimulatedSensors(Sensors):
    """Deterministic simulated Orin Nano: 7620 MiB pool, OS baseline ~1100 MiB, runtime footprint
    supplied by the simulated runtime, thermal/power that respond to load. Values are still reported as
    measurements with `simulated: true` so the UI can never confuse them with hardware."""

    def __init__(self, data_dir: str, seed: int = 1):
        super().__init__(data_dir)
        self.seed = seed
        self.t0 = time.monotonic()
        self.runtime_mb = 0.0
        self.load = 0.0
        self.faults: dict[str, Any] = {}

    def sample(self, runtime_state: str | None = None) -> dict[str, Any]:
        t = time.monotonic() - self.t0
        baseline = 1100.0 + 40.0 * ((self.seed * 7) % 5)
        used = baseline + self.runtime_mb + 120.0 * self.load
        total = float(self.faults.get("mem_total_mb", 7620.0))
        avail = max(0.0, total - used)
        return {
            "mem_total_mb": total,
            "mem_available_mb": avail,
            "swap_used_mb": 0.0,
            "cpu_pct": min(100.0, 8.0 + 60.0 * self.load),
            "gpu_pct": min(100.0, 95.0 * self.load) if self.runtime_mb else 0.0,
            "power_w": round(4.5 + 8.0 * self.load + (0.5 if self.runtime_mb else 0.0), 2),
            "temp_max_c": round(float(self.faults.get("temp_c", 41.0 + 18.0 * self.load + (t % 7) * 0.1)), 1),
            "temps": {"cpu": 40.0 + 15.0 * self.load, "gpu": 41.0 + 18.0 * self.load},
            "disk_free_mb": float(self.faults.get("disk_free_mb", disk_free_mb(self.data_dir) or 50000.0)),
            "runtime_state": runtime_state,
            "clock_confidence": "simulated",
            "simulated": True,
        }


def simulated_inventory(seed: int = 1) -> dict[str, Any]:
    return {
        "arch": "simulated", "os": "simulated", "kernel": "sim", "python": platform.python_version(), "cpu_count": 6, "mem_total_mb": 7620.0,
        "swap_total_mb": 0.0, "nvidia_smi": None, "tegrastats": None, "l4t_release": None, "l4t_major": None, "jetson_model": "Simulated Orin Nano (no hardware)",
        "cuda_version": None, "nvcc": None, "gpu_name": None, "compute_capability": None, "simulated": True, "seed": seed,
    }  # fmt: skip
