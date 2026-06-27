# 🎧 Sound Safari (chopshop)

Turn a messy pile of field recordings — made by kids exploring the world with a
mic — into a clean, categorized, **Ableton Live 11**-ready sound library.

```
recordings  ──►  auto-slice + AI guess  ──►  kids review in browser  ──►  Ableton library
 (inbox)              (chopshop_core)         (chopshop_web)            (library + Drum Racks)
```

## What's where

| File | What it does | Who runs it |
|---|---|---|
| `chopshop_doctor.py` | Detect this PC's hardware, install dependencies, write `chopshop.json` | Grown-up, once |
| `chopshop.py` | **The one command.** Slice → open kids' review app → publish | Grown-up |
| `chopshop_core.py` | Slicing + AI categorizing engine | (imported) |
| `chopshop_web.py` | The kids' "Sound Safari" review website | (kids use the browser) |
| `chopshop_build.py` | Publishes tagged WAVs + builds Drum Racks (`.adg`) | (imported) |

## First-time setup (grown-up, ~10 min)

```bash
python chopshop_doctor.py            # see what your machine can do
python chopshop_doctor.py --install  # install numpy/librosa/CLAP/torch/etc.
python chopshop_doctor.py --fetch-model   # pre-download the AI model (~2 GB)
```

## Everyday use

1. Kids drop their recordings into the **inbox** (see folders below).
2. Grown-up runs:
   ```bash
   python chopshop.py
   ```
   It slices everything, then opens **http://localhost:8000**.
3. **Kids take over the browser:** for each sound they
   - press **▶ Play** (or the spacebar),
   - tap the right **emoji category** (💧 water, 🪵 wood, 🔔 metal…),
   - optionally type a fun name,
   - hit **✅ Keep it!** or **🗑️ Toss it** (Enter = keep).
   - Bad cut? **drag on the waveform** to select the good part → **✂️ Trim**, or
     **🔗 Join with next** to glue two pieces together.
4. When they're done, the grown-up returns to the terminal and presses **Ctrl+C**.
   chopshop publishes the kept sounds and builds the Drum Racks.

## Folders (Google Drive vs. local)

These are the defaults (override with `--inbox`, `--library`, `--staging`):

```
G:\My Drive\SoundSafari\          ← synced + backed up by Google Drive
  inbox\                          ← kids drop raw recordings here
  library\                        ← the finished, shareable sound library
    Water\  Metal\  Wood\ ...     ← tagged WAVs, browsable in Ableton
    _racks\  Water.adg ...        ← drag onto a track = instantly playable
    manifest.csv / manifest.json

c:\Users\...\.chopshop\           ← the code (this folder, in Git)
  _staging\                       ← throwaway working slices (NOT synced)
```

**Why this split:** the library and inbox live in Google Drive so they back up
and share across the family automatically. The `_staging` working area stays
**local** so Drive's sync client never fights with files changing mid-review.
The **code** is tracked in **Git** (here), not Drive — code wants version
history, and Drive can corrupt a file saved mid-edit.

### ⚠️ Two Drive must-dos for Ableton

1. **Make the library "Available offline."** Right-click
   `G:\My Drive\SoundSafari\library` in Explorer → *Offline access* →
   *Available offline*. Ableton reads samples directly; online-only placeholder
   files will show as **"media offline."**
2. **Add the library to Ableton's Browser.** In Live 11: Browser → **Add Folder…**
   → pick the `library` folder. It now sits in **Places** next to factory sounds,
   fully searchable. The Drum Racks in `_racks\` use **library-relative paths**,
   so they keep working even on a different computer with the synced library.

## Backing up & sharing

- **Sounds:** automatic via Google Drive (the `library` folder). To share with
  another family member, share that Drive folder; they add it to their own Live.
- **Code:** committed to Git in this folder. To back up off-machine, add a
  remote (e.g. GitHub) and `git push`.

## Handy variations

```bash
python chopshop.py --review-only        # just reopen the kids' app
python chopshop.py --build-only         # just publish what's already reviewed
python chopshop.py --skip-slice         # reuse existing slices, review + publish
python chopshop.py --dry-run            # slice without the AI (no categories)
python chopshop.py --library "D:\Sounds"  # point anywhere
```

## License & credits

MIT licensed — see [LICENSE](LICENSE). Built as a family project to make a sound
library out of kids' field recordings. Drum Rack generation can use the
MIT-licensed [`ableton-device-creator`](https://github.com/ben-juodvalkis/Ableton-Device-Creator),
with a built-in fallback writer. Classification uses
[LAION-CLAP](https://github.com/LAION-AI/CLAP).
