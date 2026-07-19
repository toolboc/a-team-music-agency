#!/usr/bin/env python3
"""ace_studio_client.py — Drive ACE Studio via its local MCP server.

Pipeline:
  1. Connect to the local ACE Studio MCP server (must be running, MCP enabled in settings)
  2. Set project tempo from the notes file
  3. Find a voice matching --voice-tags / --voice-keyword
  4. Create a new Sing track and load the voice
  5. Add a clip spanning the full song duration
  6. Open the pattern editor for that clip
  7. Add MIDI notes with lyrics (lyric_sentence auto-distributed, or per-note lyric)
  8. Poll synthesis until complete

Notes JSON format (--notes):
  {
    "bpm": 120,
    "language": "ENG",
    "lyric_sentence": "Hello world, this is a test",
    "notes": [
      {"pos": 0,   "dur": 480, "pitch": 60},
      {"pos": 480, "dur": 480, "pitch": 62}
    ]
  }

  pos/dur are in ticks. ACE Studio uses 480 PPQ by default (480 ticks = 1 quarter note).
  pitch is MIDI note number (60 = middle C).

Export:
  ACE Studio does not expose audio export through its MCP API.
  After synthesis completes, export manually via File > Export Audio in ACE Studio,
  or use Ctrl+Shift+E. Save to songs/<slug>/iterations/NN/vocals.wav.

Usage:
  python tools/ace_studio_client.py \\
      --notes songs/<slug>/vocals.json \\
      --voice-tags "Female,Pop" \\
      [--voice-keyword "Luna"] \\
      [--track-name "Vocals"] \\
      [--list-voices] \\
      [--url http://127.0.0.1:21572]

Requirements:
  ACE Studio must be open with MCP Server enabled (Settings > MCP Server > Enable).
  Set ACE_STUDIO_MCP_URL env var to override the default URL.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path


DEFAULT_URL = os.environ.get("ACE_STUDIO_MCP_URL", "http://127.0.0.1:21572")
PPQ = 480  # ticks per quarter note (ACE Studio default)


# ---------------------------------------------------------------------------
# MCP transport
# ---------------------------------------------------------------------------

class MCPSession:
    """Minimal stateful MCP-over-HTTP client (Streamable HTTP / SSE transport)."""

    def __init__(self, base_url: str = DEFAULT_URL):
        self.base = base_url.rstrip("/")
        self._session_id: str | None = None
        self._seq = 0

    def _next_id(self) -> int:
        self._seq += 1
        return self._seq

    def _post(self, method: str, params: dict | None = None) -> dict:
        payload = json.dumps({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params or {},
        }).encode()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        req = urllib.request.Request(self.base + "/mcp", data=payload, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                new_sid = r.headers.get("Mcp-Session-Id")
                if new_sid:
                    self._session_id = new_sid
                raw = r.read().decode()
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            raise RuntimeError(f"MCP HTTP {e.code}: {e.reason} — {body}") from e

        # Parse SSE: find first data line containing JSON
        for line in raw.splitlines():
            if line.startswith("data:") and "{" in line:
                msg = json.loads(line[5:].strip())
                if "error" in msg:
                    raise RuntimeError(f"MCP error: {msg['error']}")
                return msg.get("result", {})
        return {}

    def initialize(self) -> None:
        result = self._post("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "ace-studio-client", "version": "1.0"},
        })
        server = result.get("serverInfo", {})
        print(f"[ace_studio] connected to {server.get('name', 'ACE Studio MCP')} "
              f"v{server.get('version', '?')} (session {self._session_id})")

    def call(self, tool: str, args: dict | None = None) -> dict:
        result = self._post("tools/call", {"name": tool, "arguments": args or {}})
        # MCP tools/call wraps results in content[]/structuredContent
        if "structuredContent" in result:
            return result["structuredContent"]
        # Fall back: parse the last text content item that looks like JSON
        for item in reversed(result.get("content", [])):
            if item.get("type") == "text":
                text = item["text"].strip()
                if text.startswith("{") or text.startswith("["):
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        pass
        return result

    def list_tools(self) -> list[dict]:
        result = self._post("tools/list")
        return result.get("tools", [])


# ---------------------------------------------------------------------------
# High-level helpers
# ---------------------------------------------------------------------------

def list_voices(session: MCPSession, voice_type: str = "voice",
                tags: list[str] | None = None,
                keyword: str | None = None,
                language: str | None = None) -> list[dict]:
    args: dict = {"type": voice_type}
    if tags:
        args["tags"] = tags
    if keyword:
        args["keyword"] = keyword
    if language:
        args["language"] = language
    result = session.call("get_available_sound_source_list", args)
    return result.get("soundSources", [])


def set_bpm(session: MCPSession, bpm: float) -> None:
    session.call("set_tempo_automation", {"points": [{"pos": 0, "value": float(bpm)}]})
    print(f"[ace_studio] tempo set to {bpm} BPM")


def get_tracks(session: MCPSession) -> list[dict]:
    result = session.call("get_content_track_basic_info_list")
    return result.get("tracks", [])


def add_sing_track(session: MCPSession, voice_id: int, voice_group: str = "",
                   track_name: str = "Vocals") -> int:
    """
    Creates a new track (by loading a sound source without a prior track —
    ACE Studio auto-creates one), or finds the next available empty track.
    Returns the 0-based track index of the new vocal track.
    """
    tracks_before = get_tracks(session)
    n_before = len(tracks_before)

    # Load voice — ACE Studio creates a new track when no empty slot exists.
    # We target the next track index (tracks are 0-based).
    session.call("load_new_sound_source_on_track", {
        "trackIndex": n_before,  # append after last track
        "soundSourceType": "singer",
        "id": voice_id,
        "group": voice_group,
    })

    tracks_after = get_tracks(session)
    new_idx = len(tracks_after) - 1

    # Rename the track
    session.call("rename_content_track", {"trackIndex": new_idx, "name": track_name})
    print(f"[ace_studio] vocal track '{track_name}' created at index {new_idx}")
    return new_idx


def add_vocal_clip(session: MCPSession, track_idx: int,
                   start_tick: int, dur_ticks: int,
                   clip_name: str = "Verse") -> int:
    """Creates a Sing clip on track_idx. Returns the clip index (always 0 for a new track)."""
    session.call("add_new_clip", {
        "trackIndex": track_idx,
        "pos": start_tick,
        "dur": dur_ticks,
        "type": "sing",
        "name": clip_name,
    })
    clips = session.call("get_content_track_clip_basic_info_list", {"trackIndex": track_idx})
    clip_count = clips.get("clipCount", 1)
    print(f"[ace_studio] clip '{clip_name}' added ({dur_ticks} ticks); "
          f"track now has {clip_count} clip(s)")
    return clip_count - 1


def open_editor(session: MCPSession) -> None:
    result = session.call("ask_editor_to_open")
    already = result.get("wasAlreadyVisible", False)
    if not already:
        print("[ace_studio] pattern editor opened")


def add_notes(session: MCPSession, track_idx: int, clip_idx: int,
              notes: list[dict],
              lyric_sentence: str | None = None,
              language: str = "ENG") -> None:
    """
    Open the editor for the given clip, then add notes.
    notes: list of {pos, dur, pitch[, lyric]} dicts (pos/dur in ticks).
    lyric_sentence: if set, ACE Studio auto-distributes syllables across notes (recommended).
    """
    # Seek to the clip start so the editor opens the right clip
    # First get clip position
    clips = session.call("get_content_track_clip_basic_info_list", {"trackIndex": track_idx})
    clip_info = clips.get("clips", [])[clip_idx] if clips.get("clips") else {}
    clip_begin_tick = clip_info.get("clipBegin", 0)

    session.call("seek_marker_line_position", {"time": 0.0})  # seek to start
    session.call("set_selected_track_list", {"trackIndices": [track_idx]})
    open_editor(session)

    args: dict = {"notes": notes}
    if lyric_sentence:
        args["lyric_sentence"] = lyric_sentence
        args["language"] = language
    elif notes and notes[0].get("lyric"):
        args["language"] = language

    session.call("add_notes_in_editor", args)
    print(f"[ace_studio] added {len(notes)} notes"
          + (f" with lyric: '{lyric_sentence[:50]}...'" if lyric_sentence and len(lyric_sentence) > 50
             else f" with lyric: '{lyric_sentence}'" if lyric_sentence else ""))


def wait_for_synthesis(session: MCPSession, timeout: int = 300, poll_interval: float = 2.0) -> None:
    """Poll synthesis status until idle or timeout."""
    print("[ace_studio] waiting for synthesis...", end="", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = session.call("get_synthesis_status")
        # The tool returns synthesizing: bool or similar
        is_busy = status.get("synthesizing", status.get("isSynthesizing", False))
        if not is_busy:
            print(" done.")
            return
        print(".", end="", flush=True)
        time.sleep(poll_interval)
    print(" timed out.")
    print("[ace_studio] WARNING: synthesis may still be running — check ACE Studio UI")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="ACE Studio MCP vocal synthesis client")
    ap.add_argument("--notes", help="Path to notes JSON file (see module docstring for format)")
    ap.add_argument("--voice-tags", default="",
                    help="Comma-separated voice tags to filter by (e.g. 'Female,Pop')")
    ap.add_argument("--voice-keyword", default="",
                    help="Voice name keyword to search for (e.g. 'Luna')")
    ap.add_argument("--voice-language", default="",
                    help="Voice language filter (e.g. 'English', 'Chinese')")
    ap.add_argument("--track-name", default="Vocals",
                    help="Name for the created vocal track")
    ap.add_argument("--clip-name", default="Verse",
                    help="Name for the created vocal clip")
    ap.add_argument("--list-voices", action="store_true",
                    help="List available voices and exit")
    ap.add_argument("--url", default=DEFAULT_URL,
                    help=f"ACE Studio MCP URL (default: {DEFAULT_URL})")
    ap.add_argument("--synthesis-timeout", type=int, default=300,
                    help="Max seconds to wait for synthesis (default: 300)")
    args = ap.parse_args()

    session = MCPSession(args.url)
    try:
        session.initialize()
    except Exception as e:
        print(f"[ace_studio] ERROR: could not connect to ACE Studio MCP server at {args.url}")
        print(f"  Make sure ACE Studio is running and MCP Server is enabled in Settings.")
        print(f"  Detail: {e}")
        sys.exit(1)

    # -- list voices mode --
    if args.list_voices:
        tags = [t.strip() for t in args.voice_tags.split(",") if t.strip()]
        voices = list_voices(
            session,
            tags=tags or None,
            keyword=args.voice_keyword or None,
            language=args.voice_language or None,
        )
        if not voices:
            print("[ace_studio] no voices found matching filters")
        else:
            print(f"[ace_studio] {len(voices)} voice(s) found:")
            for v in voices:
                print(f"  id={v.get('id'):<6} group={v.get('group','')!r:<3}  "
                      f"{v.get('name', v.get('displayName', '?')):<30}  "
                      f"tags={v.get('tags', [])}")
        return

    # -- notes required beyond this point --
    if not args.notes:
        ap.error("--notes is required (or use --list-voices)")

    notes_path = Path(args.notes)
    if not notes_path.exists():
        print(f"[ace_studio] ERROR: notes file not found: {notes_path}", file=sys.stderr)
        sys.exit(1)

    notes_data = json.loads(notes_path.read_text())
    bpm = float(notes_data.get("bpm", 120))
    language = notes_data.get("language", "ENG")
    lyric_sentence = notes_data.get("lyric_sentence")
    notes = notes_data.get("notes", [])

    # Support both flat (single lyric_sentence + notes[]) and
    # segmented (segments[{lyric_sentence, notes}]) formats
    segments = notes_data.get("segments")
    if segments:
        # Multi-segment: flatten all notes to calculate total duration
        all_notes = [n for seg in segments for n in seg.get("notes", [])]
        if not all_notes:
            print("[ace_studio] ERROR: segments contain no notes", file=sys.stderr)
            sys.exit(1)
        total_ticks = max(n["pos"] + n["dur"] for n in all_notes) + PPQ
        print(f"[ace_studio] {len(all_notes)} notes across {len(segments)} segments, "
              f"{bpm} BPM, language={language}, total_ticks={total_ticks}")
    else:
        if not notes:
            print("[ace_studio] ERROR: notes array is empty", file=sys.stderr)
            sys.exit(1)
        total_ticks = max(n["pos"] + n["dur"] for n in notes) + PPQ
        print(f"[ace_studio] {len(notes)} notes, {bpm} BPM, language={language}, "
              f"total_ticks={total_ticks}")

    # 1. Set tempo
    set_bpm(session, bpm)

    # 2. Find a voice
    tags = [t.strip() for t in args.voice_tags.split(",") if t.strip()]
    voices = list_voices(
        session,
        tags=tags or None,
        keyword=args.voice_keyword or None,
        language=args.voice_language or None,
    )
    if not voices:
        print("[ace_studio] ERROR: no voices found matching filters. "
              "Run with --list-voices to browse available voices.", file=sys.stderr)
        sys.exit(1)

    voice = voices[0]
    voice_name = voice.get("name", voice.get("displayName", "unknown"))
    voice_id = voice.get("id")
    voice_group = voice.get("group", "")
    print(f"[ace_studio] selected voice: {voice_name} (id={voice_id}, group={voice_group!r})")

    # 3. Create vocal track with the voice
    track_idx = add_sing_track(session, voice_id, voice_group, args.track_name)

    # 4. Add clip
    clip_idx = add_vocal_clip(session, track_idx, 0, total_ticks, args.clip_name)

    # 5. Add notes (flat or segmented)
    if segments:
        for i, seg in enumerate(segments):
            add_notes(session, track_idx, clip_idx,
                      seg["notes"],
                      lyric_sentence=seg.get("lyric_sentence"),
                      language=language)
        print(f"[ace_studio] added {len(segments)} segments")
    else:
        add_notes(session, track_idx, clip_idx, notes, lyric_sentence, language)

    # 6. Wait for synthesis
    wait_for_synthesis(session, timeout=args.synthesis_timeout)

    print()
    print("=" * 60)
    print(f"[ace_studio] Vocal track ready: '{args.track_name}'")
    print()
    print("  Next step: export the vocal track from ACE Studio.")
    print("  In ACE Studio: File > Export Audio  (or Ctrl+Shift+E)")
    print(f"  Recommended output: songs/<slug>/iterations/NN/vocals.wav")
    print("=" * 60)


if __name__ == "__main__":
    main()
