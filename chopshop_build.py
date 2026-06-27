#!/usr/bin/env python3
"""chopshop_build - PUBLISH: turn approved sounds into an Ableton-ready library.

Reads the staging set (after the kids reviewed it in chopshop_web.py) and, for
every slice marked "keep":

  1. Writes a clean WAV into ``<library>/<Category>/`` with embedded metadata
     (BWF ``bext`` + RIFF ``INFO`` tags) so the label/keywords travel with the
     file -- an open standard Ableton Live, other DAWs, and Explorer all read.
  2. Builds one **Drum Rack (.adg)** per category into ``<library>/_racks/`` so
     each category is instantly playable in Live 11. Drag the rack onto a track
     and every sound in that category is mapped across the pads.
  3. Writes a master manifest (CSV + JSON).

    python chopshop_build.py --staging ./_staging --library ./KidsSounds

Add ``<library>`` to Ableton Live's Browser "Places" once; everything below it
becomes searchable next to the factory sounds.

Drum Rack generation prefers the MIT-licensed ``ableton-device-creator`` package
(template-driven, Live 11+ correct). If it isn't installed, we fall back to a
self-contained gzipped-XML writer so the pipeline still produces racks -- those
should be spot-checked in Live before bulk use.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import struct
import sys
import zlib
from pathlib import Path
from typing import Optional

import soundfile as sf

from chopshop_core import (
    KID_LABELS,
    Slice,
    load_mono,
    read_staging,
    slugify,
)

# Map slug -> friendly folder name (e.g. "water" -> "Water").
_FRIENDLY = {slug: name for slug, _p, _e, name in KID_LABELS}


def friendly(slug: str) -> str:
    return _FRIENDLY.get(slug, slug.replace("-", " ").title() or "Unsorted")


# --------------------------------------------------------------------------- #
# step 1: publish tagged WAVs
# --------------------------------------------------------------------------- #
def publish_wavs(slices: list[Slice], staging_dir: Path, library: Path) -> list[Slice]:
    """Copy kept slices into category folders with embedded metadata.

    Returns the published slices with ``out_path`` set (relative to library).
    """
    published: list[Slice] = []
    for s in slices:
        if s.status != "keep":
            continue
        src = staging_dir / s.staging_path
        if not src.exists():
            print(f"  ! missing staged wav, skipping: {s.staging_path}",
                  file=sys.stderr)
            continue

        cat_dir = library / friendly(s.category)
        cat_dir.mkdir(parents=True, exist_ok=True)

        stem = slugify(s.custom_name) if s.custom_name else slugify(s.category)
        base = f"{slugify(Path(s.source).stem)}_{s.index:03d}_{stem}"
        dest = _unique(cat_dir / f"{base}.wav")

        y, sr = load_mono(src)
        # write the audio, then graft metadata chunks onto the RIFF container
        sf.write(str(dest), y, sr, subtype="PCM_24")
        _embed_metadata(
            dest,
            title=s.custom_name or friendly(s.category),
            keywords=[s.category, "chopshop", "sound-safari"],
            comment=f"Sound Safari capture from {s.source}",
        )

        s.out_path = str(dest.relative_to(library))
        published.append(s)
    return published


def _unique(path: Path) -> Path:
    """Return ``path`` or path with a numeric suffix if it already exists."""
    if not path.exists():
        return path
    i = 2
    while True:
        cand = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not cand.exists():
            return cand
        i += 1


# --- BWF/INFO metadata embedding (no external deps) ------------------------ #
def _embed_metadata(wav: Path, *, title: str, keywords: list[str],
                    comment: str) -> None:
    """Append RIFF ``LIST/INFO`` + ``bext`` chunks to a WAV in place.

    Uses only stdlib. INFO tags (INAM/IKEY/ICMT) are the widely-read open
    standard; ``bext`` is the broadcast-WAV description block. Both are optional
    chunks, so players that don't read them just ignore the extra bytes.
    """
    try:
        raw = bytearray(wav.read_bytes())
        if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
            return  # not a RIFF wav; leave it alone

        chunks = _info_chunk(title, keywords, comment) + _bext_chunk(comment)
        raw.extend(chunks)
        # fix the RIFF size field (total file size - 8)
        struct.pack_into("<I", raw, 4, len(raw) - 8)
        wav.write_bytes(raw)
    except Exception as exc:  # metadata is best-effort, never fatal
        print(f"  (metadata skipped for {wav.name}: {exc})", file=sys.stderr)


def _riff_subchunk(cid: bytes, data: bytes) -> bytes:
    out = cid + struct.pack("<I", len(data)) + data
    if len(data) % 2:        # RIFF chunks are word-aligned
        out += b"\x00"
    return out


def _cstr(s: str) -> bytes:
    b = s.encode("utf-8", "replace") + b"\x00"
    if len(b) % 2:
        b += b"\x00"
    return b


def _info_chunk(title: str, keywords: list[str], comment: str) -> bytes:
    body = b"INFO"
    body += _riff_subchunk(b"INAM", _cstr(title))
    body += _riff_subchunk(b"IKEY", _cstr(", ".join(keywords)))
    body += _riff_subchunk(b"ICMT", _cstr(comment))
    body += _riff_subchunk(b"ISFT", _cstr("chopshop Sound Safari"))
    return _riff_subchunk(b"LIST", body)


def _bext_chunk(description: str) -> bytes:
    # Minimal BWF bext: 256-char description + zeroed required fields (602 bytes).
    desc = description.encode("ascii", "replace")[:256].ljust(256, b"\x00")
    bext = desc + b"\x00" * (602 - 256)
    return _riff_subchunk(b"bext", bext)


# --------------------------------------------------------------------------- #
# step 2: Drum Racks (.adg), one per category
# --------------------------------------------------------------------------- #
def build_racks(published: list[Slice], library: Path,
                template: Optional[Path]) -> list[Path]:
    """Build one .adg Drum Rack per category from the published WAVs."""
    racks_dir = library / "_racks"
    racks_dir.mkdir(parents=True, exist_ok=True)

    by_cat: dict[str, list[Path]] = {}
    for s in published:
        by_cat.setdefault(s.category, []).append(library / s.out_path)

    made: list[Path] = []
    for cat, wavs in by_cat.items():
        out = racks_dir / f"{friendly(cat)}.adg"
        wavs = sorted(wavs)[:128]            # a Drum Rack holds 128 pads
        if _build_rack_via_library(wavs, out, template) or \
                _build_rack_fallback(wavs, out, library):
            made.append(out)
            print(f"  rack: {out.name} ({len(wavs)} pads)")
    return made


def _build_rack_via_library(wavs: list[Path], out: Path,
                            template: Optional[Path]) -> bool:
    """Try the ableton-device-creator package. Returns True on success."""
    try:
        from ableton_device_creator.drum_racks import DrumRackCreator  # type: ignore
    except Exception:
        return False
    try:
        # Build from a temp folder of just these wavs would copy files; instead
        # most versions accept an explicit sample list. We try the documented
        # folder API first using the shared category folder.
        creator = DrumRackCreator(template=str(template)) if template \
            else DrumRackCreator()
        creator.from_folder(samples_dir=str(wavs[0].parent), output=str(out))
        return out.exists()
    except Exception as exc:
        print(f"  (library rack build failed, using fallback: {exc})",
              file=sys.stderr)
        return False


def _build_rack_fallback(wavs: list[Path], out: Path, library: Path) -> bool:
    """Self-contained .adg writer (gzipped Live XML). Spot-check in Live 11.

    Generates a Drum Rack whose pads each hold an Original Simpler pointing at
    one WAV via a **library-relative** path, so the rack still resolves after the
    whole library is synced/copied to another machine or a different drive letter
    (e.g. ``C:\\KidsSounds`` here, ``G:\\My Drive\\SoundSafari`` on a kid's
    laptop). Live walks up from the rack to the library root, then back down to
    the sample -- no absolute path baked in.
    """
    try:
        xml = _drum_rack_xml(wavs, library)
        with gzip.open(out, "wb") as fh:
            fh.write(xml.encode("utf-8"))
        return True
    except Exception as exc:
        print(f"  ! could not write {out.name}: {exc}", file=sys.stderr)
        return False


def _drum_rack_xml(wavs: list[Path], library: Path) -> str:
    """Construct minimal Live 11 Drum Rack XML. Pads start at MIDI note 36 (C1)."""
    branches = []
    for i, wav in enumerate(wavs):
        note = 36 + i                     # standard drum-rack base = C1
        branches.append(_branch_xml(wav, note, i, library))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Ableton MajorVersion="5" MinorVersion="11.0_11300" '
        'SchemaChangeCount="3" Creator="chopshop" Revision="">\n'
        "  <GroupDevicePreset>\n"
        '    <OverwriteProtectionNumber Value="2900" />\n'
        "    <Device><DrumGroupDevice>\n"
        '      <On><Manual Value="true" /></On>\n'
        "    </DrumGroupDevice></Device>\n"
        "    <BranchPresets>\n" + "".join(branches) + "    </BranchPresets>\n"
        "  </GroupDevicePreset>\n"
        "</Ableton>\n"
    )


def _branch_xml(wav: Path, note: int, idx: int, library: Path) -> str:
    size = wav.stat().st_size if wav.exists() else 0
    crc = _wav_crc(wav)
    name = wav.name
    esc = lambda s: (s.replace("&", "&amp;").replace("<", "&lt;")
                     .replace(">", "&gt;").replace('"', "&quot;"))

    # Path relative to the rack's own folder (<library>/_racks). The sample sits
    # at <library>/<Category>/<name>, so this is "../<Category>". Encoded as Live
    # RelativePathElement dirs; the leading ".." is RelativePathType 3 (relative
    # to the file's containing folder). Absolute Path is kept as a last-resort
    # fallback Live uses only if the relative walk fails.
    rack_dir = library / "_racks"
    rel = wav.relative_to(library)                 # e.g. Water/foo.wav
    rel_dirs = [".."] + list(rel.parent.parts)     # up out of _racks, into cat
    elems = "".join(f'<RelativePathElement Dir="{esc(d)}" />' for d in rel_dirs)
    abspath = str(wav.resolve())

    return (
        "      <DrumBranchPreset>\n"
        f'        <Id Value="{idx}" />\n'
        "        <DevicePresets><AbletonDevicePreset><Device><OriginalSimpler>\n"
        "          <Player><MultiSampleMap><SampleParts>\n"
        '            <MultiSamplePart><Name Value="' + esc(name) + '" />\n'
        "              <SampleRef><FileRef>\n"
        '                <HasRelativePath Value="true" />\n'
        '                <RelativePathType Value="3" />\n'
        f'                <RelativePath>{elems}</RelativePath>\n'
        f'                <Path Value="{esc(abspath)}" />\n'
        f'                <Name Value="{esc(name)}" />\n'
        "                <SearchHint>\n"
        f'                  <FileSize Value="{size}" />\n'
        f'                  <Crc Value="{crc}" />\n'
        "                </SearchHint>\n"
        "              </FileRef></SampleRef>\n"
        "            </MultiSamplePart>\n"
        "          </SampleParts></MultiSampleMap></Player>\n"
        "        </OriginalSimpler></Device></AbletonDevicePreset></DevicePresets>\n"
        "        <BranchInfo>\n"
        f'          <ReceivingNote Value="{127 - note}" />\n'
        "        </BranchInfo>\n"
        "      </DrumBranchPreset>\n"
    )


def _wav_crc(wav: Path) -> int:
    try:
        return zlib.crc32(wav.read_bytes()) & 0xFFFFFFFF
    except OSError:
        return 0


# --------------------------------------------------------------------------- #
# step 3: manifest
# --------------------------------------------------------------------------- #
def write_manifest(published: list[Slice], library: Path) -> None:
    if not published:
        return
    rows = [{
        "source": s.source, "category": s.category, "name": s.custom_name,
        "confidence": s.confidence, "start_sec": s.start_sec,
        "end_sec": s.end_sec, "out_path": s.out_path,
    } for s in published]
    (library / "manifest.json").write_text(json.dumps(rows, indent=2))
    with (library / "manifest.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #
def build(staging_dir: Path, library: Path, template: Optional[Path]) -> int:
    slices = read_staging(staging_dir)
    if not slices:
        print(f"No staging set in {staging_dir}. Run slice + review first.")
        return 1
    kept = [s for s in slices if s.status == "keep"]
    if not kept:
        print("Nothing marked 'keep' yet. Have the kids review in the web app.")
        return 1

    library.mkdir(parents=True, exist_ok=True)
    print(f"Publishing {len(kept)} sounds to {library} ...")
    published = publish_wavs(slices, staging_dir, library)
    print(f"  wrote {len(published)} tagged WAVs")
    racks = build_racks(published, library, template)
    write_manifest(published, library)
    print(f"Done. {len(racks)} Drum Racks in {library / '_racks'}")
    print(f"Tip: add {library} to Ableton Live's Browser 'Places'.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="chopshop_build",
        description="Publish reviewed sounds into an Ableton-ready library.",
    )
    p.add_argument("--staging", type=Path, default=Path("./_staging"),
                   help="staging directory (default ./_staging)")
    p.add_argument("--library", type=Path, default=Path("./KidsSounds"),
                   help="output library root (default ./KidsSounds)")
    p.add_argument("--rack-template", type=Path, default=None,
                   help="optional .adg template for ableton-device-creator")
    args = p.parse_args(argv)
    return build(args.staging, args.library, args.rack_template)


if __name__ == "__main__":
    raise SystemExit(main())
