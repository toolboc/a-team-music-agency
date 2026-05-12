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
