#!/usr/bin/env python3
"""
render_batch.py — Generic batch renderer for ACE-STEP 1.5.

Renders all prompt JSON files in a directory sequentially, skipping
tracks that already have an output file.

Usage:
    python tools/render_batch.py --prompts <prompts_dir> --out <tracks_dir>

    # Windows PowerShell:
    $env:ACE_STEP_URL = "http://your-host:7860/"
    python tools/render_batch.py --prompts "MyAlbum/prompts" --out "MyAlbum/tracks"

    # POSIX:
    ACE_STEP_URL=http://your-host:7860/ python tools/render_batch.py \
        --prompts MyAlbum/prompts --out MyAlbum/tracks

Arguments:
    --prompts   Directory containing prompt JSON files (required)
    --out       Directory to write rendered WAV files (default: <prompts>/../tracks)
    --ext       Output file extension, default: wav
    --url       ACE-STEP URL (overrides ACE_STEP_URL env var)
    --no-skip   Re-render tracks that already exist

Each prompt JSON file must contain at minimum the fields expected by
ace_step_client.py (tags, lyrics, tempo_bpm, key, duration, …).
The output file name matches the prompt file stem, e.g.
  prompts/01-my-song.json  →  tracks/01-my-song.wav
"""

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ace_step_client import render


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch-render ACE-STEP prompts")
    parser.add_argument("--prompts", required=True, help="Directory of prompt JSON files")
    parser.add_argument("--out", help="Output directory for rendered audio (default: <prompts>/../tracks)")
    parser.add_argument("--ext", default="wav", help="Output file extension (default: wav)")
    parser.add_argument("--url", help="ACE-STEP URL (overrides ACE_STEP_URL env var)")
    parser.add_argument("--no-skip", action="store_true", help="Re-render even if output exists")
    args = parser.parse_args()

    prompts_dir = Path(args.prompts)
    if not prompts_dir.is_dir():
        print(f"ERROR: prompts directory not found: {prompts_dir}", file=sys.stderr)
        return 1

    out_dir = Path(args.out) if args.out else prompts_dir.parent / "tracks"
    out_dir.mkdir(parents=True, exist_ok=True)

    url = args.url or os.environ.get("ACE_STEP_URL")
    if not url:
        print("ERROR: ACE_STEP_URL environment variable not set (or pass --url)", file=sys.stderr)
        return 1

    prompts = sorted(prompts_dir.glob("*.json"))
    if not prompts:
        print(f"No JSON prompt files found in {prompts_dir}", file=sys.stderr)
        return 1

    ext = args.ext.lstrip(".")
    print(f"[render_batch] {len(prompts)} prompts | output: {out_dir} | server: {url}")
    print()

    total = len(prompts)
    failed = []

    for i, prompt_path in enumerate(prompts, start=1):
        slug = prompt_path.stem
        out_path = out_dir / f"{slug}.{ext}"

        if out_path.exists() and not args.no_skip:
            print(f"[{i:02d}/{total}] SKIP (exists): {slug}")
            continue

        print(f"[{i:02d}/{total}] Rendering: {slug}")
        t0 = time.time()
        try:
            result = render(prompt_path, out_path, url)
            elapsed = time.time() - t0
            if result.get("ok"):
                size_mb = result.get("audio_bytes", 0) / 1_048_576
                print(f"  OK  {elapsed:.0f}s  {size_mb:.1f} MB  → {out_path}")
            else:
                print(f"  ERROR: {result}", file=sys.stderr)
                failed.append(slug)
        except Exception as e:
            elapsed = time.time() - t0
            print(f"  EXCEPTION after {elapsed:.0f}s: {type(e).__name__}: {e}", file=sys.stderr)
            failed.append(slug)
            time.sleep(5)

        print()

    print(f"[render_batch] Done. {total - len(failed)}/{total} succeeded.")
    if failed:
        print(f"Failed ({len(failed)}):")
        for f in failed:
            print(f"  - {f}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
