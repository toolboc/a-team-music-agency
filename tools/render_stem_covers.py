"""Stem-by-stem cover transformation orchestrator.

Pipeline:
  1. Read stem_transform.json config
  2. For each stem: call ACE-STEP in cover mode with source_audio=<stem.wav>
     and track_name=<target instrument> for output isolation
  3. Sum all transformed stem WAVs (with per-stem gain) into a final mix
  4. Write iterations/<NN>/audio.wav + a summary prompt.json

Usage:
    python tools/render_stem_covers.py \\
        --config songs/<slug>/stem_transform.json \\
        --iteration 18

Requires:
    ACE_STEP_URL env var set
    python tools/extract_stems.py already run (stems_dir populated)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf


def load_audio(path: Path, target_sr: int | None = None) -> tuple[np.ndarray, int]:
    """Load a WAV as float32 (samples, channels)."""
    import librosa
    y, sr = librosa.load(str(path), sr=target_sr, mono=False)
    if y.ndim == 1:
        y = y[None, :]  # (1, samples)
    return y.T, sr  # (samples, channels)


def call_ace_step(prompt_dict: dict, out_path: Path, ace_step_url: str) -> bool:
    """Write a temp prompt.json and invoke ace_step_client. Returns True on success."""
    import tempfile
    import subprocess

    tmp = Path(tempfile.mktemp(suffix=".json"))
    tmp.write_text(json.dumps(prompt_dict, indent=2))
    out_path.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["ACE_STEP_URL"] = ace_step_url

    result = subprocess.run(
        [sys.executable, "tools/ace_step_client.py", "--prompt", str(tmp), "--out", str(out_path)],
        env=env,
    )
    tmp.unlink(missing_ok=True)
    return result.returncode == 0


def mix_stems(stem_paths: list[tuple[Path, float]], out_path: Path) -> None:
    """Load stem WAVs, apply gain, sum them, write to out_path. Clips to [-1, 1]."""
    import librosa

    mixed: np.ndarray | None = None
    ref_sr: int | None = None

    for path, gain in stem_paths:
        if not path.exists():
            print(f"[mix] WARNING: {path} missing, skipping")
            continue
        y, sr = load_audio(path)
        if ref_sr is None:
            ref_sr = sr
        elif sr != ref_sr:
            # Resample to reference sr
            import librosa as _lr
            y_t = _lr.resample(y.T.astype(np.float32), orig_sr=sr, target_sr=ref_sr).T
            y = y_t
        if mixed is None:
            mixed = np.zeros_like(y, dtype=np.float32)
        # Pad/trim to same length
        L = max(mixed.shape[0], y.shape[0])
        if mixed.shape[0] < L:
            mixed = np.pad(mixed, ((0, L - mixed.shape[0]), (0, 0)))
        if y.shape[0] < L:
            y = np.pad(y, ((0, L - y.shape[0]), (0, 0)))
        mixed = mixed[:L] + (y[:L] * gain).astype(np.float32)

    if mixed is None:
        print("[mix] ERROR: no stems to mix", file=sys.stderr)
        sys.exit(1)

    # Normalize to prevent clipping (peak normalize if over 0 dBFS)
    peak = np.abs(mixed).max()
    if peak > 0.98:
        mixed = mixed * (0.95 / peak)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_path), mixed, ref_sr, subtype="PCM_16")
    size_mb = out_path.stat().st_size / 1_048_576
    print(f"[mix] final mix -> {out_path} ({size_mb:.1f} MB, {mixed.shape[0]/ref_sr:.1f}s)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Stem-by-stem ACE-STEP cover orchestrator")
    parser.add_argument("--config", required=True, help="Path to stem_transform.json")
    parser.add_argument("--iteration", required=True, help="Iteration number, e.g. 18")
    parser.add_argument("--url", default=os.environ.get("ACE_STEP_URL"), help="ACE-STEP URL")
    parser.add_argument("--skip-existing", action="store_true", help="Skip stems already rendered")
    args = parser.parse_args()

    if not args.url:
        print("[render_stem_covers] ERROR: ACE_STEP_URL not set", file=sys.stderr)
        sys.exit(1)

    config_path = Path(args.config)
    config = json.loads(config_path.read_text())

    slug = config["song_slug"]
    stems_dir = Path(config["stems_dir"])
    iter_num = args.iteration.zfill(2)
    iter_dir = Path(f"songs/{slug}/iterations/{iter_num}")
    stems_out_dir = iter_dir / "stems"
    stems_out_dir.mkdir(parents=True, exist_ok=True)

    base = config["base_params"]

    print(f"\n[render_stem_covers] === {slug} — stem iteration {iter_num} ===")
    print(f"[render_stem_covers] stems source: {stems_dir}")
    print(f"[render_stem_covers] output dir:   {iter_dir}\n")

    rendered: list[tuple[Path, float]] = []
    t_total_start = time.time()

    for stem_cfg in config["stems"]:
        stem_name = stem_cfg["stem"]
        source_stem = stems_dir / f"{stem_name}.wav"

        if stem_cfg.get("skip_if_missing") and not source_stem.exists():
            print(f"[render_stem_covers] {stem_name:8s}  SKIPPED (file missing, skip_if_missing=true)")
            continue

        if not source_stem.exists():
            print(f"[render_stem_covers] ERROR: {source_stem} not found", file=sys.stderr)
            sys.exit(1)

        track_name = stem_cfg["track_name"]
        out_wav = stems_out_dir / f"{stem_name}_{track_name}.wav"

        if args.skip_existing and out_wav.exists():
            print(f"[render_stem_covers] {stem_name:8s}  already rendered -> {out_wav.name}")
            rendered.append((out_wav, stem_cfg.get("gain", 1.0)))
            continue

        # Build prompt dict for this stem
        prompt = {
            "_stem": stem_name,
            "_target": track_name,
            **base,
            "tags": stem_cfg["tags"],
            "source_audio": str(source_stem),
            "track_name": track_name,
            "lm_codes_strength": stem_cfg.get("lm_codes_strength", 0.60),
            "cover_strength": stem_cfg.get("cover_strength", 0.35),
        }
        if "reference_audio" in stem_cfg:
            ref_path = Path(stem_cfg["reference_audio"])
            if ref_path.exists():
                prompt["reference_audio"] = str(ref_path)
            else:
                print(f"[render_stem_covers] WARNING: reference_audio not found: {ref_path}, skipping")
        if "negative_prompt" in stem_cfg:
            prompt["negative_prompt"] = stem_cfg["negative_prompt"]
        if "lm_temperature" in stem_cfg:
            prompt["lm_temperature"] = stem_cfg["lm_temperature"]

        print(f"[render_stem_covers] {stem_name:8s}  -> {track_name:12s}  rendering ...")
        t0 = time.time()
        ok = call_ace_step(prompt, out_wav, args.url)
        elapsed = time.time() - t0

        if ok and out_wav.exists():
            size_mb = out_wav.stat().st_size / 1_048_576
            print(f"[render_stem_covers] {stem_name:8s}  DONE  {elapsed:.0f}s  {size_mb:.1f} MB -> {out_wav.name}")
            rendered.append((out_wav, stem_cfg.get("gain", 1.0)))
        else:
            print(f"[render_stem_covers] {stem_name:8s}  FAILED (exit non-zero)", file=sys.stderr)

    if not rendered:
        print("[render_stem_covers] No stems rendered successfully. Aborting mix.", file=sys.stderr)
        sys.exit(1)

    print(f"\n[render_stem_covers] mixing {len(rendered)} stems ...")
    final_wav = iter_dir / "audio.wav"
    mix_stems(rendered, final_wav)

    # Write a summary prompt.json
    summary = {
        "_type": "stem_cover",
        "_config": str(config_path),
        "_iteration": iter_num,
        "_stems_rendered": [str(p) for p, _ in rendered],
        "song_slug": slug,
        "base_params": base,
        "elapsed_total_s": round(time.time() - t_total_start),
    }
    (iter_dir / "prompt.json").write_text(json.dumps(summary, indent=2))

    total_min = (time.time() - t_total_start) / 60
    print(f"\n[render_stem_covers] COMPLETE in {total_min:.1f} min")
    print(f"[render_stem_covers] Final mix: {final_wav}")


if __name__ == "__main__":
    main()
