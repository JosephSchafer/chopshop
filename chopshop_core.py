#!/usr/bin/env python3
"""chopshop_core - the audio engine behind the kids' Sound Safari pipeline.

This module does the two heavy jobs and nothing else (no UI, no installs):

  1. SLICE  : onset detection + trailing-silence trim (librosa). Carves a long
              field recording full of hits-and-gaps into individual one-shots.
  2. SORT   : zero-shot classification with LAION-CLAP against a *real-world*
              label vocabulary (water, metal, wood, voice, ...). You supply the
              labels; it picks the best match per slice. No training.

It is imported by the rest of the pipeline:

  chopshop_doctor.py  -> detects hardware, writes chopshop.json   (setup)
  chopshop_core.py    -> slice + classify into a staging area     (this file)
  chopshop_web.py     -> kids review/keep/recategorize the staging (front-end)
  chopshop_build.py   -> publish approved sounds + build .adg racks (output)
  chopshop.py         -> thin launcher that runs the whole flow

Staging contract
----------------
:func:`slice_folder` writes candidate WAVs plus ``staging.json`` into the
staging directory. The web app reads/writes that same file; the builder reads it
to publish. Its shape is defined by :class:`Slice` and :func:`write_staging`.

Config contract
---------------
:func:`load_runtime` reads the ``runtime`` block of ``chopshop.json`` (written
by chopshop_doctor.py) so the device/batch the doctor recommended are actually
honored here -- including Apple's MPS backend, which the old code ignored.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np
import soundfile as sf
import librosa


# --------------------------------------------------------------------------- #
# constants
# --------------------------------------------------------------------------- #
# Real-world / kid-friendly default vocabulary for "sounds found in the world."
# CLAP matches short descriptive phrases better than bare nouns, so each label
# is a phrase. Each entry also carries a kid-facing emoji used by the web app.
# (slug, clap_phrase, emoji, friendly_name)
KID_LABELS: list[tuple[str, str, str, str]] = [
    ("water",    "the sound of water splashing or dripping", "💧", "Water"),
    ("metal",    "a metallic clang or ringing metal sound",  "🔔", "Metal"),
    ("wood",     "a wooden knock or tapping on wood",        "🪵", "Wood"),
    ("glass",    "the sound of glass clinking or breaking",  "🥃", "Glass"),
    ("paper",    "paper crinkling or tearing",               "📄", "Paper"),
    ("plastic",  "a plastic crinkle or tap",                 "🧴", "Plastic"),
    ("voice",    "a human voice talking or singing",         "🗣️", "Voice"),
    ("mouth",    "mouth sounds, whistling or popping",       "👄", "Mouth"),
    ("animal",   "an animal call or pet sound",              "🐾", "Animal"),
    ("bird",     "a bird chirping or tweeting",              "🐦", "Bird"),
    ("nature",   "outdoor nature sounds like wind or leaves","🍃", "Nature"),
    ("vehicle",  "a car, engine or vehicle sound",           "🚗", "Vehicle"),
    ("kitchen",  "kitchen sounds, dishes or cooking",        "🍳", "Kitchen"),
    ("door",     "a door opening, closing or creaking",      "🚪", "Door"),
    ("bell",     "a bell or chime ringing",                  "🛎️", "Bell"),
    ("scrape",   "a scraping or scratching sound",           "🪚", "Scrape"),
    ("hit",      "a hard hit, thump or impact",              "💥", "Hit"),
    ("squeak",   "a high squeak or squeal",                  "🐭", "Squeak"),
    ("rustle",   "soft rustling or shuffling",               "🌾", "Rustle"),
    ("toy",      "a toy beeping or rattling",                "🧸", "Toy"),
]

# CLAP wants 48 kHz mono.
CLAP_SR = 48_000

AUDIO_EXTS = {".wav", ".flac", ".aif", ".aiff", ".ogg", ".mp3", ".m4a"}

STAGING_FILE = "staging.json"
STAGING_VERSION = 1

# Default Google Drive desktop mount on Windows. The shared, backed-up bits
# (inbox + the published library) live under here; the throwaway _staging set
# deliberately does NOT, so mid-review WAV churn never fights Drive sync.
DRIVE_ROOT = Path("G:/My Drive/SoundSafari")


def drive_inbox() -> Path:
    """Where the kids' raw recordings land (synced via Drive)."""
    return DRIVE_ROOT / "inbox"


def drive_library() -> Path:
    """The Ableton-ready library to back up + share (synced via Drive)."""
    return DRIVE_ROOT / "library"


def local_staging() -> Path:
    """Throwaway working area, kept OFF Drive next to the scripts."""
    return Path(__file__).resolve().parent / "_staging"


def default_clap_labels() -> list[str]:
    """The CLAP phrase list from the default kid vocabulary."""
    return [phrase for _slug, phrase, _emoji, _name in KID_LABELS]


def slug_for_phrase(phrase: str) -> str:
    """Map a CLAP phrase back to its category slug (falls back to slugify)."""
    for slug, ph, _emoji, _name in KID_LABELS:
        if ph == phrase:
            return slug
    return slugify(phrase)


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #
@dataclass
class Slice:
    """One candidate sound carved from a source recording.

    Lives in the staging area until a kid approves it in the web app. Fields
    after ``staging_path`` are filled/edited during review and publish.
    """

    source: str           # original recording filename
    index: int            # nth slice within that source
    start_sec: float
    end_sec: float
    sr: int               # native sample rate of the staged wav
    staging_path: str     # wav path relative to the staging dir

    # classification (proposed by CLAP, confirmable by a kid)
    category: str = "unsorted"     # slug, e.g. "water"
    confidence: float = 0.0

    # review state set by the web app
    status: str = "pending"        # "pending" | "keep" | "trash"
    custom_name: str = ""          # optional kid-given name

    # set at publish time by chopshop_build.py
    out_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Slice":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Runtime:
    """Resolved inference settings (from chopshop.json or sensible defaults)."""

    backend: str = "clap"     # "clap" | "yamnet"
    device: str = "cpu"       # "cuda" | "mps" | "cpu"
    batch_size: int = 8
    model: str = "HTSAT-base (LAION-CLAP)"


# --------------------------------------------------------------------------- #
# io helpers
# --------------------------------------------------------------------------- #
def load_mono(path: Path) -> tuple[np.ndarray, int]:
    """Load any supported file as mono float32, preserving native sample rate."""
    y, sr = librosa.load(str(path), sr=None, mono=True)
    return y.astype(np.float32), int(sr)


def slugify(text: str) -> str:
    """Filesystem/URL-safe lowercase slug; never empty."""
    s = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[\s_-]+", "-", s) or "unsorted"


def load_runtime(config_path: Optional[Path]) -> Runtime:
    """Read the ``runtime`` block of chopshop.json, or return defaults.

    Honors whatever chopshop_doctor.py recommended -- including ``device: mps``
    on Apple Silicon, which the previous slicer silently dropped.
    """
    rt = Runtime()
    if not config_path:
        return rt
    try:
        data = json.loads(Path(config_path).read_text())
    except Exception:
        return rt
    block = data.get("runtime", {})
    rt.backend = block.get("backend", rt.backend)
    rt.device = block.get("device", rt.device)
    rt.batch_size = int(block.get("batch_size", rt.batch_size))
    rt.model = block.get("model", rt.model)
    return rt


# --------------------------------------------------------------------------- #
# stage 1: slicing
# --------------------------------------------------------------------------- #
def detect_bounds(
    y: np.ndarray,
    sr: int,
    *,
    sensitivity: float = 0.5,
    pre_roll_ms: float = 6.0,
    min_len_ms: float = 40.0,
    max_len_ms: float = 4000.0,
    silence_db: float = -45.0,
) -> list[tuple[int, int]]:
    """Return (start_sample, end_sample) for each detected sound.

    Onsets mark the attack of each hit. A segment runs from just before its
    onset to either the next onset or the point where the tail drops below the
    silence floor, whichever comes first. That keeps a sparse hit from dragging
    a couple seconds of dead air behind it -- important for messy iPad takes.
    """
    if y.size == 0:
        return []

    hop = 512
    # delta scales inversely with sensitivity: higher sensitivity -> smaller
    # delta -> more onsets caught.
    delta = float(np.interp(sensitivity, [0.0, 1.0], [0.20, 0.02]))
    onset_frames = librosa.onset.onset_detect(
        y=y, sr=sr, hop_length=hop, backtrack=True, delta=delta, units="frames"
    )
    if len(onset_frames) == 0:
        return []

    onsets = librosa.frames_to_samples(onset_frames, hop_length=hop).tolist()

    pre = int(sr * pre_roll_ms / 1000.0)
    min_len = int(sr * min_len_ms / 1000.0)
    max_len = int(sr * max_len_ms / 1000.0)
    n = len(y)

    # frame-wise rms in dB for tail trimming
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    ref = max(rms.max(), 1e-9)
    rms_db = 20.0 * np.log10(np.maximum(rms, 1e-9) / ref)

    bounds: list[tuple[int, int]] = []
    for i, onset in enumerate(onsets):
        start = max(0, onset - pre)
        hard_end = onsets[i + 1] if i + 1 < len(onsets) else n
        hard_end = min(hard_end, start + max_len, n)

        # walk frames from the onset toward hard_end; cut once the tail has
        # stayed below the silence floor for a beat (3 consecutive frames).
        end = hard_end
        f0 = onset // hop
        f1 = hard_end // hop
        quiet_run = 0
        for f in range(f0, f1):
            if rms_db[min(f, len(rms_db) - 1)] < silence_db:
                quiet_run += 1
                if quiet_run >= 3:
                    end = min(f * hop, hard_end)
                    break
            else:
                quiet_run = 0

        if end - start >= min_len:
            bounds.append((start, end))

    return bounds


def carve(
    y: np.ndarray,
    sr: int,
    start: int,
    end: int,
    *,
    fade_in_ms: float = 2.0,
    fade_out_ms: float = 8.0,
) -> np.ndarray:
    """Cut [start:end] and apply short fades so slices don't click."""
    seg = np.array(y[start:end], dtype=np.float32)
    fi = min(int(sr * fade_in_ms / 1000.0), len(seg) // 2)
    fo = min(int(sr * fade_out_ms / 1000.0), len(seg) // 2)
    if fi > 0:
        seg[:fi] *= np.linspace(0.0, 1.0, fi, dtype=np.float32)
    if fo > 0:
        seg[-fo:] *= np.linspace(1.0, 0.0, fo, dtype=np.float32)
    return seg


# --------------------------------------------------------------------------- #
# stage 2: classification
# --------------------------------------------------------------------------- #
class ClapSorter:
    """Zero-shot classifier over a fixed label vocabulary using LAION-CLAP.

    ``runtime`` carries the device picked by chopshop_doctor.py. We trust it
    (cuda/mps/cpu) but fall back to a safe auto-detect if torch disagrees, so a
    stale config can never crash the run.
    """

    def __init__(self, labels: list[str], runtime: Runtime,
                 ckpt: Optional[str] = None, temperature: float = 0.05):
        import torch  # lazy: only needed when actually classifying
        import laion_clap

        self.labels = labels
        self.temperature = temperature
        self.device = self._resolve_device(runtime.device, torch)
        self._torch = torch

        self.model = laion_clap.CLAP_Module(enable_fusion=False, device=self.device)
        if ckpt:
            self.model.load_ckpt(ckpt)
        else:
            self.model.load_ckpt()  # default checkpoint

        text_embed = self.model.get_text_embedding(labels, use_tensor=False)
        self.text_embed = self._l2(np.asarray(text_embed, dtype=np.float32))

    @staticmethod
    def _resolve_device(requested: str, torch) -> str:
        """Honor the requested device if truly available, else degrade safely."""
        if requested == "cuda" and torch.cuda.is_available():
            return "cuda"
        if requested == "mps" and getattr(torch.backends, "mps", None) \
                and torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"

    @staticmethod
    def _l2(x: np.ndarray) -> np.ndarray:
        return x / np.clip(np.linalg.norm(x, axis=-1, keepdims=True), 1e-9, None)

    @staticmethod
    def _softmax(x: np.ndarray) -> np.ndarray:
        e = np.exp(x - x.max(axis=-1, keepdims=True))
        return e / e.sum(axis=-1, keepdims=True)

    def _resample(self, audio: np.ndarray, sr: int) -> np.ndarray:
        if sr != CLAP_SR:
            audio = librosa.resample(audio, orig_sr=sr, target_sr=CLAP_SR)
        return audio.astype(np.float32)

    def classify(self, audio: np.ndarray, sr: int) -> tuple[str, float]:
        """Classify one slice. Returns (label_phrase, confidence)."""
        label, conf = self.classify_batch([self._resample(audio, sr)])[0]
        return label, conf

    def classify_batch(self, audios_48k: list[np.ndarray]) -> list[tuple[str, float]]:
        """Classify several already-48k slices at once.

        Uses the batch size implied by the caller; CLAP pads to the longest
        clip in the list, so callers should keep batches reasonably uniform.
        This is the batched path the old single-slice code never used.
        """
        if not audios_48k:
            return []
        width = max(len(a) for a in audios_48k)
        batch = np.zeros((len(audios_48k), width), dtype=np.float32)
        for i, a in enumerate(audios_48k):
            batch[i, : len(a)] = a

        emb = self.model.get_audio_embedding_from_data(x=batch, use_tensor=False)
        emb = self._l2(np.asarray(emb, dtype=np.float32))

        sims = emb @ self.text_embed.T                 # (n, n_labels) cosine sims
        probs = self._softmax(sims / self.temperature)
        out: list[tuple[str, float]] = []
        for row in probs:
            best = int(np.argmax(row))
            out.append((self.labels[best], float(row[best])))
        return out


# --------------------------------------------------------------------------- #
# pipeline -> staging
# --------------------------------------------------------------------------- #
def gather_inputs(path: Path) -> list[Path]:
    """All supported audio files under ``path`` (or just the file itself)."""
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*") if p.suffix.lower() in AUDIO_EXTS)


def slice_folder(
    inputs: list[Path],
    staging_dir: Path,
    sorter: Optional[ClapSorter],
    *,
    slice_kwargs: dict,
    batch_size: int = 8,
) -> list[Slice]:
    """Slice every input into staging WAVs and (optionally) classify them.

    Writes each candidate slice as a native-rate WAV under ``staging_dir`` and
    records a :class:`Slice`. Nothing is published yet -- the web app reviews
    this staging set, then chopshop_build.py emits the final library.
    """
    staging_dir.mkdir(parents=True, exist_ok=True)
    results: list[Slice] = []

    # accumulate (Slice, 48k-audio) for batched classification
    pending: list[tuple[Slice, np.ndarray]] = []

    def flush() -> None:
        if not sorter or not pending:
            pending.clear()
            return
        preds = sorter.classify_batch([a for _s, a in pending])
        for (s, _a), (phrase, conf) in zip(pending, preds):
            s.category = slug_for_phrase(phrase)
            s.confidence = round(conf, 4)
        pending.clear()

    for src in inputs:
        try:
            y, sr = load_mono(src)
        except Exception as exc:
            print(f"  ! skipping {src.name}: {exc}", file=sys.stderr)
            continue

        bounds = detect_bounds(y, sr, **slice_kwargs)
        print(f"  {src.name}: {len(bounds)} slices")

        for idx, (start, end) in enumerate(bounds):
            seg = carve(y, sr, start, end)
            rel = f"{src.stem}_{idx:03d}.wav"
            dest = staging_dir / rel
            sf.write(str(dest), seg, sr, subtype="PCM_24")

            rec = Slice(
                source=src.name,
                index=idx,
                start_sec=round(start / sr, 4),
                end_sec=round(end / sr, 4),
                sr=sr,
                staging_path=rel,
            )
            results.append(rec)

            if sorter is not None:
                pending.append((rec, sorter._resample(seg, sr)))
                if len(pending) >= batch_size:
                    flush()

    flush()
    write_staging(staging_dir, results)
    return results


def write_staging(staging_dir: Path, rows: list[Slice]) -> None:
    """Persist the staging manifest the web app and builder both read."""
    payload = {
        "version": STAGING_VERSION,
        "slices": [r.to_dict() for r in rows],
    }
    (staging_dir / STAGING_FILE).write_text(json.dumps(payload, indent=2))


def read_staging(staging_dir: Path) -> list[Slice]:
    """Load the staging manifest, or [] if none exists."""
    path = staging_dir / STAGING_FILE
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return [Slice.from_dict(d) for d in data.get("slices", [])]


# --------------------------------------------------------------------------- #
# cli (slice stage on its own; the launcher usually drives this)
# --------------------------------------------------------------------------- #
def load_labels(labels: str, labels_file: str) -> list[str]:
    """Resolve the CLAP label phrases from flags, or the kid default set."""
    if labels_file:
        text = Path(labels_file).read_text()
        return [ln.strip() for ln in text.splitlines() if ln.strip()]
    if labels:
        return [s.strip() for s in labels.split(",") if s.strip()]
    return default_clap_labels()


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="chopshop_core",
        description="Slice recordings into staged one-shots and classify them.",
    )
    p.add_argument("input", type=Path, help="audio file or folder of recordings")
    p.add_argument("--staging", type=Path, default=Path("./_staging"),
                   help="staging directory (default ./_staging)")
    p.add_argument("--config", type=Path, default=None,
                   help="chopshop.json from the doctor (device/batch)")
    p.add_argument("--dry-run", action="store_true",
                   help="slice only, skip classification (no model load)")

    g = p.add_argument_group("slicing")
    g.add_argument("-s", "--sensitivity", type=float, default=0.5,
                   help="0.0 sparse .. 1.0 catches everything (default 0.5)")
    g.add_argument("--min-len", type=float, default=40.0, help="min slice ms")
    g.add_argument("--max-len", type=float, default=4000.0, help="max slice ms")
    g.add_argument("--silence-db", type=float, default=-45.0,
                   help="tail trim floor in dBFS (default -45)")

    c = p.add_argument_group("classification")
    c.add_argument("--labels", type=str, default="",
                   help="comma-separated CLAP label phrases")
    c.add_argument("--labels-file", type=str, default="",
                   help="path to a file with one label phrase per line")
    c.add_argument("--ckpt", type=str, default="",
                   help="path to a local CLAP .pt checkpoint")

    args = p.parse_args(argv)

    if not args.input.exists():
        print(f"input not found: {args.input}", file=sys.stderr)
        return 1
    inputs = gather_inputs(args.input)
    if not inputs:
        print("no audio files found", file=sys.stderr)
        return 1

    runtime = load_runtime(args.config)

    sorter = None
    if not args.dry_run:
        labels = load_labels(args.labels, args.labels_file)
        print(f"loading CLAP ({len(labels)} labels) on {runtime.device}...")
        try:
            sorter = ClapSorter(labels, runtime, ckpt=args.ckpt or None)
        except ImportError:
            print("laion-clap / torch not installed. Use --dry-run or run "
                  "chopshop_doctor.py --install", file=sys.stderr)
            return 1
        print(f"  device: {sorter.device}")

    slice_kwargs = dict(
        sensitivity=args.sensitivity,
        min_len_ms=args.min_len,
        max_len_ms=args.max_len,
        silence_db=args.silence_db,
    )

    print(f"processing {len(inputs)} file(s) -> {args.staging}...")
    rows = slice_folder(inputs, args.staging, sorter,
                        slice_kwargs=slice_kwargs, batch_size=runtime.batch_size)
    print(f"done. {len(rows)} slices staged in {args.staging}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
