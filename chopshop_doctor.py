#!/usr/bin/env python3
"""chopshop_doctor - inspect this machine and recommend a local setup for the
classification stage of the chopshop pipeline.

Runs with nothing installed. Detects CPU, RAM, NVIDIA GPUs, and Apple Silicon,
then recommends a backend, device, and batch size. Writes ``chopshop.json`` so
the slicer (``chopshop.py``) can read the runtime config later.

    python chopshop_doctor.py                                  # detect + recommend + write config
    python chopshop_doctor.py --sounds "G:/My Drive/sounds"    # set the folder root
    python chopshop_doctor.py --install                        # actually run the pip installs
    python chopshop_doctor.py --fetch-model                    # pre-download the CLAP checkpoint
    python chopshop_doctor.py --json                           # machine-readable, no prose

The verdict in one line: CLAP runs on everything you own. The GPU just makes it
faster. This tool mostly decides device + batch size and flags slow machines.

Design notes
------------
* **Zero hard dependencies.** Every probe degrades gracefully if a library or
  system file is missing, so the tool can run on a bare interpreter before any
  ``pip install``.
* **Stable on-disk schema.** ``chopshop.json`` (see :class:`Config`) is a
  contract with the downstream slicer. Bump ``CONFIG_VERSION`` and update the
  reader if you change its shape.
* **Detection vs. recommendation are separate.** :func:`profile_host` only
  observes; :func:`recommend` only decides. Neither performs installs.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Schema version for chopshop.json. Bump on any breaking change to the layout
# emitted by Config.to_dict(); the slicer keys off this.
CONFIG_VERSION = 1

# Model identifiers surfaced to the user and written to the config.
CLAP_MODEL = "HTSAT-base (LAION-CLAP)"
YAMNET_MODEL = "YAMNet (tiny, AudioSet taxonomy)"

# Below this much RAM, a CPU-only host is steered to the lightweight YAMNet
# backend instead of CLAP.
LOW_RAM_GB = 6.0


# --------------------------------------------------------------------------- #
# host profile (detection only -- no side effects, no installs)
# --------------------------------------------------------------------------- #
@dataclass
class Gpu:
    """A single detected NVIDIA GPU."""

    name: str
    vram_mb: int


@dataclass
class TorchInfo:
    """What an already-installed torch reports about available devices.

    All fields default to "not installed / nothing available" so callers can
    treat a bare interpreter and a torch-less environment identically.
    """

    installed: bool = False
    cuda: bool = False
    mps: bool = False
    cuda_devices: int = 0


@dataclass
class Host:
    """Observed properties of the current machine.

    Produced by :func:`profile_host`. Purely descriptive: it records what is
    present, never what to do about it.
    """

    hostname: str
    os: str
    arch: str
    cpu: str
    cpu_cores: int | None
    ram_gb: float | None
    gpus: list[Gpu]
    apple_silicon: bool
    torch: TorchInfo
    clap_installed: bool

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def detect_ram_gb() -> float | None:
    """Total physical RAM in GB, or ``None`` if it can't be determined.

    Tries psutil, then Linux ``/proc/meminfo``, then the Windows
    ``GlobalMemoryStatusEx`` API via ctypes.
    """
    # Preferred: psutil, if it happens to be installed.
    try:
        import psutil  # type: ignore

        return round(psutil.virtual_memory().total / 1e9, 1)
    except Exception:
        pass

    # Linux: parse /proc/meminfo (MemTotal is in kB).
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemTotal:"):
                kb = int(line.split()[1])
                return round(kb * 1024 / 1e9, 1)

    # Windows: GlobalMemoryStatusEx via ctypes.
    if platform.system() == "Windows":
        try:
            import ctypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MemoryStatusEx()
            stat.dwLength = ctypes.sizeof(MemoryStatusEx)
            # The API returns nonzero on success; 0 means the call failed and
            # the struct holds garbage, so don't trust it.
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return round(stat.ullTotalPhys / 1e9, 1)
        except Exception:
            pass

    return None


def detect_nvidia() -> list[Gpu]:
    """List NVIDIA GPUs via ``nvidia-smi``, or ``[]`` if none / not present."""
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8,
        )
    except Exception:
        return []
    if out.returncode != 0:
        return []

    gpus: list[Gpu] = []
    for line in out.stdout.strip().splitlines():
        if not line.strip():
            continue
        name, _, mem = line.partition(",")
        try:
            vram = int(mem.strip())
        except ValueError:
            vram = 0  # couldn't parse memory; record the GPU anyway
        gpus.append(Gpu(name=name.strip(), vram_mb=vram))
    return gpus


def detect_apple_silicon() -> bool:
    """True on an arm64 macOS host (M-series)."""
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def detect_torch() -> TorchInfo:
    """If torch is already installed, get a definitive device read from it.

    A successful torch import is more authoritative than ``nvidia-smi`` because
    it reflects the actual CUDA/MPS build that will run inference.
    """
    info = TorchInfo()
    try:
        import torch  # type: ignore

        info.installed = True
        info.cuda = bool(torch.cuda.is_available())
        info.cuda_devices = torch.cuda.device_count() if info.cuda else 0
        info.mps = bool(getattr(torch.backends, "mps", None)
                        and torch.backends.mps.is_available())
    except Exception:
        pass
    return info


def detect_clap_installed() -> bool:
    """True if ``laion_clap`` can be imported."""
    try:
        import laion_clap  # type: ignore  # noqa: F401

        return True
    except Exception:
        return False


def profile_host() -> Host:
    """Probe the current machine and return a :class:`Host` snapshot."""
    return Host(
        hostname=platform.node(),
        os=f"{platform.system()} {platform.release()}",
        arch=platform.machine(),
        cpu=platform.processor() or platform.machine(),
        cpu_cores=os.cpu_count(),
        ram_gb=detect_ram_gb(),
        gpus=detect_nvidia(),
        apple_silicon=detect_apple_silicon(),
        torch=detect_torch(),
        clap_installed=detect_clap_installed(),
    )


# --------------------------------------------------------------------------- #
# recommendation (pure decision logic -- depends only on a Host)
# --------------------------------------------------------------------------- #
@dataclass
class Recommendation:
    """The chosen runtime setup plus human-facing notes and helper commands."""

    backend: str            # "clap" | "yamnet"
    device: str             # "cuda" | "mps" | "cpu"
    batch_size: int
    model: str
    notes: list[str] = field(default_factory=list)
    install: list[str] = field(default_factory=list)
    run_hint: str = ""


def pick_batch(vram_mb: int) -> int:
    """Pick a CLAP batch size from available VRAM (0 means CPU)."""
    if vram_mb >= 20_000:
        return 64
    if vram_mb >= 12_000:
        return 48
    if vram_mb >= 8_000:
        return 24
    if vram_mb >= 1:
        return 8
    return 4


def recommend(host: Host) -> Recommendation:
    """Decide backend / device / batch size for ``host``.

    Priority order: NVIDIA CUDA, then Apple MPS, then CPU (CLAP normally, but
    YAMNet when RAM is tight). Fills in install commands and a run hint before
    returning.
    """
    notes: list[str] = []

    # NVIDIA GPU. Trust torch's CUDA read if present; otherwise trust the
    # nvidia-smi inventory. We need at least one entry in `gpus` to size the
    # batch, so a torch-reports-cuda-but-no-smi machine still needs the list.
    cuda_ok = host.torch.cuda or bool(host.gpus)
    if cuda_ok and host.gpus:
        best = max(host.gpus, key=lambda g: g.vram_mb)
        rec = Recommendation(
            backend="clap", device="cuda",
            batch_size=pick_batch(best.vram_mb), model=CLAP_MODEL,
        )
        notes.append(f"Using {best.name} ({best.vram_mb} MB). Plenty for CLAP.")
        if len(host.gpus) > 1:
            notes.append(f"{len(host.gpus)} GPUs found. chopshop uses one per "
                         f"run; run separate processes pinned with "
                         f"CUDA_VISIBLE_DEVICES to fan a big library across them.")
        return _finalize(rec, notes, host)

    # Apple Silicon (MPS).
    if host.apple_silicon:
        rec = Recommendation(
            backend="clap", device="mps", batch_size=16, model=CLAP_MODEL,
        )
        notes.append("Apple Silicon detected. CLAP runs on the MPS backend; "
                     "a few ops may fall back to CPU but it's fine for batches.")
        return _finalize(rec, notes, host)

    # CPU only. Steer low-RAM boxes to the lighter YAMNet model.
    ram = host.ram_gb or 0
    if ram and ram < LOW_RAM_GB:
        rec = Recommendation(
            backend="yamnet", device="cpu", batch_size=4, model=YAMNET_MODEL,
        )
        notes.append(f"Low RAM ({ram} GB) and no GPU. Recommending the tiny "
                     f"YAMNet model. Faster, but coarser categories and a fixed "
                     f"vocabulary instead of your own label list.")
        return _finalize(rec, notes, host)

    rec = Recommendation(
        backend="clap", device="cpu", batch_size=pick_batch(0), model=CLAP_MODEL,
    )
    notes.append("No GPU. CLAP still runs on CPU and keeps your custom labels, "
                 "it's just slower. Fine for a background watch-folder where "
                 "latency doesn't matter. If you want it snappier on this box, "
                 "re-run with --backend yamnet for the lightweight trade.")
    return _finalize(rec, notes, host)


def _finalize(rec: Recommendation, notes: list[str], host: Host) -> Recommendation:
    """Attach notes, install commands, and the run hint to a recommendation."""
    rec.notes = notes
    rec.install = install_commands(rec, host)
    rec.run_hint = run_hint(rec)
    return rec


def install_commands(rec: Recommendation, host: Host) -> list[str]:
    """The pip commands needed to satisfy ``rec`` on ``host``.

    Lines beginning with ``#`` are advisory comments, not runnable; the
    installer skips them. On Linux, pip lines get ``--break-system-packages`` so
    they don't bounce off PEP 668 externally-managed environments.
    """
    # numpy/soundfile/librosa do the slicing; ableton-device-creator builds the
    # Drum Racks at publish time (optional -- chopshop_build has a fallback).
    base = "pip install numpy soundfile librosa ableton-device-creator"
    if rec.backend == "yamnet":
        cmds = [base, "pip install tensorflow tensorflow-hub"]
    else:  # CLAP
        cmds = [base]
        if rec.device == "cuda":
            cmds.append("pip install laion-clap")
            cmds.append("# GPU torch (match your CUDA): "
                        "pip install torch --index-url "
                        "https://download.pytorch.org/whl/cu121")
        else:
            cmds.append("pip install laion-clap torch")

    if host.os.startswith("Linux"):
        cmds = [
            c.replace("pip install ", "pip install --break-system-packages ")
            if c.startswith("pip install") else c
            for c in cmds
        ]
    return cmds


def run_hint(rec: Recommendation) -> str:
    """The example launcher invocation shown in the report.

    Points at chopshop.py, which slices the inbox, opens the kids' web review
    app, then publishes the Ableton library -- all in one command.
    """
    return ("python chopshop.py --inbox \"<sounds>/inbox\" "
            "--library \"<sounds>/KidsSounds\" --config chopshop.json   "
            f"# device={rec.device}, batch={rec.batch_size}")


# --------------------------------------------------------------------------- #
# config (the chopshop.json contract with the slicer)
# --------------------------------------------------------------------------- #
@dataclass
class Config:
    """The serialized config consumed by the downstream slicer.

    Layout is versioned by :data:`CONFIG_VERSION`. ``to_dict`` defines the exact
    JSON shape; keep it stable.
    """

    host: Host
    rec: Recommendation
    sounds_root: str | None = None
    labels_file: str | None = None

    def to_dict(self) -> dict[str, Any]:
        paths: dict[str, str] = {}
        if self.sounds_root:
            rp = Path(self.sounds_root)
            paths = {
                "sounds_root": str(rp),
                "raw": str(rp / "raw"),
                "categorized": str(rp / "categorized"),
                "manifest": str(rp / "categorized" / "_manifest"),
                "processed_marker": str(rp / "categorized" / "_processed"),
            }
        return {
            "version": CONFIG_VERSION,
            "host": self.host.to_dict(),
            "runtime": {
                "backend": self.rec.backend,
                "device": self.rec.device,
                "batch_size": self.rec.batch_size,
                "model": self.rec.model,
            },
            "paths": paths,
            "labels_file": self.labels_file,
        }


# --------------------------------------------------------------------------- #
# side effects (installs and downloads -- only run when explicitly asked)
# --------------------------------------------------------------------------- #
def run_install(cmds: list[str]) -> None:
    """Execute install commands in order, stopping on the first failure.

    Comment lines (leading ``#``) are echoed and skipped.
    """
    for cmd in cmds:
        if cmd.strip().startswith("#"):
            print(f"\n  (skipping comment) {cmd}")
            continue
        print(f"\n$ {cmd}")
        rc = subprocess.run(cmd, shell=True).returncode
        if rc != 0:
            print(f"  ! command failed (exit {rc}). Stopping.", file=sys.stderr)
            return


def fetch_model() -> None:
    """Pre-download the LAION-CLAP checkpoint (~2 GB) into the local cache.

    Runs in a subprocess so a torch/laion_clap import here never affects the
    doctor's own dependency-free guarantee.
    """
    print("Pre-downloading the LAION-CLAP checkpoint (~2 GB)...")
    code = (
        "import laion_clap, torch; "
        "d='cuda' if torch.cuda.is_available() else "
        "('mps' if getattr(torch.backends,'mps',None) and "
        "torch.backends.mps.is_available() else 'cpu'); "
        "m=laion_clap.CLAP_Module(enable_fusion=False, device=d); "
        "m.load_ckpt(); print('checkpoint ready on', d)"
    )
    subprocess.run([sys.executable, "-c", code])


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def print_report(host: Host, rec: Recommendation) -> None:
    """Print the human-readable detection + recommendation summary."""
    line = "=" * 60
    print(line)
    print(f"  chopshop doctor  -  {host.hostname}")
    print(line)
    print(f"  OS        : {host.os} ({host.arch})")
    print(f"  CPU       : {host.cpu}  ({host.cpu_cores} cores)")
    print(f"  RAM       : {host.ram_gb} GB" if host.ram_gb
          else "  RAM       : unknown")
    if host.gpus:
        for g in host.gpus:
            print(f"  GPU       : {g.name}  ({g.vram_mb} MB)")
    elif host.apple_silicon:
        print("  GPU       : Apple Silicon (MPS)")
    else:
        print("  GPU       : none detected")
    if host.torch.installed:
        t = host.torch
        print(f"  torch     : installed (cuda={t.cuda}, mps={t.mps})")
    print(f"  CLAP      : {'installed' if host.clap_installed else 'not yet'}")
    print(line)
    print("  RECOMMENDATION")
    print(f"    backend : {rec.backend}")
    print(f"    model   : {rec.model}")
    print(f"    device  : {rec.device}")
    print(f"    batch   : {rec.batch_size}")
    for n in rec.notes:
        print(f"    note    : {n}")
    print(line)
    print("  INSTALL")
    for c in rec.install:
        print(f"    {c}")
    print(line)
    print("  RUN")
    print(f"    {rec.run_hint}")
    print(line)


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #
def apply_backend_override(rec: Recommendation, backend: str, host: Host) -> None:
    """Force ``rec`` onto a specific backend, in place.

    YAMNet has no GPU path in this tool, so forcing it also pins the device to
    CPU and resizes the batch. Install commands and the run hint are rebuilt to
    match. A no-op if ``backend`` is "auto" or already selected.
    """
    if backend == "auto" or backend == rec.backend:
        return

    rec.backend = backend
    if backend == "yamnet":
        rec.model = YAMNET_MODEL
        rec.device = "cpu"
        rec.batch_size = 4
    else:
        rec.model = CLAP_MODEL

    rec.install = install_commands(rec, host)
    rec.run_hint = run_hint(rec)
    rec.notes.append(f"Backend forced to {backend} by flag.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="chopshop_doctor",
        description="Detect hardware and recommend a local classification setup.",
    )
    p.add_argument("--sounds", type=str, default="",
                   help="sounds root folder (expects raw/ inside it)")
    p.add_argument("--config", type=str, default="chopshop.json",
                   help="where to write the config (default chopshop.json)")
    p.add_argument("--backend", choices=["auto", "clap", "yamnet"],
                   default="auto", help="force a backend instead of auto")
    p.add_argument("--install", action="store_true",
                   help="run the recommended pip installs")
    p.add_argument("--fetch-model", action="store_true",
                   help="pre-download the CLAP checkpoint after install")
    p.add_argument("--json", action="store_true",
                   help="print machine-readable JSON only")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    host = profile_host()
    rec = recommend(host)
    apply_backend_override(rec, args.backend, host)

    config = Config(host=host, rec=rec, sounds_root=args.sounds or None)
    config_json = json.dumps(config.to_dict(), indent=2)
    Path(args.config).write_text(config_json)

    if args.json:
        print(config_json)
    else:
        print_report(host, rec)
        print(f"\n  wrote {args.config}")
        if not args.sounds:
            print("  tip: re-run with --sounds \"<path to your sounds folder>\" "
                  "to fill in raw/ and categorized/ paths.")

    if args.install:
        run_install(rec.install)
    if args.fetch_model:
        fetch_model()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
