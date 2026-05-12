---
name: ace-step
description: "Use when working with ACE-STEP 1.5 — calling the API, interpreting its parameters, debugging render failures, or updating tools/ace_step_client.py. Contains the verified /generation_wrapper signature for the Jetson docker-compose build."
---

# ACE-STEP 1.5 — API Reference & Call Guide

ACE-STEP 1.5 is a music generation model served as a Gradio app.
This skill documents the **exact verified API** of our local Jetson docker-compose instance.

## Endpoint

```
POST  <ACE_STEP_URL>/gradio_api/queue/join
api_name: /generation_wrapper   (fn_index 126)
```

Configured via `ACE_STEP_URL` env var. Never hardcode a URL.

## How to call it (Python)

```python
from gradio_client import Client
client = Client(url, verbose=False)
result = client.predict(*args, api_name="/generation_wrapper")
```

`result` is a list of FileData dicts — one per `batch_size`.
Each dict has a `"path"` key pointing to the rendered file on the **server**.
`gradio_client` copies it to a local temp path; that temp path is what `"path"` resolves to.

The project wrapper `tools/ace_step_client.py` handles all of this. Use it:

```powershell
python tools/ace_step_client.py --prompt songs/<slug>/iterations/NN/prompt.json \
                                 --out    songs/<slug>/iterations/NN/audio.wav
```

## /generation_wrapper — Full Parameter List (69 args, positional)

| # | Label | Type | Default | Notes |
|---|-------|------|---------|-------|
| 0 | Music Caption | str | — | Genre/mood/instrumentation tags |
| 1 | Lyrics | str | — | Section-marked: `[verse]` `[chorus]` `[bridge]` `[outro]` |
| 2 | BPM | float\|None | None | Explicit BPM; None = auto-detect |
| 3 | Key | str | `""` | e.g. `"D minor"`; `""` = auto |
| 4 | Time Signature | `''|'2'|'3'|'4'|'6'|'N/A'` | `""` | |
| 5 | Vocal Language | str (ISO code) | `"unknown"` | Use `"en"` for English |
| 6 | DiT Inference Steps | float | 8 | Range 1–20; 20 = highest quality |
| 7 | DiT Guidance Scale | float | 7.0 | Range 1.0–15.0 |
| 8 | Random Seed (bool) | bool | True | True = new seed every run |
| 9 | Seed | str | `"-1"` | Only used when [8]=False |
| 10 | Reference Audio | filepath\|None | None | Audio conditioning; None = text-only |
| 11 | Audio Duration (s) | float | -1 | -1 = auto from lyrics; 180 = 3 min |
| 12 | Batch Size | float | 2 | 1 = single result (faster on Jetson) |
| 13 | Source Audio | filepath\|None | None | For repainting/editing |
| 14–19 | Repainting params | various | defaults | Only for edit mode |
| 20 | task_type | str | `"text2music"` | Always `"text2music"` for generation |
| 21–29 | Guidance/sampler params | various | defaults | See full list below |
| 26 | Inference Method | `'ode'|'sde'` | `"ode"` | |
| 27 | Sampler Mode | `'euler'|'heun'` | `"euler"` | |
| 36 | Audio Format | `'wav'|'mp3'|'flac'|'opus'|'aac'|'wav32'` | `"mp3"` | **Always use `"wav"`** |
| 39 | LM Temperature | float | 0.85 | Range 0.0–2.0; lower = more conservative |
| 44 | LM Negative Prompt | str | `"NO USER INPUT"` | Use `""` or describe what to avoid |
| 54 | Track Name | str\|None | None | Single-stem isolation; None = full mix |
| 55 | Track Names | list[str] | `[]` | Multi-stem; `[]` = full mix |
| 56 | Enable Normalization | bool | True | |
| 58 | Fade In (s) | float | 0.0 | |
| 59 | Fade Out (s) | float | 0.0 | |
| 66–68 | Edit params | various | defaults | Only for audio editing mode |

Available Track Names (stems): `woodwinds`, `brass`, `fx`, `synth`, `strings`, `percussion`, `keyboard`, `guitar`, `bass`, `drums`, `backing_vocals`, `vocals`

## prompt.json Schema

The project standard (stored in `memory/conventions.md`):

```json
{
  "_brief":            "songs/<slug>/brief.md",
  "_iteration":        "01",
  "_refinement_notes": "Initial render OR cite critic notes addressed",
  "tags":              "comma-separated genre, mood, instrumentation tags",
  "lyrics":            "[verse]\n...\n[chorus]\n...",
  "tempo_bpm":         72,
  "key":               "D minor",
  "vocal_language":    "en",
  "duration":          180,
  "batch_size":        1,
  "infer_steps":       20,
  "guidance_scale":    7.0,
  "seed":              -1,
  "lm_temperature":    0.85,
  "negative_prompt":   "upbeat, happy, major key, ..."
}
```

Fields prefixed `_` are stripped before sending to ACE-STEP (metadata only).
`seed: -1` → random seed every run. Set to a specific integer to reproduce.

## Typical Jetson Performance

- Jetson Orin (docker-compose): ~3–6 min per 180s track at `infer_steps=20`
- `batch_size=1` is strongly recommended on Jetson to avoid OOM
- `infer_steps=20` is the effective maximum for quality; 8 is the default (faster)

## Debugging Failures

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Connection refused` | ACE-STEP not running | `docker-compose up` on Jetson |
| `No usable audio path` | Result is None or temp file deleted | Check Gradio temp dir; increase timeout |
| `SyntaxError / TypeError` | Wrong arg count | Verify 69 args passed positionally |
| Audio < 50 KB | Model returned silence / 0s clip | Increase `guidance_scale`; check lyrics format |
| No section markers | Lyrics missing `[verse]`/`[chorus]` | Add markers — ACE-STEP uses them for structure |

## Notes on the Jetson docker-compose build

- Runs Gradio 5.x with SSE-v3 (`/gradio_api/queue/join`)
- `gradio_client >= 1.3.0` required (already in `tools/requirements.txt`)
- Audio temp files are written inside the container; `gradio_client` fetches them over HTTP
