# Development & contributing

How to set up a dev environment, run the tests, extend the tool, and contribute.
For the system design read [ARCHITECTURE.md](ARCHITECTURE.md) first.

---

## Setup

```bash
git clone https://github.com/JosephSchafer/chopshop.git
cd chopshop
python -m venv .venv && . .venv/Scripts/activate    # Windows
# python -m venv .venv && source .venv/bin/activate # macOS/Linux

# slicing deps (enough for --dry-run and the web app and builder)
pip install numpy soundfile librosa

# classification deps (only needed to actually label sounds)
pip install laion-clap torch

# rack builder (optional; there's a fallback)
pip install ableton-device-creator
```

Or just let the doctor do it:
```bash
python chopshop_doctor.py --install
```

Requires **Python 3.9+** (the code uses `X | None` annotations under
`from __future__ import annotations`).

---

## Running without real hardware/audio

You can exercise most of the pipeline with no GPU, no model, and synthetic audio:

```bash
# slice synthetic/real audio with no model at all
python chopshop_core.py some_recording.wav --dry-run --staging ./_staging

# review it
python chopshop_web.py --staging ./_staging

# publish it (uses the fallback rack writer if ableton-device-creator is absent)
python chopshop_build.py --staging ./_staging --library ./out
```

### Smoke test

The pipeline has been validated end-to-end with a synthetic recording (four
tone bursts separated by silence):

1. `slice_folder` produces multiple staged slices + `staging.json`.
2. Simulated review marks them `keep` with categories and a custom name.
3. `build` writes tagged WAVs (INFO + bext confirmed present), per-category
   `.adg` racks (valid gzipped Live XML with **relative** sample paths), and the
   manifest.
4. The web server serves `/`, `/api/state`, `/api/audio`, and accepts the POST
   mutation routes.

When changing the slicer, builder, or web API, re-run an equivalent end-to-end
check. There's no formal test suite yet - see *Ideas* below.

---

## Project layout

```
chopshop.py          launcher / orchestrator
chopshop_doctor.py   hardware detection + setup (standalone, stdlib-only)
chopshop_core.py     slicing + classification engine + shared library
chopshop_web.py      stdlib HTTP review app (imports core)
chopshop_build.py    publish: tagged WAVs + .adg racks (imports core)
docs/                this documentation set
```

---

## Common extensions

### Add or change categories
Edit `KID_LABELS` in `chopshop_core.py`. Each entry is
`(slug, clap_phrase, emoji, friendly_name)`:

```python
("rain", "the sound of rain falling", "🌧️", "Rain"),
```

- `slug` - folder/url-safe id used in `staging.json` and filenames.
- `clap_phrase` - what CLAP actually matches against. **Short descriptive
  phrases beat bare nouns** ("the sound of rain falling", not "rain").
- `emoji` / `friendly_name` - shown in the web app and used for folder names.

Everything downstream (web grid, publish folders, `slug_for_phrase`) reads from
this one list - no other file needs touching.

### Use a custom vocabulary at runtime
Without editing code:
```bash
python chopshop.py --labels "the sound of rain, a dog barking, a car horn"
python chopshop.py --labels-file my_labels.txt   # one phrase per line
```
Note: runtime labels feed CLAP, but the web app's emoji grid is driven by
`KID_LABELS`. For a fully custom emoji grid, edit `KID_LABELS`.

### Tune the slicer
The knobs live in `detect_bounds` (`chopshop_core.py`) and are exposed as CLI
flags: `--sensitivity`, `--min-len`, `--max-len`, `--silence-db`. Onset
sensitivity maps inversely to librosa's `delta`; the tail trim cuts after 3
consecutive sub-floor RMS frames.

### Add a backend (e.g. wire up YAMNet)
The doctor can already *recommend* `yamnet`, but `chopshop_core` only implements
CLAP. To add it: create a sorter class with the same interface as `ClapSorter`
(`classify` / `classify_batch`), and branch on `runtime.backend` in the slice
entry points (`chopshop_core.main` and `chopshop.run_slice`).

### Change the rack layout
`build_racks` groups by category and maps samples sequentially from MIDI 36. To
change pad mapping or rack grouping, edit `build_racks` / `_branch_xml`. See
[ABLETON.md](ABLETON.md) for the XML schema and the relative-path scheme.

---

## Code conventions

- **Match the surrounding style.** The codebase favors small, single-purpose
  functions, dataclasses for structured data, and thorough docstrings.
- **Keep the doctor dependency-free.** `chopshop_doctor.py` must run on a bare
  interpreter; don't import third-party packages at module top level there.
- **Lazy-import heavy deps.** `torch` / `laion_clap` are imported inside
  `ClapSorter`, not at module load, so `--dry-run` and the web app work without
  them.
- **Stages talk through files.** Don't introduce shared in-memory state between
  stages; the on-disk contracts (`staging.json`, `chopshop.json`) are the API.
- **Metadata and racks are best-effort.** Never let a metadata or rack failure
  abort a publish run that has valid audio.

---

## Contributing

1. Fork and branch from `main`.
2. Make focused changes; keep the docstring/comment density of the existing code.
3. Run an end-to-end smoke check (see above) for any change to slicing, the web
   API, or rack/metadata output.
4. Open a PR describing what changed and how you verified it.

This is MIT-licensed (see [LICENSE](../LICENSE)).

---

## Ideas / roadmap

- **Validate `.adg` in real Live 11** and, if needed, ship a verified template.
- **A formal test suite** (pytest) around `detect_bounds`, the metadata writer,
  and the `.adg` XML, using synthetic fixtures.
- **True split** (one slice → two keepers) in the web app, alongside trim/merge.
- **YAMNet backend** for low-RAM machines (the doctor already recommends it).
- **Batch-uniform classification** - group slices by length before batching for
  tighter CLAP batches.
- **Live Clips (`.alc`)** as an optional output alongside Drum Racks.
