#!/usr/bin/env python3
"""Transcribe a single audio file via whisper-asr-service and print segments."""
import argparse, json, os, urllib.request
from pathlib import Path

WHISPER_URL = os.environ.get("WHISPER_URL", "http://192.168.1.171:9000/asr")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    audio_data = Path(args.audio).read_bytes()
    boundary = b"----WASRBoundary"
    body  = b"--" + boundary + b"\r\n"
    body += b'Content-Disposition: form-data; name="audio_file"; filename="audio.mp3"\r\n'
    body += b"Content-Type: audio/mpeg\r\n\r\n"
    body += audio_data + b"\r\n"
    body += b"--" + boundary + b"--\r\n"

    req = urllib.request.Request(
        f"{WHISPER_URL}?task=transcribe&language=en&output=json&word_timestamps=false&vad_filter=false",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary.decode()}"},
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=300)
    data = json.loads(resp.read())
    Path(args.out).write_text(json.dumps(data, indent=2), encoding="utf-8")

    for seg in data.get("segments", []):
        print(f"[{seg['start']:.1f}s]  {seg['text'].strip()}")

if __name__ == "__main__":
    main()
