# Conventions

Append-only list of established conventions.

---

### Song slug format
Lowercase, hyphen-separated, max 40 chars, derived from the theme.
Example: theme *"Edgar Allan Poe — The Raven"* → slug `poe-the-raven`.

### Iteration directory naming
Zero-padded two-digit numbers: `iterations/01/`, `iterations/02/`, ...
Never overwrite an existing iteration; always create the next number.

### Brief structure (`songs/<slug>/brief.md`)
Required sections, in order:
1. `# Theme` — one paragraph
2. `## Source Material` — bullet list of references
3. `## Sonic Palette` — `genre`, `tempo (BPM)`, `key`, `mood`, `instrumentation`, `vocal style`, `duration (s)`
4. `## Lyrics` — full lyrics with section markers (`[verse]`, `[chorus]`, `[bridge]`)
5. `## Acceptance Criteria` — checkable bullet list the critic scores against

### ACE-STEP prompt fields (`prompt.json`)
```json
{
  "_brief":            "songs/<slug>/brief.md",
  "_iteration":        "NN",
  "_refinement_notes": "Initial render OR cite critic notes addressed",
  "tags":              "comma-separated genre/mood/instrumentation tags",
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
  "negative_prompt":   "upbeat, happy, major key, electronic"
}
```
Fields prefixed `_` are metadata only — stripped before sending to ACE-STEP.
`seed: -1` = random. Set an integer to reproduce a specific render.
Full parameter reference: `.github/skills/ace-step/SKILL.md`.

### Critic verdicts
The critic's `critique.md` MUST end with exactly one of:
- `VERDICT: ship`
- `VERDICT: refine-producer`
- `VERDICT: refine-lyricist`

### ACE-STEP cover mode: dual-audio technique (Demucs pre-strip)
When a cover source has strong vocal presence that bleeds through as audible singing:
1. Run `tools/strip_vocals.py` to produce a vocals-stripped WAV via Demucs (`htdemucs`, `--two-stems vocals`).
2. Set `source_audio` = the stripped WAV → LM codes are built from vocal-free DNA.
3. Set `reference_audio` = the original source → melodic contour and style conditioning come from the full mix.
4. ACE-STEP then blueprints timbral character from an instrument-only source while tracking melody from the original.
Example prompt.json:
```json
{
  "task_type": "cover",
  "source_audio": "songs/<slug>/source_novocals.wav",
  "reference_audio": "songs/<slug>/source.mp3",
  "lm_codes_strength": 0.52,
  "cover_strength": 0.35
}
```

### ACE-STEP cover mode: vocal bleed parameter guide
| lm_codes_strength | Effect |
|-------------------|--------|
| 0.55+ | Strong melody lock, source instruments (incl. vocals) bleed through |
| 0.48–0.52 | Good melody, occasional vocal timbre inheritance |
| 0.38–0.45 | Weak vocal bleed but model may lose melody thread |
| <0.35 | Melody lost; model free-generates |

To break held-note vocal timbre without lowering `lm_codes_strength`:
- Set `lm_temperature: 1.3` (adds LM token sampling noise, breaks exact timbre reproduction)
- Set `vocal_language: ""` (empty string, stops model from priming into vocal output mode)

Avoid parenthetical descriptions in the `lyrics` field — the model will sing them literally.
Use bare section markers only: `[Intro]\n\n[Verse 1]\n\n[Chorus]\n\n[Outro]\n`

### tools/screw_and_chop.py — DJ Screw post-processing
CLI tool for chopped & screwed audio transformation. Uses librosa + scipy (no extra installs).
Key flags:
- `--speed` (default 0.73): time-stretch without pitch shift (slows track)
- `--semitones` (default -4): pitch shift in semitones after stretch
- `--chop-every` (default 2): bars between chop repeat points
- `--chop-repeats` (default 2): how many times each chop loops
- `--echo-delay` (default 220ms), `--echo-decay` (default 0.30), `--echo-repeats` (default 5)
- `--no-screw`, `--no-chop`, `--no-echo`: disable individual stages
Uses HPSS (harmonic-percussive source separation) to preserve drum transients through time-stretch.
Apply 3ms micro-crossfades on chop cuts to prevent clicks.
Example:
```powershell
.venv\Scripts\python.exe tools/screw_and_chop.py --audio input.wav --out output_screwed.wav
```

### tools/strip_vocals.py — Demucs vocal removal
CLI tool to strip vocals from audio using Demucs. Requires `pip install demucs` (model ~80MB, auto-downloaded on first run).
Uses `--two-stems vocals` mode: produces `vocals.wav` + `no_vocals.wav` in a temp dir, copies `no_vocals.wav` to `--out`.
Default model: `htdemucs` (4-stem, fastest for instrumental extraction).
Example:
```powershell
.venv\Scripts\python.exe tools/strip_vocals.py --audio songs/<slug>/source.mp3 --out songs/<slug>/source_novocals.wav
```
