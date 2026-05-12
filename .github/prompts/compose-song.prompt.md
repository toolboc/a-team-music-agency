---
description: "Produce a song from a theme. Hands the theme to the conductor, who runs the lyricist → producer → critic loop until the song ships."
argument-hint: "theme description (e.g. 'song based on Edgar Allan Poe — The Raven')"
---

You are kicking off a music production session. Hand off to the **conductor** agent with the following brief:

**Theme**: ${input:theme:Describe the song's theme}
**Track count**: ${input:tracks:1}
**Target duration (seconds, per track)**: ${input:duration:150}
**Hard constraints (genre, mood, vocal style — leave blank for free hand)**: ${input:constraints:}

Instructions for the conductor:
1. Derive a slug per `memory/conventions.md`.
2. Run the full pipeline: `lyricist` → `producer` → `critic` → refine loop (max 5 iterations).
3. Write `songs/<slug>/final.md` and report back the path to the shipped iteration.
