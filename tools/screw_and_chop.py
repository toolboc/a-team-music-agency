#!/usr/bin/env python3
"""
screw_and_chop.py — DJ Screw style audio processor

Three effects, applied in order:
  1. CHOP  — beat-detect, slice into 4-beat bars, repeat every Nth bar N times
  2. SCREW — time-stretch slow + pitch-shift down (mimics slowing a cassette)
  3. SYRUP — repeating echo delay with exponential decay

All deps are already in requirements.txt (librosa, soundfile, numpy, scipy).

Usage examples:
  # Full C&S with defaults (73% speed, -4 semitones, chop every 2nd bar x2)
  python tools/screw_and_chop.py --audio songs/<slug>/iterations/NN/audio.wav \
                                  --out   songs/<slug>/iterations/NN/audio_screwed.wav

  # Screw only, no chop, aggressive pitch drop
  python tools/screw_and_chop.py --audio input.wav --out output.wav \
                                  --speed 0.70 --semitones -6 --no-chop --no-echo

  # Chop only (useful for testing the beat grid)
  python tools/screw_and_chop.py --audio input.wav --out output.wav \
                                  --no-screw --no-echo --chop-every 1 --chop-repeats 3
"""

import argparse
import os
import sys

import numpy as np
import librosa
import soundfile as sf


# ---------------------------------------------------------------------------
# Effects
# ---------------------------------------------------------------------------

def apply_chop(y: np.ndarray, sr: int, chop_every: int, chop_repeats: int) -> np.ndarray:
    """
    Slice audio into 4-beat bars and stutter-repeat every Nth bar.
    3ms micro-crossfades at all cut points to prevent clicks without blurring.
    Chop happens at original tempo before screwing so beat detection is accurate.
    """
    print(f"[chop] detecting beats...", flush=True)
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")
    beat_samples = librosa.frames_to_samples(beat_frames).tolist()
    bpm_val = float(np.atleast_1d(tempo)[0])

    if len(beat_samples) < 8:
        print(f"[chop] only {len(beat_samples)} beats found — skipping chop", flush=True)
        return y

    print(f"[chop] {len(beat_samples)} beats at ~{bpm_val:.1f} BPM", flush=True)

    FADE = int(sr * 0.003)  # 3ms crossfade — kills clicks, won't blur transients
    fade_out = np.linspace(1.0, 0.0, FADE).astype(np.float32)
    fade_in  = np.linspace(0.0, 1.0, FADE).astype(np.float32)

    BAR = 4  # beats per bar
    chunks = []

    # Audio before the first beat (intro pickup) — no fade needed
    if beat_samples[0] > 0:
        chunks.append(y[: beat_samples[0]])

    bar_num = 0
    i = 0
    while i < len(beat_samples):
        start = beat_samples[i]
        next_i = i + BAR
        end = beat_samples[next_i] if next_i < len(beat_samples) else len(y)
        chunk = y[start:end].copy()

        if len(chunk) < FADE * 2:
            i += BAR
            bar_num += 1
            continue

        # Apply micro-crossfade envelope to this chunk
        chunk[:FADE]  *= fade_in
        chunk[-FADE:] *= fade_out

        count = chop_repeats if (bar_num % chop_every == 0) else 1
        for _ in range(count):
            chunks.append(chunk)

        i += BAR
        bar_num += 1

    result = np.concatenate(chunks)
    print(f"[chop] {len(y)/sr:.1f}s → {len(result)/sr:.1f}s "
          f"({bar_num} bars, every {chop_every}th repeated x{chop_repeats})", flush=True)
    return result


def apply_screw(y: np.ndarray, sr: int, speed: float, semitones: float) -> np.ndarray:
    """
    Authentic Screw effect using HPSS — harmonic and percussive tracks are
    time-stretched with different window sizes so drums stay sharp:
      - Harmonic  (sax, bass, chords): large n_fft=2048 — smooth tonal quality
      - Percussive (kick, snare, hats): small n_fft=256  — transient-preserving
    Both are recombined before a single pitch-shift pass.
    """
    print(f"[screw] separating harmonic / percussive (HPSS)...", flush=True)
    D = librosa.stft(y)
    H, P = librosa.decompose.hpss(D, margin=4.0)
    y_h = librosa.istft(H, length=len(y))
    y_p = librosa.istft(P, length=len(y))

    print(f"[screw] stretching harmonic to {speed*100:.0f}% (n_fft=2048)...", flush=True)
    y_h_slow = librosa.effects.time_stretch(y_h, rate=speed, n_fft=2048)

    print(f"[screw] stretching percussive to {speed*100:.0f}% (n_fft=256)...", flush=True)
    y_p_slow = librosa.effects.time_stretch(y_p, rate=speed, n_fft=256)

    # Align and recombine
    n = min(len(y_h_slow), len(y_p_slow))
    y_mixed = y_h_slow[:n] + y_p_slow[:n]

    print(f"[screw] pitch-shifting {semitones:+.1f} semitones...", flush=True)
    y_screwed = librosa.effects.pitch_shift(y_mixed, sr=sr, n_steps=semitones)

    print(f"[screw] {len(y)/sr:.1f}s → {len(y_screwed)/sr:.1f}s", flush=True)
    return y_screwed


def apply_syrup(y: np.ndarray, sr: int,
                delay_ms: float, decay: float, repeats: int) -> np.ndarray:
    """
    Syrup drip: additive echo with exponential decay — that wet, woozy Screw sound.
    """
    print(f"[syrup] echo delay={delay_ms}ms decay={decay} x{repeats}...", flush=True)
    delay_samples = int(sr * delay_ms / 1000)
    out = y.astype(np.float64).copy()

    for i in range(1, repeats + 1):
        offset = delay_samples * i
        amp = decay ** i
        if offset < len(out):
            out[offset:] += amp * y[: len(out) - offset]

    # Soft-clip / normalize
    peak = np.max(np.abs(out))
    if peak > 0.98:
        out = out * (0.98 / peak)

    return out.astype(np.float32)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="DJ Screw chopped & screwed audio processor",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--audio",    required=True,  help="Input audio file (.wav / .mp3)")
    parser.add_argument("--out",      required=True,  help="Output .wav path")

    # Screw
    parser.add_argument("--speed",    type=float, default=0.73,
                        help="Tape speed ratio (0.73 = 73%% = classic Screw)")
    parser.add_argument("--semitones",type=float, default=-4.0,
                        help="Pitch shift in semitones (negative = lower)")
    parser.add_argument("--no-screw", action="store_true", help="Skip slow+pitch effect")

    # Chop
    parser.add_argument("--chop-every",   type=int, default=2,
                        help="Repeat every Nth 4-beat bar")
    parser.add_argument("--chop-repeats", type=int, default=2,
                        help="How many times to play the chopped bar")
    parser.add_argument("--no-chop",  action="store_true", help="Skip chop effect")

    # Syrup echo
    parser.add_argument("--echo-delay", type=float, default=220.0,
                        help="Echo delay in milliseconds")
    parser.add_argument("--echo-decay", type=float, default=0.30,
                        help="Echo amplitude decay factor per repeat (0–1)")
    parser.add_argument("--echo-repeats", type=int, default=5,
                        help="Number of echo repeats")
    parser.add_argument("--no-echo",  action="store_true", help="Skip syrup echo")

    args = parser.parse_args()

    # Validate
    if not os.path.exists(args.audio):
        print(f"[error] input not found: {args.audio}", file=sys.stderr)
        sys.exit(1)
    if not (0 < args.speed <= 2.0):
        print("[error] --speed must be between 0 (exclusive) and 2.0", file=sys.stderr)
        sys.exit(1)

    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # Load (mono, native sample rate)
    print(f"[screw_and_chop] loading {args.audio}...", flush=True)
    y, sr = librosa.load(args.audio, sr=None, mono=True)
    print(f"[screw_and_chop] {len(y)/sr:.1f}s  sr={sr}Hz", flush=True)

    # 1. Chop first — beat detection is most accurate at original tempo
    if not args.no_chop:
        y = apply_chop(y, sr, args.chop_every, args.chop_repeats)

    # 2. Screw
    if not args.no_screw:
        y = apply_screw(y, sr, args.speed, args.semitones)

    # 3. Syrup echo
    if not args.no_echo:
        y = apply_syrup(y, sr, args.echo_delay, args.echo_decay, args.echo_repeats)

    # Write
    print(f"[screw_and_chop] writing {args.out}...", flush=True)
    sf.write(args.out, y, sr, subtype="PCM_16")
    size_mb = os.path.getsize(args.out) / 1024 / 1024
    duration = len(y) / sr
    print(f"[screw_and_chop] done ✓  {duration:.1f}s  {size_mb:.1f} MB → {args.out}",
          flush=True)


if __name__ == "__main__":
    main()
