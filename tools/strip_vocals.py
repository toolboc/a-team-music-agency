"""Strip vocals from an audio file using Demucs (htdemucs model).

Uses the Demucs Python API directly (bypasses torchaudio CLI which requires
torchcodec on newer builds). Audio I/O done with librosa + soundfile.

Produces a no-vocals WAV by summing all non-vocal stems:
  drums + bass + other  (everything except "vocals")

Usage:
    python tools/strip_vocals.py --audio INPUT --out OUTPUT [--model htdemucs]

Requirements:
    pip install demucs   (model weights ~80MB auto-downloaded on first run)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Vocal stem removal via Demucs Python API")
    parser.add_argument("--audio", required=True, help="Input audio file")
    parser.add_argument("--out", required=True, help="Output WAV path (no-vocals mix)")
    parser.add_argument("--vocals-out", default=None, help="Optional output WAV path for the isolated vocal stem")
    parser.add_argument("--model", default="htdemucs", help="Demucs model name")
    args = parser.parse_args()

    audio_path = Path(args.audio)
    out_path = Path(args.out)

    if not audio_path.exists():
        print(f"[strip_vocals] ERROR: input not found: {audio_path}", file=sys.stderr)
        sys.exit(1)

    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[strip_vocals] loading {audio_path} ...")
    import librosa
    import numpy as np
    import soundfile as sf
    import torch
    from demucs.pretrained import get_model
    from demucs.apply import apply_model

    y, sr = librosa.load(str(audio_path), sr=None, mono=False)
    if y.ndim == 1:
        y = y[None, :]

    print(f"[strip_vocals] loading model '{args.model}' ...")
    model = get_model(args.model)
    model.eval()

    model_sr = model.samplerate
    if sr != model_sr:
        print(f"[strip_vocals] resampling {sr} -> {model_sr} Hz ...")
        y = librosa.resample(y, orig_sr=sr, target_sr=model_sr)
        sr = model_sr

    if y.shape[0] == 1:
        y = np.concatenate([y, y], axis=0)

    wav = torch.tensor(y, dtype=torch.float32).unsqueeze(0)

    print("[strip_vocals] separating stems ...")
    with torch.no_grad():
        sources = apply_model(model, wav, progress=True)

    sources = sources[0]
    stem_names = model.sources
    print(f"[strip_vocals] stems: {stem_names}")

    vocal_idx = stem_names.index("vocals")
    no_vocals = sum(sources[i] for i in range(len(stem_names)) if i != vocal_idx)

    out_np = no_vocals.numpy().T
    sf.write(str(out_path), out_np, sr, subtype="PCM_16")
    size_mb = out_path.stat().st_size / 1_048_576
    print(f"[strip_vocals] done -> {out_path} ({size_mb:.1f} MB)")

    if args.vocals_out:
        vocals_path = Path(args.vocals_out)
        vocals_path.parent.mkdir(parents=True, exist_ok=True)
        vocals_np = sources[vocal_idx].numpy().T
        sf.write(str(vocals_path), vocals_np, sr, subtype="PCM_16")
        size_mb_v = vocals_path.stat().st_size / 1_048_576
        print(f"[strip_vocals] vocals -> {vocals_path} ({size_mb_v:.1f} MB)")


if __name__ == "__main__":
    main()
