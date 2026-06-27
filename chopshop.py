#!/usr/bin/env python3
"""chopshop - one-command launcher for the kids' Sound Safari pipeline.

This is the grown-up's entry point. It runs the three stages in order:

  1. SLICE   : carve every recording in the inbox into candidate one-shots and
               let CLAP propose a real-world category for each (chopshop_core).
  2. REVIEW  : open the kid-friendly web app so the kids listen and keep/sort
               each sound (chopshop_web). The launcher waits here until you stop
               the server (Ctrl+C) once the kids are done.
  3. PUBLISH : write the kept sounds into the Ableton-ready library with embedded
               metadata and build one Drum Rack (.adg) per category (chopshop_build).

    # full run: slice the inbox, review, then publish
    python chopshop.py --inbox ./inbox --library ./KidsSounds --config chopshop.json

    # skip stages you've already done
    python chopshop.py --library ./KidsSounds --skip-slice   # just review + publish
    python chopshop.py --library ./KidsSounds --review-only   # just the web app
    python chopshop.py --library ./KidsSounds --build-only    # just publish

The kids only ever touch the browser. Everything else is one command for you.
Run chopshop_doctor.py first to detect hardware and install dependencies.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import chopshop_core as core
import chopshop_web as web
import chopshop_build as build


def run_slice(args) -> int:
    """Stage 1: slice the inbox into the staging area (optionally classify)."""
    inbox = Path(args.inbox)
    if not inbox.exists():
        print(f"inbox not found: {inbox}", file=sys.stderr)
        return 1
    inputs = core.gather_inputs(inbox)
    if not inputs:
        print(f"no audio files in {inbox}", file=sys.stderr)
        return 1

    runtime = core.load_runtime(Path(args.config) if args.config else None)

    sorter = None
    if not args.dry_run:
        labels = core.load_labels(args.labels, args.labels_file)
        print(f"loading CLAP ({len(labels)} labels) on {runtime.device}...")
        try:
            sorter = core.ClapSorter(labels, runtime, ckpt=args.ckpt or None)
        except ImportError:
            print("CLAP not installed -- slicing without labels. "
                  "Run chopshop_doctor.py --install for auto-categories.",
                  file=sys.stderr)

    slice_kwargs = dict(
        sensitivity=args.sensitivity,
        min_len_ms=args.min_len,
        max_len_ms=args.max_len,
        silence_db=args.silence_db,
    )
    print(f"slicing {len(inputs)} recording(s) -> {args.staging}")
    rows = core.slice_folder(inputs, Path(args.staging), sorter,
                             slice_kwargs=slice_kwargs,
                             batch_size=runtime.batch_size)
    print(f"  {len(rows)} sounds staged. Next: the kids review them.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="chopshop",
        description="Slice -> kid review -> Ableton library, in one command.",
    )
    # Defaults: inbox + library live in the Google Drive SoundSafari folder so
    # they back up and share automatically; _staging stays local (off Drive) so
    # mid-review file churn never conflicts with the sync client.
    p.add_argument("--inbox", type=Path, default=core.drive_inbox(),
                   help=f"folder of raw recordings (default {core.drive_inbox()})")
    p.add_argument("--staging", type=Path, default=core.local_staging(),
                   help="local working area for candidate slices (off Drive)")
    p.add_argument("--library", type=Path, default=core.drive_library(),
                   help=f"final Ableton-ready library (default {core.drive_library()})")
    p.add_argument("--config", type=str, default="",
                   help="chopshop.json from the doctor (device/batch)")

    stage = p.add_argument_group("stage control")
    stage.add_argument("--skip-slice", action="store_true",
                       help="reuse existing staging; go straight to review")
    stage.add_argument("--review-only", action="store_true",
                       help="only open the web review app")
    stage.add_argument("--build-only", action="store_true",
                       help="only publish what's already reviewed")
    stage.add_argument("--dry-run", action="store_true",
                       help="slice without loading the model (no categories)")

    sl = p.add_argument_group("slicing")
    sl.add_argument("-s", "--sensitivity", type=float, default=0.5)
    sl.add_argument("--min-len", type=float, default=40.0)
    sl.add_argument("--max-len", type=float, default=4000.0)
    sl.add_argument("--silence-db", type=float, default=-45.0)

    cl = p.add_argument_group("classification")
    cl.add_argument("--labels", type=str, default="")
    cl.add_argument("--labels-file", type=str, default="")
    cl.add_argument("--ckpt", type=str, default="")

    web_g = p.add_argument_group("review app")
    web_g.add_argument("--port", type=int, default=8000)
    web_g.add_argument("--no-open", action="store_true")

    bl = p.add_argument_group("publish")
    bl.add_argument("--rack-template", type=Path, default=None,
                    help="optional .adg template for ableton-device-creator")
    args = p.parse_args(argv)

    # --- single-stage shortcuts ---
    if args.build_only:
        return build.build(args.staging, args.library, args.rack_template)
    if args.review_only:
        web.serve(args.staging, port=args.port, open_browser=not args.no_open)
        return 0

    # --- full pipeline ---
    if not args.skip_slice:
        rc = run_slice(args)
        if rc != 0:
            return rc

    print("\nOpening the Sound Safari review app for the kids...")
    print("When they're done, come back here and press Ctrl+C to publish.\n")
    web.serve(args.staging, port=args.port, open_browser=not args.no_open)

    print("\nPublishing the reviewed sounds to the library...")
    return build.build(args.staging, args.library, args.rack_template)


if __name__ == "__main__":
    raise SystemExit(main())
