#!/usr/bin/env python3
"""
mix_tracks.py — Continuous DJ mix assembler for the Poe Experiment Ibiza Edition.

Usage:
    python tools/mix_tracks.py --playlist <path/to/playlist.json> --out <output_dir>

The playlist JSON is a list of objects:
    [
        {"title": "...", "audio": "path/to/audio.wav", "bpm": 129.2, "key": "D minor"},
        ...
    ]

Outputs:
    <output_dir>/processed/<N>-<slug>.wav  — HPF'd + normalised individual tracks
    <output_dir>/continuous_mix.wav       — full continuous mix with crossfades
"""

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt


# ── Constants ───────────────────────────────────────────────────────────────

HPF_CUTOFF_HZ = 40.0          # High-pass filter cutoff (removes infra-sub)
HPF_ORDER = 4                  # Butterworth filter order
TARGET_RMS_DBFS = -18.0       # Target RMS level for normalisation
CROSSFADE_SECONDS = 22.0      # Crossfade duration (≈ 8 bars at 129 BPM)


# ── DSP helpers ─────────────────────────────────────────────────────────────

def highpass_filter(audio: np.ndarray, sr: int, cutoff_hz: float = HPF_CUTOFF_HZ) -> np.ndarray:
    """Apply a 4th-order Butterworth high-pass filter."""
    sos = butter(HPF_ORDER, cutoff_hz / (sr / 2), btype="high", output="sos")
    if audio.ndim == 1:
        return sosfilt(sos, audio)
    # Stereo: filter each channel
    return np.stack([sosfilt(sos, audio[:, ch]) for ch in range(audio.shape[1])], axis=1)


def rms_normalise(audio: np.ndarray, target_dbfs: float = TARGET_RMS_DBFS) -> np.ndarray:
    """Normalise audio to a target RMS level in dBFS."""
    rms = np.sqrt(np.mean(audio ** 2))
    if rms < 1e-9:
        return audio
    target_rms = 10 ** (target_dbfs / 20.0)
    gain = target_rms / rms
    # Never clip — if gain would exceed +12 dB, cap it
    gain = min(gain, 4.0)
    return audio * gain


def equal_power_crossfade(a: np.ndarray, b: np.ndarray, cf_samples: int) -> np.ndarray:
    """
    Crossfade the tail of `a` into the head of `b` using equal-power curves.

    Returns the merged audio (a[:-cf_samples] + crossfade region + b[cf_samples:]).
    """
    cf_samples = min(cf_samples, len(a), len(b))
    t = np.linspace(0, math.pi / 2, cf_samples)
    fade_out = np.cos(t)   # 1 → 0
    fade_in  = np.sin(t)   # 0 → 1

    # Handle mono / stereo
    if a.ndim == 2:
        fade_out = fade_out[:, np.newaxis]
        fade_in  = fade_in[:, np.newaxis]

    overlap = a[-cf_samples:] * fade_out + b[:cf_samples] * fade_in

    return np.concatenate([a[:-cf_samples], overlap, b[cf_samples:]], axis=0)


# ── Core pipeline ────────────────────────────────────────────────────────────

def process_track(audio_path: Path, sr_target: int | None = None) -> tuple[np.ndarray, int]:
    """Load, HPF, and normalise a single track. Returns (audio, sample_rate)."""
    audio, sr = sf.read(str(audio_path), always_2d=False, dtype="float32")
    if sr_target is not None and sr != sr_target:
        # Basic resample via scipy if needed (rare — all tracks are 48 kHz)
        from scipy.signal import resample_poly
        ratio_num = sr_target
        ratio_den = sr
        from math import gcd
        g = gcd(ratio_num, ratio_den)
        audio = resample_poly(audio, ratio_num // g, ratio_den // g)
        sr = sr_target

    audio = highpass_filter(audio, sr)
    audio = rms_normalise(audio)
    # Hard-clip safety + ensure float32
    audio = np.clip(audio, -1.0, 1.0).astype(np.float32)
    return audio, sr


def compute_mix_length(tracks: list[tuple[np.ndarray, int]], crossfade_seconds: float) -> tuple[int, int]:
    """Compute total sample count and sample rate for the final mix."""
    sr = tracks[0][1]
    cf = int(crossfade_seconds * sr)
    total = sum(len(t[0]) for t in tracks) - cf * (len(tracks) - 1)
    return total, sr


def build_mix(tracks: list[tuple[np.ndarray, int]], crossfade_seconds: float) -> tuple[np.ndarray, int]:
    """Build a continuous mix with equal-power crossfades."""
    if not tracks:
        raise ValueError("No tracks provided")

    sr = tracks[0][1]
    cf_samples = int(crossfade_seconds * sr)

    mix = tracks[0][0]
    for audio, track_sr in tracks[1:]:
        if track_sr != sr:
            raise ValueError(f"Sample rate mismatch: {track_sr} vs {sr}")
        mix = equal_power_crossfade(mix, audio, cf_samples)

    return mix, sr


def stream_mix_to_file(tracks: list[tuple[np.ndarray, int]],
                       crossfade_seconds: float,
                       out_path: Path) -> tuple[int, int]:
    """
    Write the continuous mix directly to a SoundFile without holding the full
    mix in RAM.  Returns (total_frames, sample_rate).
    """
    if not tracks:
        raise ValueError("No tracks provided")

    sr = tracks[0][1]
    cf = int(crossfade_seconds * sr)
    n_ch = tracks[0][0].shape[1] if tracks[0][0].ndim == 2 else 1

    total_frames, _ = compute_mix_length(tracks, crossfade_seconds)

    with sf.SoundFile(str(out_path), mode="w", samplerate=sr,
                      channels=n_ch, subtype="PCM_24") as f:

        pending: np.ndarray | None = None   # crossfade tail carried forward

        for idx, (audio, _) in enumerate(tracks):
            is_last = (idx == len(tracks) - 1)

            if pending is not None:
                # Blend pending crossfade region into head of this track
                blend_len = min(cf, len(pending), len(audio))
                t = np.linspace(0, math.pi / 2, blend_len)
                fade_out = np.cos(t)
                fade_in  = np.sin(t)
                if audio.ndim == 2:
                    fade_out = fade_out[:, np.newaxis]
                    fade_in  = fade_in[:, np.newaxis]
                blend = pending[:blend_len] * fade_out + audio[:blend_len] * fade_in
                f.write(blend.astype(np.float32))
                audio = audio[blend_len:]
                pending = None

            if is_last or cf == 0:
                f.write(audio.astype(np.float32))
            else:
                # Write the body, hold back the tail for next crossfade
                body = audio[:-cf] if len(audio) > cf else np.zeros(0, dtype=np.float32)
                if len(body):
                    f.write(body.astype(np.float32))
                pending = audio[-cf:].copy()

    return total_frames, sr


def stream_mix_from_disk(processed_paths: list[Path],
                         sr: int,
                         crossfade_seconds: float,
                         out_path: Path) -> tuple[int, int]:
    """
    Read processed WAV files one at a time from disk and stream the mix to
    out_path — never holds more than two tracks in RAM simultaneously.
    Returns (total_frames, sample_rate).
    """
    if not processed_paths:
        raise ValueError("No processed paths provided")

    cf = int(crossfade_seconds * sr)

    # Compute total frames without loading everything
    total_frames = 0
    for p in processed_paths:
        info = sf.info(str(p))
        total_frames += info.frames
    total_frames -= cf * (len(processed_paths) - 1)

    # Detect channels from first file
    info0 = sf.info(str(processed_paths[0]))
    n_ch = info0.channels

    with sf.SoundFile(str(out_path), mode="w", samplerate=sr,
                      channels=n_ch, subtype="PCM_24") as fout:

        pending: np.ndarray | None = None

        for idx, path in enumerate(processed_paths):
            is_last = (idx == len(processed_paths) - 1)
            audio, _ = sf.read(str(path), always_2d=(n_ch > 1), dtype="float32")

            if pending is not None:
                blend_len = min(cf, len(pending), len(audio))
                t = np.linspace(0, math.pi / 2, blend_len)
                fade_out = np.cos(t)
                fade_in  = np.sin(t)
                if audio.ndim == 2:
                    fade_out = fade_out[:, np.newaxis]
                    fade_in  = fade_in[:, np.newaxis]
                blend = pending[:blend_len] * fade_out + audio[:blend_len] * fade_in
                fout.write(blend.astype(np.float32))
                audio = audio[blend_len:]
                pending = None

            if is_last or cf == 0:
                fout.write(audio.astype(np.float32))
            else:
                body = audio[:-cf] if len(audio) > cf else np.zeros(0, dtype=np.float32)
                if len(body):
                    fout.write(body.astype(np.float32))
                pending = audio[-cf:].copy()

    return total_frames, sr


# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Assemble a continuous DJ mix from a playlist JSON")
    parser.add_argument("--playlist", required=True, help="Path to playlist.json")
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument("--crossfade", type=float, default=CROSSFADE_SECONDS,
                        help=f"Crossfade duration in seconds (default {CROSSFADE_SECONDS})")
    parser.add_argument("--hpf", type=float, default=HPF_CUTOFF_HZ,
                        help=f"High-pass filter cutoff Hz (default {HPF_CUTOFF_HZ})")
    parser.add_argument("--target-rms", type=float, default=TARGET_RMS_DBFS,
                        help=f"Target RMS in dBFS (default {TARGET_RMS_DBFS})")
    args = parser.parse_args()

    playlist_path = Path(args.playlist)
    out_dir = Path(args.out)
    processed_dir = out_dir / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)

    playlist = json.loads(playlist_path.read_text(encoding="utf-8"))
    print(f"[mix_tracks] Loaded {len(playlist)} tracks from {playlist_path}")

    sr_ref: int | None = None
    processed_paths: list[Path] = []

    # Pass 1: process each track and write to disk — free from RAM immediately
    for i, entry in enumerate(playlist, start=1):
        audio_path = Path(entry["audio"])
        title = entry.get("title", audio_path.stem)
        print(f"[mix_tracks] [{i:02d}/{len(playlist):02d}] Processing: {title}")

        if not audio_path.exists():
            print(f"  ERROR: file not found — {audio_path}", file=sys.stderr)
            sys.exit(1)

        slug = entry.get("slug", f"{i:02d}")
        out_name = f"{i:02d}-{slug}.wav"
        out_path = processed_dir / out_name
        processed_paths.append(out_path)

        if out_path.exists():
            info = sf.info(str(out_path))
            if sr_ref is None:
                sr_ref = info.samplerate
                print(f"  Sample rate: {sr_ref} Hz")
            print(f"  CACHED  {info.frames / info.samplerate:.1f}s  -> {out_path}")
            continue

        audio, sr = process_track(audio_path, sr_target=sr_ref)
        if sr_ref is None:
            sr_ref = sr
            print(f"  Sample rate: {sr} Hz")

        dur = len(audio) / sr
        rms_db = 20 * math.log10(max(np.sqrt(np.mean(audio**2)), 1e-9))
        print(f"  Duration: {dur:.1f}s  RMS: {rms_db:.1f} dBFS")
        sf.write(str(out_path), audio, sr_ref, subtype="PCM_24")
        print(f"  -> {out_path}")
        del audio  # release RAM immediately

    print(f"\n[mix_tracks] Building continuous mix ({args.crossfade}s crossfades)…")
    mix_path = out_dir / "continuous_mix.wav"
    total_frames, sr = stream_mix_from_disk(processed_paths, sr_ref, args.crossfade, mix_path)

    mix_dur = total_frames / sr
    mix_minutes = int(mix_dur // 60)
    mix_seconds = mix_dur % 60
    print(f"[mix_tracks] Mix duration: {mix_minutes}m {mix_seconds:.0f}s")
    print(f"[mix_tracks] Continuous mix written -> {mix_path}")
    print("[mix_tracks] Done.")


if __name__ == "__main__":
    main()
