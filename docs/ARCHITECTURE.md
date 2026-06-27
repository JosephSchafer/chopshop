# Architecture

This document explains how chopshop is put together: the stages, the modules,
the data that flows between them, and the on-disk contracts that hold it all
together. If you want to *use* the tool, read [USAGE.md](USAGE.md) instead; this
is for understanding or modifying it.

## The big picture

chopshop is a four-stage pipeline. Each stage is a separate module with one job,
and stages hand off through files on disk — never through shared process state.
That makes every stage independently runnable, testable, and resumable.

```
   ┌─────────────────────────────────────────────────────────────────────┐
   │                          chopshop.py (launcher)                       │
   │            orchestrates the stages; the grown-up's one command        │
   └─────────────────────────────────────────────────────────────────────┘
        │                 │                  │                    │
        ▼                 ▼                  ▼                    ▼
   ┌─────────┐      ┌──────────┐      ┌───────────┐       ┌────────────┐
   │  SETUP  │      │  SLICE   │      │  REVIEW   │       │  PUBLISH   │
   │ doctor  │      │  core    │      │   web     │       │   build    │
   └─────────┘      └──────────┘      └───────────┘       └────────────┘
        │                 │                  │                    │
        ▼                 ▼                  ▼                    ▼
   chopshop.json    _staging/*.wav    staging.json edits   library/<Cat>/*.wav
   (host+runtime)   + staging.json    (keep/category/...)  + _racks/*.adg
                                                            + manifest.csv/json
```

## Stages and modules

### 0. SETUP — `chopshop_doctor.py`
Runs on a bare interpreter before anything is installed. It:
- **Detects** the machine: CPU, RAM (psutil → `/proc/meminfo` → Windows ctypes),
  NVIDIA GPUs (`nvidia-smi`), Apple Silicon, and whether torch/CLAP are present.
- **Recommends** a backend (`clap`/`yamnet`), device (`cuda`/`mps`/`cpu`), and
  batch size based on VRAM/RAM.
- **Writes** `chopshop.json` (the runtime config the rest of the pipeline reads).
- Optionally **installs** the pip dependencies and **pre-downloads** the CLAP
  checkpoint.

Detection (`profile_host`) and recommendation (`recommend`) are kept strictly
separate: one observes, the other decides. Neither performs installs — that only
happens behind `--install` / `--fetch-model`.

Key types: `Host`, `Gpu`, `TorchInfo`, `Recommendation`, `Config`.

### 1. SLICE — `chopshop_core.py`
The audio engine. It does two jobs and nothing else (no UI, no installs):
- **Slice** (`detect_bounds` + `carve`): librosa onset detection plus a
  trailing-silence trim, carving a long recording into individual one-shots.
- **Classify** (`ClapSorter`): zero-shot labelling with LAION-CLAP against a
  *real-world* vocabulary (`KID_LABELS`) — water, metal, wood, voice, … No
  training; you supply the label phrases and CLAP picks the best match per slice.

It writes each candidate as a native-rate WAV into the **staging** directory and
records a `Slice` row in `staging.json`. Nothing is published here.

`chopshop_core` is also the **shared library** the other modules import
(`Slice`, `read_staging`/`write_staging`, `KID_LABELS`, path helpers, etc.).

### 2. REVIEW — `chopshop_web.py`
A local web app (Python standard library only — no Flask, no build step) that an
11-year-old drives in a browser. It reads `staging.json`, serves one sound at a
time with a waveform and big buttons, and writes the kid's decisions straight
back to `staging.json`:
- ✅ keep / 🗑️ trash
- 🏷️ confirm or change the category (emoji grid)
- ✏️ optional fun name
- ✂️ trim (drag a region on the waveform) / 🔗 join-with-next (fix bad cuts)

All mutations go through the `Library` class behind a lock, so the on-disk
manifest stays consistent even though the browser fires several requests at once.

### 3. PUBLISH — `chopshop_build.py`
Reads the reviewed `staging.json` and, for every slice marked `keep`:
1. Writes a clean WAV into `library/<Category>/` with **embedded metadata**
   (BWF `bext` + RIFF `INFO` tags) — see [ABLETON.md](ABLETON.md).
2. Builds one **Drum Rack (`.adg`)** per category into `library/_racks/`, using
   library-relative sample paths so the racks stay portable.
3. Writes a master `manifest.csv` / `manifest.json`.

Rack generation prefers the MIT-licensed `ableton-device-creator` package and
falls back to a self-contained gzipped-XML writer if it isn't installed.

### Launcher — `chopshop.py`
A thin orchestrator. The full run slices the inbox, opens the review app, waits
for the grown-up to stop the server (Ctrl+C) once the kids are done, then
publishes. Stage shortcuts (`--review-only`, `--build-only`, `--skip-slice`) let
you re-enter at any point because every stage's state lives on disk.

## Data flow in detail

```
inbox/*.wav,*.m4a,...
   │  gather_inputs()              collect supported audio files
   ▼
detect_bounds() ──► carve()       per-recording: find sounds, cut + fade
   │
   ▼
ClapSorter.classify_batch()       batched CLAP labels (skipped on --dry-run)
   │
   ▼
_staging/<source>_<NNN>.wav       one WAV per candidate slice
_staging/staging.json             list[Slice] manifest
   │
   │   (kids review in the browser — edits status/category/name in place,
   │    and trim/merge rewrite the staging WAVs)
   ▼
publish_wavs()                    kept slices → library/<Category>/*.wav (+meta)
build_racks()                     per-category .adg drum racks
write_manifest()                  library/manifest.{csv,json}
```

## On-disk contracts

The pipeline is glued together by three file formats. Treat their shapes as
APIs.

### `chopshop.json` — runtime config (doctor → everyone)
Written by `chopshop_doctor.py`; the `runtime` block is read by
`chopshop_core.load_runtime()`. Versioned by `CONFIG_VERSION`.

```jsonc
{
  "version": 1,
  "host": { /* full Host snapshot: cpu, ram_gb, gpus, torch, ... */ },
  "runtime": {
    "backend": "clap",            // "clap" | "yamnet"
    "device":  "cuda",            // "cuda" | "mps" | "cpu"
    "batch_size": 64,
    "model": "HTSAT-base (LAION-CLAP)"
  },
  "paths":  { /* optional sounds_root/raw/categorized/... */ },
  "labels_file": null
}
```

Only the `runtime` block is consumed downstream; the rest is informational. A
missing or unreadable file is fine — `load_runtime()` returns safe defaults
(`cpu`, batch 8).

### `staging.json` — the review manifest (core ↔ web → build)
Written by `chopshop_core.write_staging()`, edited by the web app, read by the
builder. Versioned by `STAGING_VERSION`.

```jsonc
{
  "version": 1,
  "slices": [
    {
      "source": "kid_recording.wav",   // original file
      "index": 0,                        // nth slice within that source
      "start_sec": 0.41, "end_sec": 0.73,
      "sr": 44100,                       // native sample rate of the staged wav
      "staging_path": "kid_recording_000.wav",   // relative to _staging/
      "category": "water",               // slug from KID_LABELS (or custom)
      "confidence": 0.82,                // CLAP softmax prob; 1.0 if kid-chosen
      "status": "pending",               // "pending" | "keep" | "trash"
      "custom_name": "",                 // optional kid-given name
      "out_path": ""                     // set at publish time
    }
  ]
}
```

`Slice.from_dict()` ignores unknown keys, so the schema can gain fields without
breaking older manifests.

### `.adg` — Ableton Drum Rack (build → Ableton)
Gzipped Live XML, one per category. See [ABLETON.md](ABLETON.md) for the full
structure, the relative-path scheme that keeps racks portable, and the
template-vs-fallback generation paths.

## Design principles

- **Zero hard dependencies in the doctor.** It must run before anything is
  installed; every probe degrades gracefully.
- **Detection vs. decision vs. side effects are separate.** Observing the host,
  choosing a setup, and running installs are three different responsibilities in
  three different functions.
- **Stages communicate through files, not memory.** Any stage can be re-run on
  its own; a crash mid-pipeline loses nothing already written.
- **Nothing is published until a human approves it.** Slicing writes only to the
  throwaway staging area; the library is built solely from `keep`-marked slices.
- **Portability over convenience in output.** Racks use relative paths and WAVs
  carry embedded metadata so the library survives being copied or synced
  elsewhere.

## Dependency map

```
chopshop.py
 ├── chopshop_core   (numpy, soundfile, librosa; torch + laion_clap when classifying)
 ├── chopshop_web    (stdlib http.server only; imports core)
 └── chopshop_build  (soundfile; optional ableton-device-creator; imports core)

chopshop_doctor.py   (stdlib only; optional psutil/torch/laion_clap probing)
```

`chopshop_doctor.py` is intentionally standalone — it shares no imports with the
rest so it can run on a clean machine.
