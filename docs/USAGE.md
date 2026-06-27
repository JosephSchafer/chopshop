# Usage & CLI reference

Complete reference for every command-line entry point and flag. For the
big-picture flow see [ARCHITECTURE.md](ARCHITECTURE.md); for the kids' side see
[KIDS_GUIDE.md](KIDS_GUIDE.md).

There are five entry points. In normal use you only touch two:
`chopshop_doctor.py` once, then `chopshop.py` for every session. The other three
are run directly only when you want a single stage.

---

## `chopshop_doctor.py` — setup & install

Detect hardware, recommend a setup, write `chopshop.json`, optionally install.

```bash
python chopshop_doctor.py                  # detect + recommend + write config
python chopshop_doctor.py --install        # also run the recommended pip installs
python chopshop_doctor.py --fetch-model    # pre-download the CLAP checkpoint (~2 GB)
python chopshop_doctor.py --json           # machine-readable JSON, no prose
```

| Flag | Default | Meaning |
|---|---|---|
| `--sounds PATH` | `""` | Sounds root; fills `paths` in the config (expects `raw/` inside). |
| `--config PATH` | `chopshop.json` | Where to write the config. |
| `--backend {auto,clap,yamnet}` | `auto` | Force a backend instead of auto-detecting. Forcing `yamnet` also pins device to CPU. |
| `--install` | off | Run the recommended `pip install` commands. |
| `--fetch-model` | off | Pre-download the CLAP checkpoint after install. |
| `--json` | off | Print the config as JSON only (for scripts). |

**Typical first run:**
```bash
python chopshop_doctor.py            # look at the recommendation
python chopshop_doctor.py --install  # install what it suggested
python chopshop_doctor.py --fetch-model
```

---

## `chopshop.py` — the launcher (main entry point)

Runs SLICE → REVIEW → PUBLISH in order. The grown-up's one command.

```bash
# full run with defaults (inbox + library in Google Drive)
python chopshop.py

# explicit paths + the doctor's config
python chopshop.py --inbox ./inbox --library ./KidsSounds --config chopshop.json
```

### Path options
| Flag | Default | Meaning |
|---|---|---|
| `--inbox PATH` | `G:\My Drive\SoundSafari\inbox` | Folder of raw recordings to slice. |
| `--staging PATH` | `<repo>/_staging` (local, off Drive) | Working area for candidate slices. |
| `--library PATH` | `G:\My Drive\SoundSafari\library` | Final Ableton-ready library. |
| `--config STR` | `""` | Path to `chopshop.json` (device/batch). Optional. |

### Stage control
| Flag | Effect |
|---|---|
| `--skip-slice` | Reuse the existing staging set; go straight to review + publish. |
| `--review-only` | Only open the web review app. |
| `--build-only` | Only publish what's already reviewed. |
| `--dry-run` | Slice without loading the model (no AI categories). |

### Slicing options
| Flag | Default | Meaning |
|---|---|---|
| `-s, --sensitivity FLOAT` | `0.5` | `0.0` sparse … `1.0` catches everything. |
| `--min-len FLOAT` | `40.0` | Minimum slice length (ms). |
| `--max-len FLOAT` | `4000.0` | Maximum slice length (ms). |
| `--silence-db FLOAT` | `-45.0` | Tail-trim floor in dBFS. |

### Classification options
| Flag | Default | Meaning |
|---|---|---|
| `--labels STR` | `""` | Comma-separated CLAP label phrases (overrides the kid default set). |
| `--labels-file PATH` | `""` | File with one label phrase per line. |
| `--ckpt PATH` | `""` | Local CLAP `.pt` checkpoint (skip the download). |

### Review-app options
| Flag | Default | Meaning |
|---|---|---|
| `--port INT` | `8000` | Port for the local web app. |
| `--no-open` | off | Don't auto-open the browser. |

### Publish options
| Flag | Default | Meaning |
|---|---|---|
| `--rack-template PATH` | `None` | Optional `.adg` template for `ableton-device-creator`. |

### Common recipes
```bash
python chopshop.py --review-only                 # reopen the kids' app
python chopshop.py --build-only                  # just publish reviewed sounds
python chopshop.py --skip-slice                  # keep slices, review + publish
python chopshop.py --dry-run                     # slice with no AI labels
python chopshop.py -s 0.7 --min-len 60           # catch more, drop tiny clicks
python chopshop.py --library "D:\Sounds"         # point the library anywhere
python chopshop.py --port 9000 --no-open         # custom port, no auto browser
```

---

## `chopshop_core.py` — slice stage on its own

Useful for slicing/classifying without the launcher (e.g. headless batches).

```bash
python chopshop_core.py ./inbox --staging ./_staging --config chopshop.json
python chopshop_core.py recording.wav --dry-run     # one file, no model
```

| Flag | Default | Meaning |
|---|---|---|
| `input` (positional) | — | Audio file or folder of recordings. |
| `--staging PATH` | `./_staging` | Staging directory to write into. |
| `--config PATH` | `None` | `chopshop.json` for device/batch. |
| `--dry-run` | off | Slice only; skip classification (no model load). |
| `-s, --sensitivity` | `0.5` | Onset sensitivity. |
| `--min-len` / `--max-len` | `40` / `4000` | Slice length bounds (ms). |
| `--silence-db` | `-45.0` | Tail-trim floor (dBFS). |
| `--labels` / `--labels-file` | `""` | Custom label vocabulary. |
| `--ckpt` | `""` | Local CLAP checkpoint. |

---

## `chopshop_web.py` — review app on its own

Serve the staging set for review without slicing or publishing.

```bash
python chopshop_web.py --staging ./_staging
# then open http://localhost:8000
```

| Flag | Default | Meaning |
|---|---|---|
| `--staging PATH` | `./_staging` | Staging directory to review. |
| `--port INT` | `8000` | Port. |
| `--no-open` | off | Don't auto-open the browser. |

Press **Ctrl+C** in the terminal to stop. All decisions are saved continuously
to `staging.json`, so stopping never loses progress.

### HTTP API (for the curious / for testing)
The page talks to a small JSON API on the same server:

| Method & route | Body | Purpose |
|---|---|---|
| `GET /` | — | The single-page app (HTML/CSS/JS). |
| `GET /api/state` | — | Full summary: totals + every slice. |
| `GET /api/audio?index=N` | — | The WAV bytes for slice N. |
| `POST /api/keep` | `{index}` | Mark slice kept. |
| `POST /api/trash` | `{index}` | Mark slice trashed. |
| `POST /api/category` | `{index, slug}` | Set category (confidence → 1.0). |
| `POST /api/name` | `{index, name}` | Set a custom name. |
| `POST /api/merge` | `{index}` | Join slice with the next (same source). |
| `POST /api/trim` | `{index, start, end}` | Keep only `[start,end]` (fractions 0–1). |

---

## `chopshop_build.py` — publish stage on its own

Publish whatever is already reviewed in a staging set.

```bash
python chopshop_build.py --staging ./_staging --library ./KidsSounds
```

| Flag | Default | Meaning |
|---|---|---|
| `--staging PATH` | `./_staging` | Reviewed staging directory. |
| `--library PATH` | `./KidsSounds` | Output library root. |
| `--rack-template PATH` | `None` | Optional `.adg` template for `ableton-device-creator`. |

Exit codes: `0` success; `1` if there's no staging set or nothing marked `keep`.

---

## End-to-end example (real session)

```bash
# 1. one-time setup
python chopshop_doctor.py --install
python chopshop_doctor.py --fetch-model

# 2. kids drop recordings into G:\My Drive\SoundSafari\inbox

# 3. run the pipeline
python chopshop.py --config chopshop.json
#    → slices the inbox, opens http://localhost:8000
#    → kids review every sound in the browser
#    → grown-up presses Ctrl+C → sounds published + racks built

# 4. in Ableton Live 11: Browser → Add Folder → pick the library folder
```

See [ABLETON.md](ABLETON.md) for the two Google-Drive must-dos before Ableton
can read the library.
