# Project Guidelines — A-Team Music Production Agency

A squad of custom GitHub Copilot agents that simulate a music production studio.
The team researches a theme, writes lyrics, generates a song with ACE-STEP 1.5,
critically listens to the result, and iterates until the song matches the brief.

## The Squad

| Agent | Codename | Role |
|-------|----------|------|
| `conductor` | Quincy | Orchestrator. Drives the produce → critique → refine loop. |
| `lyricist` | Cohen | Researches the theme, writes lyrics, drafts the production brief (genre, tempo, mood, instrumentation). |
| `producer` | Rubin | Calls ACE-STEP 1.5 (URL from `ACE_STEP_URL` env var) to render audio from a brief + lyrics. |
| `critic` | Pauline | Listens back, analyzes the rendered audio, scores it against the brief, and emits actionable refinement notes. |

Default flow: `conductor → lyricist → producer → critic → (refine? back to producer or lyricist) → done`.

## ACE-STEP Backend

- **Endpoint**: configured via the `ACE_STEP_URL` environment variable (Gradio app). The conductor prompts for this on first run if unset.
- **Wrapper**: `tools/ace_step_client.py` — CLI invoked by `producer`
- **Audio analysis**: `tools/analyze_audio.py` — CLI invoked by `critic` (librosa-based)
- Set `ACE_STEP_URL` env var to override the endpoint.

## Artifacts

Each song lives under `songs/<slug>/` and contains:

```
songs/<slug>/
├── brief.md         # Theme, genre, mood, tempo, lyrics — written by lyricist
├── prompt.json      # Resolved ACE-STEP parameters — written by producer
├── iterations/
│   ├── 01/
│   │   ├── audio.wav
│   │   ├── analysis.json   # Tempo/key/energy/etc. from librosa
│   │   └── critique.md     # Critic's verdict + refinement notes
│   └── 02/...
└── final.md         # Conductor's wrap-up: which iteration shipped & why
```

The `specs/` directory holds longer-form production specs when the conductor
decides a song needs a dedicated plan (e.g. concept albums, multi-track suites).

## Shared Memory

The project maintains shared memory in `memory/`:

- `memory/decisions.md` — Production decisions (chosen sound palettes, why an iteration was discarded, etc.)
- `memory/conventions.md` — Established conventions (slug format, ACE-STEP parameter ranges that work, critic scoring rubric)

### Reading
Before drafting a brief, generating audio, or critiquing, check existing
decisions and conventions for prior context.

### Writing
When a new decision is made or convention established:
1. Read the current file
2. Append the new entry at the end
3. Do not modify or remove existing entries

**Decision format:**
```
### <Decision Title>
- **Date**: YYYY-MM-DD
- **Song**: <slug or "global">
- **Context**: What prompted this decision
- **Decision**: What was decided
- **Rationale**: Why this choice
- **Alternatives**: What else was considered
```

**Convention format:**
```
### <Convention Name>
<Clear description with example if helpful>
```

## Pipeline Rules

- **One song in flight at a time.** The conductor runs a single produce → critique
  loop to completion before starting a new song, to keep iterations and memory
  attributable.
- **Always pass through the critic.** No song is considered done until the
  critic emits a `VERDICT: ship` (or the conductor explicitly overrides with a
  recorded decision).
- **Iterations are immutable.** Never overwrite `iterations/NN/` — always create
  the next number.
- **Bounded refinement.** Cap at 5 iterations per song. If still failing, the
  conductor escalates back to the lyricist (re-brief) or to the user.
