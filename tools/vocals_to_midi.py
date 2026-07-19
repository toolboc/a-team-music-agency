#!/usr/bin/env python3
"""vocals_to_midi.py — Extract vocal melody and align lyrics to produce a notes JSON.

Uses YIN F0 tracking on an isolated vocal stem and aligns with a Whisper
transcript to produce a multi-segment notes JSON for ace_studio_client.py.

Each transcript segment becomes one call to add_notes_in_editor with its
lyric_sentence auto-distributed across the notes for that segment.

Algorithm:
  1. Load vocal WAV, run YIN F0 tracking (voiced frames only)
  2. For each transcript segment (filtering non-speech):
     - Count syllables → determines how many notes to create
     - Extract median F0 over the segment → MIDI pitch
     - Optionally track melodic contour (--contour) for note-by-note pitches
  3. Convert timestamps → ticks using BPM (480 PPQ)
  4. Output notes JSON with bpm + segments list

Output JSON format (consumed by ace_studio_client.py --notes):
  {
    "bpm": 123.0,
    "ppq": 480,
    "language": "ENG",
    "segments": [
      {
        "lyric_sentence": "The face of your own instability",
        "notes": [
          {"pos": 14390, "dur": 320, "pitch": 55},
          ...
        ]
      },
      ...
    ]
  }

Usage:
  python tools/vocals_to_midi.py \\
      --vocals songs/<slug>/source_vocals.wav \\
      --transcript songs/<slug>/source_transcript.json \\
      --bpm 123 \\
      --out songs/<slug>/vocals_notes.json \\
      [--language ENG] \\
      [--min-note-dur 0.08] \\
      [--pitch-shift 0] \\
      [--contour] \\
      [--max-segments 30]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
from midiutil import MIDIFile


PPQ = 480
HOP_LENGTH = 512


def seconds_to_ticks(t: float, bpm: float) -> int:
    return int(t * bpm * PPQ / 60.0)


def count_syllables(text: str) -> int:
    """Rough English syllable count using vowel-group detection."""
    text = re.sub(r"[^a-zA-Z ]", "", text).lower()
    count = len(re.findall(r"[aeiouy]+", text))
    return max(1, count)


def hz_to_midi(f0: float) -> int:
    """Convert Hz to nearest MIDI note number (clamped to [36, 84])."""
    if f0 <= 0:
        return 60
    midi = 12.0 * np.log2(f0 / 440.0) + 69.0
    return int(max(36, min(84, round(midi))))


def extract_f0(audio_path: Path, sr_target: int = 44100) -> tuple[np.ndarray, int]:
    """Run YIN F0 tracking on a mono/stereo audio file. Returns (f0_array, sr)."""
    import librosa
    y, sr = librosa.load(str(audio_path), sr=sr_target, mono=True)
    f0 = librosa.yin(y, fmin=80.0, fmax=700.0, sr=sr, hop_length=HOP_LENGTH)
    return f0, sr


def get_segment_f0(f0: np.ndarray, sr: int,
                   start_sec: float, end_sec: float,
                   contour: bool = False) -> list[int]:
    """
    Extract MIDI pitches for a segment.
    contour=False: returns [median_pitch] * n_syllables (uniform pitch per phrase).
    contour=True:  returns a pitch per note, following the melodic contour.
    """
    frame_start = max(0, int(start_sec * sr / HOP_LENGTH))
    frame_end = min(len(f0), int(end_sec * sr / HOP_LENGTH))
    segment_f0 = f0[frame_start:frame_end]

    # filter unvoiced/silence frames
    voiced = segment_f0[(segment_f0 > 80) & (segment_f0 < 700)]
    if len(voiced) < 3:
        return []

    if not contour:
        return [hz_to_midi(float(np.median(voiced)))]

    # With contour: divide segment into even windows per note and take median per window
    return voiced  # caller will handle windowing


def build_notes_for_segment(pitches_or_f0, n_syllables: int,
                             start_tick: int, dur_ticks: int,
                             min_note_ticks: int,
                             contour: bool) -> list[dict]:
    """Build a list of note dicts for one lyric segment."""
    if not contour or isinstance(pitches_or_f0, list):
        # Uniform pitch mode: one pitch for all notes
        pitch = pitches_or_f0[0] if pitches_or_f0 else 60
        notes = []
        note_dur = max(min_note_ticks, dur_ticks // n_syllables)
        for i in range(n_syllables):
            pos = start_tick + i * note_dur
            notes.append({
                "pos": pos,
                "dur": int(note_dur * 0.85),  # small gap between notes
                "pitch": int(pitch),
            })
        return notes
    else:
        # Contour mode: voiced_f0 is an array; divide into n_syllables windows
        voiced_f0 = pitches_or_f0
        note_dur = max(min_note_ticks, dur_ticks // n_syllables)
        windows = np.array_split(voiced_f0, n_syllables) if n_syllables > 1 else [voiced_f0]
        notes = []
        for i, window in enumerate(windows):
            if len(window) == 0:
                pitch = 60
            else:
                pitch = hz_to_midi(float(np.median(window[window > 80])) if np.any(window > 80) else 60)
            pos = start_tick + i * note_dur
            notes.append({
                "pos": pos,
                "dur": int(note_dur * 0.85),
                "pitch": pitch,
            })
        return notes


def main() -> None:
    ap = argparse.ArgumentParser(description="Convert vocal stem + transcript to notes JSON")
    ap.add_argument("--vocals", required=True, help="Isolated vocal stem WAV")
    ap.add_argument("--transcript", required=True,
                    help="Whisper transcript JSON (with segments[].start/end/text/no_speech_prob)")
    ap.add_argument("--bpm", type=float, required=True, help="Song BPM")
    ap.add_argument("--out", required=True, help="Output notes JSON path")
    ap.add_argument("--language", default="ENG", help="Language code for ACE Studio (default: ENG)")
    ap.add_argument("--min-note-dur", type=float, default=0.08,
                    help="Minimum note duration in seconds (default: 0.08)")
    ap.add_argument("--pitch-shift", type=int, default=0,
                    help="Semitone shift to apply to all pitches (default: 0)")
    ap.add_argument("--contour", action="store_true",
                    help="Track melodic contour note-by-note (default: uniform median per phrase)")
    ap.add_argument("--max-segments", type=int, default=0,
                    help="Limit to first N segments (0 = all)")
    ap.add_argument("--no-speech-threshold", type=float, default=0.4,
                    help="Skip segments with no_speech_prob above this (default: 0.4)")
    args = ap.parse_args()

    vocals_path = Path(args.vocals)
    transcript_path = Path(args.transcript)
    out_path = Path(args.out)

    if not vocals_path.exists():
        print(f"[vocals_to_midi] ERROR: vocals file not found: {vocals_path}", file=sys.stderr)
        sys.exit(1)
    if not transcript_path.exists():
        print(f"[vocals_to_midi] ERROR: transcript not found: {transcript_path}", file=sys.stderr)
        sys.exit(1)

    bpm = args.bpm
    min_note_ticks = seconds_to_ticks(args.min_note_dur, bpm)

    print(f"[vocals_to_midi] extracting F0 from {vocals_path.name} ...")
    f0, sr = extract_f0(vocals_path)
    print(f"[vocals_to_midi] {len(f0)} F0 frames at sr={sr}, hop={HOP_LENGTH}")

    transcript = json.loads(transcript_path.read_text())
    segments_in = transcript.get("segments", [])
    print(f"[vocals_to_midi] {len(segments_in)} transcript segments")

    segments_out = []
    skipped = 0

    for seg in segments_in:
        if args.max_segments and len(segments_out) >= args.max_segments:
            break

        no_speech = seg.get("no_speech_prob", 0.0)
        if no_speech > args.no_speech_threshold:
            skipped += 1
            continue

        text = seg.get("text", "").strip()
        if not text or len(text) < 2:
            skipped += 1
            continue

        start_sec = float(seg["start"])
        end_sec = float(seg["end"])
        if end_sec - start_sec < 0.2:
            skipped += 1
            continue

        start_tick = seconds_to_ticks(start_sec, bpm)
        end_tick = seconds_to_ticks(end_sec, bpm)
        dur_ticks = max(PPQ // 2, end_tick - start_tick)

        n_syl = count_syllables(text)

        if args.contour:
            pitched = get_segment_f0(f0, sr, start_sec, end_sec, contour=True)
            if len(pitched) < 3:
                skipped += 1
                continue
            notes = build_notes_for_segment(pitched, n_syl, start_tick, dur_ticks,
                                            min_note_ticks, contour=True)
        else:
            pitches = get_segment_f0(f0, sr, start_sec, end_sec, contour=False)
            if not pitches:
                skipped += 1
                continue
            notes = build_notes_for_segment(pitches, n_syl, start_tick, dur_ticks,
                                            min_note_ticks, contour=False)

        # Apply pitch shift
        if args.pitch_shift:
            for n in notes:
                n["pitch"] = max(36, min(84, n["pitch"] + args.pitch_shift))

        segments_out.append({
            "lyric_sentence": text,
            "notes": notes,
        })

    print(f"[vocals_to_midi] {len(segments_out)} segments → notes, {skipped} skipped")

    output = {
        "bpm": bpm,
        "ppq": PPQ,
        "language": args.language,
        "segments": segments_out,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2))
    total_notes = sum(len(s["notes"]) for s in segments_out)
    print(f"[vocals_to_midi] wrote {total_notes} notes across {len(segments_out)} segments → {out_path}")

    # Also write a standard .mid file alongside the JSON
    midi_path = out_path.with_suffix(".mid")
    mid = MIDIFile(1)
    mid.addTempo(0, 0, bpm)
    beats_per_tick = 1.0 / PPQ
    for seg in segments_out:
        for note in seg["notes"]:
            beat = note["pos"] * beats_per_tick
            dur  = note["dur"] * beats_per_tick
            mid.addNote(0, 0, note["pitch"], beat, dur, volume=100)
    with open(midi_path, "wb") as f:
        mid.writeFile(f)
    print(f"[vocals_to_midi] wrote MIDI → {midi_path}")


if __name__ == "__main__":
    main()
