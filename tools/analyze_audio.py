"""Audio analysis CLI for the critic.

Loads a rendered audio file, extracts musical features with librosa, and
optionally compares them against the brief (e.g. checking lyric overlap if
Whisper is installed). Writes a JSON report.

Usage:
    python tools/analyze_audio.py --audio path/to/audio.wav \
        --brief path/to/brief.md --out path/to/analysis.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

KEY_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Krumhansl-Schmuckler key profiles (major / minor) for cheap key detection.
MAJOR_PROFILE = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
MINOR_PROFILE = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


def detect_key(chroma_mean) -> dict[str, Any]:
    import numpy as np

    scores = []
    for i in range(12):
        major_corr = np.corrcoef(chroma_mean, np.roll(MAJOR_PROFILE, i))[0, 1]
        minor_corr = np.corrcoef(chroma_mean, np.roll(MINOR_PROFILE, i))[0, 1]
        scores.append((KEY_NAMES[i] + " major", float(major_corr)))
        scores.append((KEY_NAMES[i] + " minor", float(minor_corr)))
    scores.sort(key=lambda x: x[1], reverse=True)
    best, best_score = scores[0]
    return {"detected_key": best, "confidence": round(best_score, 3),
            "runner_up": scores[1][0]}


def analyze(audio_path: Path) -> dict[str, Any]:
    try:
        import librosa
        import numpy as np
    except ImportError as e:
        raise SystemExit(
            "librosa not installed. Run: pip install -r tools/requirements.txt"
        ) from e

    y, sr = librosa.load(str(audio_path), sr=22050, mono=True)
    duration = float(len(y) / sr)

    # ── Tempo & beats ────────────────────────────────────────────────────────
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
    tempo_val = float(tempo) if not hasattr(tempo, "__len__") else float(tempo[0])

    # Beat regularity: std-dev of inter-beat intervals (lower = more regular)
    if len(beats) > 1:
        ibi = np.diff(librosa.frames_to_time(beats, sr=sr))
        beat_regularity = {"ibi_std_ms": round(float(ibi.std() * 1000), 2),
                           "ibi_mean_ms": round(float(ibi.mean() * 1000), 2),
                           "regularity_score": round(float(1 - min(ibi.std() / max(ibi.mean(), 1e-6), 1)), 3)}
    else:
        beat_regularity = {"ibi_std_ms": None, "ibi_mean_ms": None, "regularity_score": None}

    # ── Key detection ────────────────────────────────────────────────────────
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    chroma_mean = chroma.mean(axis=1)
    key_info = detect_key(chroma_mean)

    # ── Energy & dynamics ────────────────────────────────────────────────────
    rms = librosa.feature.rms(y=y)[0]
    rms_peak = float(rms.max())
    rms_mean = float(rms.mean())
    # Crest factor: ratio of peak amplitude to RMS — higher = more punch/dynamics
    peak_amplitude = float(np.abs(y).max())
    rms_signal = float(np.sqrt(np.mean(y ** 2)))
    crest_factor_db = float(20 * np.log10(peak_amplitude / max(rms_signal, 1e-9)))

    # ── Spectral features ────────────────────────────────────────────────────
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr)[0]
    zcr = librosa.feature.zero_crossing_rate(y)[0]
    bandwidth = librosa.feature.spectral_bandwidth(y=y, sr=sr)[0]
    flatness = librosa.feature.spectral_flatness(y=y)[0]  # 0=tonal, 1=noisy/textured

    # Spectral contrast: difference between peaks and valleys per sub-band
    # 7 bands; higher contrast = more punchy, less compressed
    contrast = librosa.feature.spectral_contrast(y=y, sr=sr, n_bands=6)

    # ── FFT frequency band energy breakdown ──────────────────────────────────
    # Use STFT magnitude for per-band energy
    S = np.abs(librosa.stft(y))
    freqs = librosa.fft_frequencies(sr=sr)

    def band_energy(f_low: float, f_high: float) -> float:
        mask = (freqs >= f_low) & (freqs < f_high)
        if not mask.any():
            return 0.0
        return float(np.mean(S[mask, :] ** 2))

    total_energy = float(np.mean(S ** 2)) or 1e-9
    bands = {
        "sub_bass_20_80hz":    round(band_energy(20,   80)   / total_energy, 4),
        "bass_80_250hz":       round(band_energy(80,   250)  / total_energy, 4),
        "low_mid_250_500hz":   round(band_energy(250,  500)  / total_energy, 4),
        "mid_500_2000hz":      round(band_energy(500,  2000) / total_energy, 4),
        "high_mid_2000_4000hz":round(band_energy(2000, 4000) / total_energy, 4),
        "presence_4000_6000hz":round(band_energy(4000, 6000) / total_energy, 4),
        "brilliance_6000hz_plus": round(band_energy(6000, sr / 2) / total_energy, 4),
    }

    # ── Harmonic / Percussive separation ─────────────────────────────────────
    y_harm, y_perc = librosa.effects.hpss(y)
    harm_energy = float(np.mean(y_harm ** 2))
    perc_energy = float(np.mean(y_perc ** 2))
    total_hp = harm_energy + perc_energy or 1e-9
    hp_ratio = {
        "harmonic_ratio": round(harm_energy / total_hp, 3),
        "percussive_ratio": round(perc_energy / total_hp, 3),
        # >0.5 percussive = drum-forward; >0.5 harmonic = melody/pad-forward
    }

    # ── Vocal F0 estimation (YIN on harmonic signal) ──────────────────────────
    # Run YIN pitch tracker on the harmonic component only (percussion removed).
    # fmin=150 Hz (above bass guitar range, E2=82Hz, D3=147Hz) so that a
    # prominent bass guitar doesn't dominate and pull median down into 80–110 Hz.
    # Typical male tenor sits at 150–520 Hz; female vocal 200–1000 Hz.
    try:
        f0 = librosa.yin(y_harm, fmin=150.0, fmax=700.0, sr=sr)
        # Per-frame RMS of harmonic signal — use to mask unvoiced frames
        hop = 512
        frame_rms = np.array([
            float(np.sqrt(np.mean(y_harm[i * hop: i * hop + hop] ** 2)))
            for i in range(len(f0))
        ])
        rms_threshold = float(np.percentile(frame_rms, 40))  # bottom 40% = unvoiced/quiet
        voiced = f0[(frame_rms >= rms_threshold) & (f0 > 150) & (f0 < 680)]
        if len(voiced) > 10:
            f0_median = float(np.median(voiced))
            f0_std = float(np.std(voiced))
            # Register label based on median F0
            if f0_median < 130:
                register = "deep bass / industrial baritone"
            elif f0_median < 180:
                register = "baritone"
            elif f0_median < 250:
                register = "tenor / mid male"
            elif f0_median < 330:
                register = "high male / low female"
            else:
                register = "high / female"
        else:
            f0_median = None
            f0_std = None
            register = "undetermined"
        vocal_f0 = {
            "f0_median_hz": round(f0_median, 1) if f0_median is not None else None,
            "f0_std_hz": round(f0_std, 1) if f0_std is not None else None,
            "voiced_frame_count": int(len(voiced)) if voiced is not None else 0,
            "register": register,
        }
    except Exception:
        vocal_f0 = {"f0_median_hz": None, "f0_std_hz": None, "voiced_frame_count": 0,
                    "register": "undetermined"}

    # ── Structure ─────────────────────────────────────────────────────────────
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    bounds = librosa.segment.agglomerative(
        librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13), k=8
    )

    return {
        "duration_seconds": round(duration, 2),
        "tempo_bpm": round(tempo_val, 2),
        "beat_count": int(len(beats)),
        "beat_regularity": beat_regularity,
        "key": key_info,
        "energy": {
            "rms_mean": rms_mean,
            "rms_std": float(rms.std()),
            "rms_peak": rms_peak,
            "dynamic_range_db": float(20 * np.log10(rms_peak / max(rms_mean, 1e-6))),
            "crest_factor_db": round(crest_factor_db, 2),
        },
        "spectral": {
            "centroid_hz_mean": float(centroid.mean()),
            "centroid_hz_std": float(centroid.std()),
            "rolloff_hz_mean": float(rolloff.mean()),
            "bandwidth_hz_mean": float(bandwidth.mean()),
            "zero_crossing_rate_mean": float(zcr.mean()),
            "onset_strength_mean": float(onset_env.mean()),
            "flatness_mean": round(float(flatness.mean()), 4),  # 0=tonal 1=textured/noisy
            "spectral_contrast_db_mean": [round(float(v), 2) for v in contrast.mean(axis=1)],
        },
        "frequency_bands": bands,
        "harmonic_percussive": hp_ratio,
        "vocal_f0": vocal_f0,
        "structure": {
            "segment_boundaries": [int(b) for b in bounds.tolist()],
            "segment_count": int(len(set(bounds.tolist()))),
        },
    }


def parse_brief_lyrics(brief_path: Path) -> str | None:
    try:
        text = brief_path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"^##\s*Lyrics\s*\n(.+?)(?=^##\s|\Z)", text, re.MULTILINE | re.DOTALL)
    if not m:
        return None
    body = m.group(1)
    body = re.sub(r"\[[^\]]+\]", " ", body)  # drop [verse] markers
    return body.strip()


def transcribe_audio(audio_path: Path) -> str | None:
    """Optional. Requires `pip install openai-whisper`."""
    try:
        import whisper  # type: ignore
    except ImportError:
        return None
    model = whisper.load_model("base")
    result = model.transcribe(str(audio_path), fp16=False)
    return result.get("text", "").strip() or None


def lyric_overlap(brief_lyrics: str, transcription: str) -> dict[str, Any]:
    norm = lambda s: set(re.findall(r"[a-z']+", s.lower()))
    brief_words = norm(brief_lyrics)
    heard_words = norm(transcription)
    if not brief_words:
        return {"overlap_ratio": None, "shared_word_count": 0}
    shared = brief_words & heard_words
    return {
        "overlap_ratio": round(len(shared) / len(brief_words), 3),
        "shared_word_count": len(shared),
        "brief_word_count": len(brief_words),
        "heard_word_count": len(heard_words),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Analyze a rendered song against its brief.")
    p.add_argument("--audio", required=True, type=Path)
    p.add_argument("--brief", type=Path, default=None)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--transcribe", action="store_true",
                   help="Attempt Whisper transcription (requires openai-whisper)")
    args = p.parse_args()

    if not args.audio.exists():
        print(json.dumps({"ok": False, "error": f"audio not found: {args.audio}"}))
        return 2

    report: dict[str, Any] = {"ok": True, "audio": str(args.audio)}
    try:
        report["analysis"] = analyze(args.audio)
    except Exception as e:
        report["ok"] = False
        report["error"] = f"analysis failed: {e!r}"

    if args.transcribe:
        text = transcribe_audio(args.audio)
        report["transcription"] = text
        if text and args.brief and args.brief.exists():
            brief_lyrics = parse_brief_lyrics(args.brief)
            if brief_lyrics:
                report["lyric_adherence"] = lyric_overlap(brief_lyrics, text)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
