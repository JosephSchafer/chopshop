# Ableton Live 11 integration & file formats

How chopshop's output plugs into Ableton Live 11, what's inside the files it
generates, and the open standards it leans on so the library isn't locked to any
one tool.

---

## What you get

After a publish run, the library looks like this:

```
library/
  Water/   splash_000_my-first-splash.wav   ...   ← tagged one-shot WAVs
  Metal/   clang_001_metal.wav              ...
  Wood/    ...
  _racks/  Water.adg  Metal.adg  Wood.adg   ...   ← playable Drum Racks
  manifest.csv
  manifest.json
```

Two things make this Ableton-friendly:
1. A **clean folder-per-category tree** of WAVs you can browse in Live's Browser.
2. **Drum Racks (`.adg`)** — drop one on a track and the whole category is mapped
   across the pads, instantly playable.

---

## Adding the library to Live

Do this once:

1. In Live 11, open the **Browser** (left panel).
2. Click **Add Folder…** (under *Places*).
3. Choose your `library` folder.

It now appears in **Places**, fully searchable next to Ableton's factory content.
Drag a WAV onto a track for a single sound, or drag a `.adg` from `_racks/` for a
whole playable kit.

---

## Two must-dos if the library lives in Google Drive

Ableton reads sample files directly off disk, which trips over two Drive
behaviours:

### 1. Make the library "Available offline"
Google Drive's "online-only" files are zero-byte placeholders until opened. Live
will scan them and report **"media offline."**

> Right-click `…\SoundSafari\library` in Explorer → **Offline access** →
> **Available offline**. Drive then keeps real bytes on disk and Ableton is happy.

### 2. Keep the working area off Drive
`_staging/` deliberately defaults to a **local** path, not Drive. Slicing and
trimming rewrite those WAVs constantly; letting Drive sync them mid-review causes
conflicts and churn. Only the finished `library/` (and the `inbox/`) belong in
Drive.

---

## File format: the WAVs (open standards)

Every published WAV is plain PCM (24-bit) plus two optional metadata chunks that
travel *inside* the file:

- **RIFF `INFO` (`LIST/INFO`)** — the widely-read tag block. chopshop writes:
  - `INAM` — title (the custom name, or the category)
  - `IKEY` — keywords (category + `chopshop` + `sound-safari`)
  - `ICMT` — comment ("Sound Safari capture from <source>")
  - `ISFT` — software ("chopshop Sound Safari")
- **BWF `bext`** — the Broadcast-WAV description block (256-char description).

These are **open standards** read by Live, other DAWs, and many file managers, so
the label/keywords aren't trapped in chopshop. They're *optional* chunks: any
player that doesn't understand them simply ignores the extra bytes. The metadata
is also best-effort — if writing fails for any reason, the audio is still valid.

Implementation: `_embed_metadata`, `_info_chunk`, `_bext_chunk` in
`chopshop_build.py`. It appends the chunks and fixes the RIFF size field; no
external library required.

---

## File format: the Drum Racks (`.adg`)

An `.adg` ("Ableton Device Group") is **gzip-compressed XML**. chopshop builds one
per category. You can inspect one yourself:

```bash
python -c "import gzip,sys; sys.stdout.write(gzip.open('Water.adg').read().decode())"
```

### Structure (simplified)

```xml
<Ableton MajorVersion="5" MinorVersion="11.0_11300" Creator="chopshop">
  <GroupDevicePreset>
    <Device><DrumGroupDevice> … </DrumGroupDevice></Device>
    <BranchPresets>
      <DrumBranchPreset>            <!-- one per sample / pad -->
        <DevicePresets><AbletonDevicePreset><Device><OriginalSimpler>
          <Player><MultiSampleMap><SampleParts>
            <MultiSamplePart>
              <SampleRef><FileRef>
                <HasRelativePath Value="true" />
                <RelativePathType Value="3" />
                <RelativePath>
                  <RelativePathElement Dir=".." />
                  <RelativePathElement Dir="Water" />
                </RelativePath>
                <Path Value="C:\…\library\Water\splash.wav" />
                <Name Value="splash.wav" />
                <SearchHint><FileSize Value="…" /><Crc Value="…" /></SearchHint>
              </FileRef></SampleRef>
            </MultiSamplePart>
          </SampleParts></MultiSampleMap></Player>
        </OriginalSimpler></Device></AbletonDevicePreset></DevicePresets>
        <BranchInfo><ReceivingNote Value="…" /></BranchInfo>
      </DrumBranchPreset>
    </BranchPresets>
  </GroupDevicePreset>
</Ableton>
```

Pads start at MIDI note 36 (C1), the drum-rack convention, and a rack holds up to
128 pads.

### Why relative paths matter (portability)

The sample reference uses **`RelativePathType="3"`** (relative to the file's own
folder) with `RelativePathElement` dirs that walk *up* out of `_racks/` and back
*down* into the category folder: `../Water/splash.wav`.

That's the difference between a rack that works only on the machine that built it
and one that works **anywhere the library is**:

| Machine | Library at | Relative path still resolves? |
|---|---|---|
| Build PC | `C:\…\library` | ✅ `../Water/splash.wav` |
| Kid's laptop | `G:\My Drive\SoundSafari\library` | ✅ `../Water/splash.wav` |

The absolute `<Path>` is kept too, but only as a last resort Live falls back to
if the relative walk fails. The `SearchHint` (file size + CRC) is what Live uses
to relocate a sample that moved.

Implementation: `build_racks`, `_drum_rack_xml`, `_branch_xml` in
`chopshop_build.py`.

---

## Two ways racks get built

1. **Preferred — `ableton-device-creator`** (MIT, pip-installable). A
   template-driven builder: it decompresses a known-good `.adg` template, fills in
   samples, and recompresses. Because the template comes from a real Live export,
   the schema is guaranteed correct for that Live version. Pass your own template
   with `--rack-template`.
2. **Fallback — built-in writer.** If that package isn't installed, chopshop
   writes the gzipped XML itself (shown above). It produces structurally valid,
   relative-path racks, but should be **spot-checked in your Live 11** before bulk
   use — schema details vary slightly between Live builds.

> ⚠️ **Verification status:** the fallback writer's output passes structural
> tests but has not yet been confirmed to load cleanly in a live Ableton 11
> install. Test one rack (drag it onto a track, check for "media offline") before
> trusting a big batch. If it misbehaves, exporting a one-sample Drum Rack from
> your own Live and passing it via `--rack-template` is the reliable fix.

---

## Beyond Ableton

Because the WAVs carry standard INFO/BWF metadata and live in a plain folder
tree, the library is usable far beyond Live:
- Any DAW (Logic, Reaper, FL, Bitwig) can import the folders.
- Sample managers (ADSR, XLN XO, etc.) read the embedded tags.
- File explorers show the title/comment on Windows and macOS.

The `.adg` racks are the only Ableton-specific artifact; everything else is open.
