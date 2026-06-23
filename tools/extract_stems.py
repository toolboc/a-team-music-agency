"""Extract all stems from an audio file using Demucs.

Default model: htdemucs_6s (6 stems: drums, bass, guitar, piano, other, vocals)
Also works with htdemucs (4 stems: drums, bass, other, vocals)

Writes one WAV per stem: <out-dir>/<stem-name>.wav

Usage:
    python tools/extract_stems.py --audio INPUT --out-dir OUTPUT_DIR [--model htdemucs_6s]

Requirements:
    pip install demucs
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-stem extraction via Demucs")
    parser.add_argument("--audio", required=True, help="Input audio file")
    parser.add_argument("--out-dir", required=True, help="Directory to write one WAV per stem")
    parser.add_argument(
        "--model",
        default="htdemucs_6s",
        help="Demucs model (default: htdemucs_6s = drums/bass/guitar/piano/other/vocals)",
    )
    args = parser.parse_args()

    audio_path = Path(args.audio)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not audio_path.exists():
        print(f"[extract_stems] ERROR: {audio_path} not found", file=sys.stderr)
        sys.exit(1)

    print(f"[extract_stems] loading {audio_path} ...")
    import librosa
    import numpy as np
    import soundfile as sf
    import torch
    from demucs.pretrained import get_model
    from demucs.apply import apply_model

    y, sr = librosa.load(str(audio_path), sr=None, mono=False)
    if y.ndim == 1:
        y = y[None, :]

    print(f"[extract_stems] loading model '{args.model}' ...")
    model = get_model(args.model)
    model.eval()

    model_sr = model.samplerate
    if sr != model_sr:
        print(f"[extract_stems] resampling {sr} -> {model_sr} Hz ...")
        y = librosa.resample(y, orig_sr=sr, target_sr=model_sr)
        sr = model_sr

    if y.shape[0] == 1:
        y = np.concatenate([y, y], axis=0)

    wav = torch.tensor(y, dtype=torch.float32).unsqueeze(0)

    print("[extract_stems] separating stems (this takes a while on CPU) ...")
    with torch.no_grad():
        sources = apply_model(model, wav, progress=True)

    sources = sources[0]  # (stems, channels, samples)
    stem_names = model.sources
    print(f"[extract_stems] stems: {stem_names}")

    for i, name in enumerate(stem_names):
        out_path = out_dir / f"{name}.wav"
        stem_np = sources[i].numpy().T  # (samples, channels)
        sf.write(str(out_path), stem_np, sr, subtype="PCM_16")
        size_mb = out_path.stat().st_size / 1_048_576
        print(f"[extract_stems] {name:10s} -> {out_path} ({size_mb:.1f} MB)")

    print(f"[extract_stems] done. {len(stem_names)} stems written to {out_dir}")


if __name__ == "__main__":
    main()
