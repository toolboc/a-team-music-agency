#!/usr/bin/env python3
"""
prepare_youtube_release.py - Build YouTube-ready release assets with ffmpeg.

Modes:
- album: one full-album video from a folder of tracks + artwork
- playlist: one video per track from the same artwork
- both: generate both album and playlist outputs

Examples:
    python tools/prepare_youtube_release.py ^
      --audio-dir songs/my-album/final-tracks ^
      --artwork songs/my-album/cover.jpg ^
      --out-dir songs/my-album/release ^
      --mode both ^
      --album-title "My Album"

    python tools/prepare_youtube_release.py \
      --audio-dir songs/my-album/final-tracks \
      --artwork songs/my-album/cover.png \
      --out-dir songs/my-album/release \
      --mode album
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"}


@dataclass
class Track:
    index: int
    title: str
    file_path: Path
    duration_sec: float


def run_command(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    # Keep terminal output compact; full stderr/stdout is surfaced on failures.
    return subprocess.run(cmd, text=True, capture_output=True, check=True)


def ensure_ffmpeg_tools() -> None:
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        print(
            "ERROR: Missing required tools: " + ", ".join(missing) + ". Install ffmpeg and ensure it is on PATH.",
            file=sys.stderr,
        )
        sys.exit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare YouTube release assets from tracks + artwork")
    parser.add_argument("--audio-dir", required=True, help="Directory containing ordered track audio files")
    parser.add_argument("--artwork", required=True, help="Artwork image for all rendered videos")
    parser.add_argument("--out-dir", required=True, help="Output directory for release assets")
    parser.add_argument(
        "--mode",
        choices=["album", "playlist", "both"],
        default="both",
        help="Release style to render (default: both)",
    )
    parser.add_argument("--album-title", default="", help="Album title (default: audio dir name)")
    parser.add_argument(
        "--manifest",
        help=(
            "Optional JSON file describing exact track order/titles. "
            "Format: [{\"file\": \"01-intro.wav\", \"title\": \"Intro\"}]"
        ),
    )
    parser.add_argument("--width", type=int, default=1920, help="Output video width (default: 1920)")
    parser.add_argument("--height", type=int, default=1080, help="Output video height (default: 1080)")
    parser.add_argument("--fps", type=int, default=30, help="Output video FPS for still-image videos (default: 30)")
    parser.add_argument("--audio-bitrate", default="192k", help="AAC audio bitrate (default: 192k)")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing outputs")
    return parser.parse_args()


def seconds_to_timestamp(total_seconds: float) -> str:
    seconds = int(total_seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    remaining = seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{remaining:02d}"
    return f"{minutes:02d}:{remaining:02d}"


def safe_slug(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return slug or "track"


def safe_filename_preserve_case(text: str) -> str:
    # Keep user-facing case while removing characters invalid on Windows filesystems.
    cleaned = re.sub(r'[<>:"/\\|?*]+', " ", text).strip().rstrip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "Untitled"


def ffmpeg_escape_drawtext(text: str) -> str:
    # Escape characters that are special in ffmpeg drawtext filter arguments.
    escaped = text.replace("\\", r"\\")
    escaped = escaped.replace(":", r"\:")
    escaped = escaped.replace("'", r"\'")
    escaped = escaped.replace("%", r"\%")
    escaped = escaped.replace(",", r"\,")
    return escaped


def get_drawtext_font_path() -> str | None:
    candidates = [
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def build_video_filter(
    width: int,
    height: int,
    title_text: str,
    font_path: str | None,
    start_sec: float | None = None,
    end_sec: float | None = None,
) -> str:
    base = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    escaped_title = ffmpeg_escape_drawtext(title_text)
    parts = [
        "drawbox=x=0:y=ih-130:w=iw:h=130:color=black@0.45:t=fill",
    ]

    text_part = (
        f"drawtext=text='{escaped_title}':"
        f"x=(w-text_w)/2:y=h-88:fontsize=44:fontcolor=white:borderw=2:bordercolor=black"
    )
    if font_path:
        text_part += f":fontfile='{font_path.replace('\\', '/')}" + "'"
    if start_sec is not None and end_sec is not None:
        text_part += f":enable='between(t,{start_sec:.3f},{end_sec:.3f})'"
    parts.append(text_part)

    return f"{base},{','.join(parts)}"


def track_title_from_file(file_path: Path) -> str:
    stem = file_path.stem.strip()
    dash_match = re.split(r"\s[-–—]\s", stem, maxsplit=1)
    if len(dash_match) == 2:
        # Prefer the human track title after the final separator.
        return dash_match[1].strip() or stem

    raw = re.sub(r"^[0-9]+[-_. ]*", "", stem)
    cleaned = re.sub(r"[_]+", " ", raw).strip()
    return cleaned or file_path.stem


def get_audio_duration_seconds(file_path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(file_path),
    ]
    result = run_command(cmd)
    return float(result.stdout.strip())


def discover_audio_files(audio_dir: Path) -> list[Path]:
    files = [p for p in sorted(audio_dir.iterdir()) if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS]
    if not files:
        print(f"ERROR: No audio files found in {audio_dir}", file=sys.stderr)
        sys.exit(1)
    return files


def load_tracks(audio_dir: Path, manifest_path: Path | None) -> list[Track]:
    tracks: list[Track] = []

    if manifest_path is None:
        audio_files = discover_audio_files(audio_dir)
        for idx, file_path in enumerate(audio_files, start=1):
            file_path = file_path.resolve()
            title = track_title_from_file(file_path)
            duration = get_audio_duration_seconds(file_path)
            tracks.append(Track(index=idx, title=title, file_path=file_path, duration_sec=duration))
        return tracks

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"ERROR: Could not parse manifest {manifest_path}: {exc}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(manifest, list) or not manifest:
        print("ERROR: manifest must be a non-empty JSON list", file=sys.stderr)
        sys.exit(1)

    for idx, entry in enumerate(manifest, start=1):
        if not isinstance(entry, dict) or "file" not in entry:
            print(f"ERROR: manifest entry #{idx} must include 'file'", file=sys.stderr)
            sys.exit(1)
        file_path = (audio_dir / str(entry["file"])).resolve()
        if not file_path.exists():
            print(f"ERROR: manifest file does not exist: {file_path}", file=sys.stderr)
            sys.exit(1)
        title = str(entry.get("title") or track_title_from_file(file_path))
        duration = get_audio_duration_seconds(file_path)
        tracks.append(Track(index=idx, title=title, file_path=file_path, duration_sec=duration))

    return tracks


def write_tracklist(album_title: str, tracks: list[Track], out_path: Path) -> None:
    lines = [f"{album_title} - YouTube Chapters", ""]
    elapsed = 0.0
    for track in tracks:
        lines.append(f"{seconds_to_timestamp(elapsed)} {track.title}")
        elapsed += track.duration_sec
    lines.append("")
    lines.append(f"Total runtime: {seconds_to_timestamp(elapsed)}")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_release_manifest(album_title: str, tracks: list[Track], out_path: Path, mode: str) -> None:
    elapsed = 0.0
    payload: dict[str, Any] = {
        "album_title": album_title,
        "mode": mode,
        "track_count": len(tracks),
        "tracks": [],
    }

    for track in tracks:
        payload["tracks"].append(
            {
                "index": track.index,
                "title": track.title,
                "file": str(track.file_path),
                "duration_sec": round(track.duration_sec, 3),
                "chapter_start": seconds_to_timestamp(elapsed),
            }
        )
        elapsed += track.duration_sec

    payload["total_duration_sec"] = round(elapsed, 3)
    payload["total_duration_hms"] = seconds_to_timestamp(elapsed)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def ffmpeg_still_video_command(
    artwork_path: Path,
    audio_path: Path,
    out_path: Path,
    width: int,
    height: int,
    fps: int,
    audio_bitrate: str,
    overwrite: bool,
    title_text: str,
) -> list[str]:
    gop = str(max(fps, 1))
    font_path = get_drawtext_font_path()
    vf = build_video_filter(width=width, height=height, title_text=title_text, font_path=font_path)
    cmd = [
        "ffmpeg",
        "-y" if overwrite else "-n",
        "-fflags",
        "+genpts",
        "-loop",
        "1",
        "-framerate",
        str(fps),
        "-i",
        str(artwork_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-tune",
        "stillimage",
        "-r",
        str(fps),
        "-g",
        gop,
        "-keyint_min",
        gop,
        "-sc_threshold",
        "0",
        "-vf",
        vf,
        "-c:a",
        "aac",
        "-af",
        "aresample=async=1:first_pts=0",
        "-b:a",
        audio_bitrate,
        "-fps_mode",
        "cfr",
        "-video_track_timescale",
        "90000",
        "-movflags",
        "+faststart",
        "-pix_fmt",
        "yuv420p",
        "-shortest",
        str(out_path),
    ]
    return cmd


def build_album_audio(tracks: list[Track], album_audio_path: Path, overwrite: bool) -> None:
    concat_file = album_audio_path.parent / "album-concat.txt"
    concat_lines = []
    for track in tracks:
        escaped = str(track.file_path).replace("\\", "/").replace("'", r"'\''")
        concat_lines.append(f"file '{escaped}'")
    concat_file.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")

    cmd = [
        "ffmpeg",
        "-y" if overwrite else "-n",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-vn",
        "-c:a",
        "aac",
        "-b:a",
        "256k",
        str(album_audio_path),
    ]
    run_command(cmd)


def build_album_video(
    tracks: list[Track],
    artwork_path: Path,
    album_dir: Path,
    album_title: str,
    width: int,
    height: int,
    fps: int,
    audio_bitrate: str,
    overwrite: bool,
) -> Path:
    album_audio_path = album_dir / "full-album-audio.m4a"
    build_album_audio(tracks, album_audio_path, overwrite=overwrite)

    album_video_name = safe_filename_preserve_case(album_title) + " - Full Album.mp4"
    album_video_path = album_dir / album_video_name

    font_path = get_drawtext_font_path()
    base_filter = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    overlay_parts: list[str] = ["drawbox=x=0:y=ih-130:w=iw:h=130:color=black@0.45:t=fill"]
    elapsed = 0.0
    for track in tracks:
        start = elapsed
        end = elapsed + track.duration_sec
        escaped_title = ffmpeg_escape_drawtext(track.title)
        text_part = (
            f"drawtext=text='{escaped_title}':"
            f"x=(w-text_w)/2:y=h-88:fontsize=44:fontcolor=white:borderw=2:bordercolor=black"
        )
        if font_path:
            text_part += f":fontfile='{font_path.replace('\\', '/')}" + "'"
        text_part += f":enable='between(t,{start:.3f},{end:.3f})'"
        overlay_parts.append(text_part)
        elapsed = end

    vf = base_filter + "," + ",".join(overlay_parts)
    cmd = ffmpeg_still_video_command(
        artwork_path=artwork_path,
        audio_path=album_audio_path,
        out_path=album_video_path,
        width=width,
        height=height,
        fps=fps,
        audio_bitrate=audio_bitrate,
        overwrite=overwrite,
        title_text=album_title,
    )
    # Replace static text filter with timeline-aware per-track titles for full album video.
    vf_index = cmd.index("-vf") + 1
    cmd[vf_index] = vf
    run_command(cmd)
    return album_video_path


def build_playlist_videos(
    tracks: list[Track],
    artwork_path: Path,
    playlist_dir: Path,
    width: int,
    height: int,
    fps: int,
    audio_bitrate: str,
    overwrite: bool,
) -> list[Path]:
    outputs: list[Path] = []
    for track in tracks:
        file_name = safe_filename_preserve_case(track.file_path.stem) + ".mp4"
        out_path = playlist_dir / file_name
        cmd = ffmpeg_still_video_command(
            artwork_path=artwork_path,
            audio_path=track.file_path,
            out_path=out_path,
            width=width,
            height=height,
            fps=fps,
            audio_bitrate=audio_bitrate,
            overwrite=overwrite,
            title_text=track.title,
        )
        run_command(cmd)
        outputs.append(out_path)
    return outputs


def print_summary(
    mode: str,
    album_video_path: Path | None,
    playlist_videos: list[Path],
    tracklist_path: Path,
    manifest_path: Path,
) -> None:
    print("\n[prepare_youtube_release] Done.")
    print(f"- Mode: {mode}")
    if album_video_path:
        print(f"- Album video: {album_video_path}")
    if playlist_videos:
        print(f"- Playlist videos: {len(playlist_videos)} files")
        print(f"  First: {playlist_videos[0]}")
    print(f"- Chapter tracklist: {tracklist_path}")
    print(f"- Release manifest: {manifest_path}")


def main() -> int:
    args = parse_args()
    ensure_ffmpeg_tools()

    audio_dir = Path(args.audio_dir).resolve()
    artwork_path = Path(args.artwork).resolve()
    out_dir = Path(args.out_dir).resolve()

    if not audio_dir.is_dir():
        print(f"ERROR: audio directory not found: {audio_dir}", file=sys.stderr)
        return 1
    if not artwork_path.is_file():
        print(f"ERROR: artwork file not found: {artwork_path}", file=sys.stderr)
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    album_title = args.album_title.strip() or audio_dir.name.replace("-", " ").title()

    manifest_path = Path(args.manifest).resolve() if args.manifest else None
    tracks = load_tracks(audio_dir=audio_dir, manifest_path=manifest_path)

    chapter_path = out_dir / "youtube-chapters.txt"
    manifest_out_path = out_dir / "release-manifest.json"
    write_tracklist(album_title=album_title, tracks=tracks, out_path=chapter_path)
    write_release_manifest(album_title=album_title, tracks=tracks, out_path=manifest_out_path, mode=args.mode)

    album_video_path: Path | None = None
    playlist_outputs: list[Path] = []

    try:
        if args.mode in {"album", "both"}:
            album_dir = out_dir / "album"
            album_dir.mkdir(parents=True, exist_ok=True)
            album_video_path = build_album_video(
                tracks=tracks,
                artwork_path=artwork_path,
                album_dir=album_dir,
                album_title=album_title,
                width=args.width,
                height=args.height,
                fps=args.fps,
                audio_bitrate=args.audio_bitrate,
                overwrite=args.overwrite,
            )

        if args.mode in {"playlist", "both"}:
            playlist_dir = out_dir / "playlist"
            playlist_dir.mkdir(parents=True, exist_ok=True)
            playlist_outputs = build_playlist_videos(
                tracks=tracks,
                artwork_path=artwork_path,
                playlist_dir=playlist_dir,
                width=args.width,
                height=args.height,
                fps=args.fps,
                audio_bitrate=args.audio_bitrate,
                overwrite=args.overwrite,
            )
    except subprocess.CalledProcessError as exc:
        if exc.stdout:
            print(exc.stdout, end="")
        if exc.stderr:
            print(exc.stderr, file=sys.stderr, end="")
        print(f"ERROR: ffmpeg command failed with exit code {exc.returncode}", file=sys.stderr)
        return 1

    print_summary(
        mode=args.mode,
        album_video_path=album_video_path,
        playlist_videos=playlist_outputs,
        tracklist_path=chapter_path,
        manifest_path=manifest_out_path,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
