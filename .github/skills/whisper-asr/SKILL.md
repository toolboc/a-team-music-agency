# Whisper ASR Transcription Skill

Use when you need to transcribe audio from any song iteration or source file — for remix lyric extraction, lyric-adherence checking, or building new prompt lyrics from a rendered output.

## Service

- **Endpoint**: `http://192.168.1.171:9000` (Jetson-hosted whisper-asr-webservice)
- **Route**: `POST /asr?task=transcribe&language=en&output=json`
- **Field**: `audio_file` (multipart form upload)
- Accepts: `.wav`, `.mp3`, `.ogg`, etc.
- Returns JSON with `segments[]` each containing `start`, `end`, `text`

## Usage Pattern

```python
import requests

def transcribe(audio_path: str, asr_url: str = "http://192.168.1.171:9000") -> list[dict]:
    url = f"{asr_url}/asr?task=transcribe&language=en&output=json"
    with open(audio_path, "rb") as f:
        r = requests.post(url, files={"audio_file": ("audio", f)}, timeout=300)
    r.raise_for_status()
    return r.json()["segments"]
```

## Cleaning transcription for lyrics use

```python
def segments_to_lyrics(segments: list[dict], cutoff_seconds: float = None) -> str:
    """
    Convert Whisper segments to clean lyric text.
    - cutoff_seconds: drop segments after this point (Whisper hallucinates on silence/noise)
    - Short fragments (<3 words) after the main body are usually noise artifacts — drop them
    """
    lines = []
    for seg in segments:
        if cutoff_seconds and seg["start"] > cutoff_seconds:
            break
        text = seg["text"].strip()
        if len(text.split()) >= 3:
            lines.append(text)
    return "\n".join(lines)
```

## Detecting hallucination tail

Whisper often hallucinates on silence or instrumental sections after the vocals end — single words, foreign words, gibberish. Signs:
- Segments with `avg_logprob < -0.5` (low confidence)
- Very short segments (< 2 words)
- `no_speech_prob > 0.4`

Apply a `cutoff_seconds` at the last clearly voiced segment before the noise starts.

## Workflow: Remix from transcribed lyrics

1. Transcribe the source audio via the ASR service
2. Clean the segments, identify verse/chorus structure by timing
3. Format into `[verse]` / `[chorus]` / `[bridge]` / `[outro]` blocks
4. Write new `prompt.json` with `source_audio` pointing at the source file and `retake_variance: 0.5`
5. Replace `lyrics` field with the cleaned transcription
6. Render via `ace_step_client.py`

## Notes

- The service runs on the same Jetson as ACE-Step (192.168.1.171)
- No API key required on the local network
- Timeout of 300s is sufficient for songs up to ~10 min
- For songs with long instrumental outros, set `cutoff_seconds` to avoid hallucination garbage in the lyrics
