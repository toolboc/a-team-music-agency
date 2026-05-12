"""ACE-STEP 1.5 Gradio client.

Usage:
    python tools/ace_step_client.py --prompt songs/<slug>/iterations/NN/prompt.json
                                    --out    songs/<slug>/iterations/NN/audio.wav

Environment:
    ACE_STEP_URL   URL of the ACE-STEP Gradio instance, e.g. http://your-host:7860/
                   Can also be passed via --url.

Model selection:
    The model (DiT config) is switched server-side before generation via the
    /update_model_type_settings Gradio endpoint. Set "model" in prompt.json to
    choose, e.g. "acestep-v15-xl-turbo". Defaults to "acestep-v15-xl-turbo".

API: /generation_wrapper  73 positional args (ACE-Step 1.5)
See: .github/skills/ace-step/SKILL.md
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

DEFAULT_URL: str | None = os.environ.get("ACE_STEP_URL")
DEFAULT_MODEL = "acestep-v15-xl-turbo"
API_NAME = "/generation_wrapper"
MODEL_SWITCH_API = "/update_model_type_settings"


def _file_data(path: str | None) -> dict | None:
    """Wrap a local file path in the FileData dict that Gradio 4.x requires.

    Gradio rejects plain strings for Audio/File components; they must arrive as
    {'path': ..., 'meta': {'_type': 'gradio.FileData'}}.
    Returns None when path is falsy so optional file args stay unset.
    """
    if not path:
        return None
    return {"path": str(path), "meta": {"_type": "gradio.FileData"}}


def _build_args(prompt: dict[str, Any]) -> list[Any]:
    """Return the 73-element positional arg list for /generation_wrapper (ACE-Step 1.5).

    The named Gradio endpoint handles the hidden State at position 48
    (is_format_caption_state) and the 4 batch States at the end automatically.
    We only pass the 73 visible params.
    """
    seed_str = str(prompt.get("seed", -1))
    use_random_seed: bool = seed_str in ("-1", "0", "")

    return [
        str(prompt.get("tags", "")),                          # [0]  Music Caption
        str(prompt.get("lyrics", "")),                        # [1]  Lyrics
        float(prompt["tempo_bpm"]) if "tempo_bpm" in prompt else None,  # [2]  BPM
        str(prompt.get("key", "")),                           # [3]  Key
        str(prompt.get("time_signature", "")),                # [4]  Time Signature
        str(prompt.get("vocal_language", "en")),              # [5]  Vocal Language
        int(prompt.get("infer_steps", 20)),                   # [6]  DiT Infer Steps (int!)
        float(prompt.get("guidance_scale", 7.0)),             # [7]  DiT Guidance Scale
        use_random_seed,                                      # [8]  Random Seed (bool)
        seed_str,                                             # [9]  Seed (str)
        _file_data(prompt.get("reference_audio")),            # [10] Reference Audio (style/melody conditioning for text2music)
        float(prompt.get("duration", -1)),                    # [11] Audio Duration
        int(prompt.get("batch_size", 1)),                     # [12] Batch Size (int!)
        _file_data(prompt.get("source_audio")),               # [13] Source Audio (retake/remix)
        None,                                                 # [14] LM Codes Hints
        0.0,                                                  # [15] Repainting Start
        -1.0,                                                 # [16] Repainting End
        "Fill the audio semantic mask based on the given conditions:",  # [17] Instruction
        float(prompt.get("lm_codes_strength", 1.0)),          # [18] LM Codes Strength
        float(prompt.get("cover_strength", 0.0)),              # [19] Cover Strength
        str(prompt.get("task_type", "text2music")),           # [20] task_type ("text2music", "cover", etc.)
        False,                                                # [21] no_fsq
        False,                                                # [22] Use ADG
        0.0,                                                  # [23] CFG Interval Start
        1.0,                                                  # [24] CFG Interval End
        3.0,                                                  # [25] Shift
        str(prompt.get("inference_method", "ode")),           # [26] Inference Method
        str(prompt.get("sampler", "euler")),                  # [27] Sampler Mode
        0.0,                                                  # [28] Velocity Norm Threshold
        0.0,                                                  # [29] Velocity EMA Factor
        True,                                                 # [30] Enable DCW
        "double",                                             # [31] DCW Mode
        0.05,                                                 # [32] DCW Scaler
        0.02,                                                 # [33] DCW High Scaler
        "haar",                                               # [34] DCW Wavelet
        "",                                                   # [35] Custom Timesteps
        "wav",                                                # [36] Audio Format
        "128k",                                               # [37] MP3 Bitrate
        48000,                                                # [38] MP3 Sample Rate (int, not str in 1.5!)
        float(prompt.get("lm_temperature", 0.85)),            # [39] LM Temperature
        False,                                                # [40] Think
        2.0,                                                  # [41] LM CFG Scale
        0,                                                    # [42] LM Top-K (int!)
        0.9,                                                  # [43] LM Top-P
        str(prompt.get("negative_prompt", "NO USER INPUT")),  # [44] LM Negative Prompt
        True,                                                 # [45] CoT Metas
        False,                                                # [46] CaptionRewrite
        True,                                                 # [47] CoT Language Detection
        # [48] is_format_caption_state — hidden State, skipped by named endpoint
        False,                                                # [49→48] Constrained Decoding Debug
        True,                                                 # [50→49] Allow LM Batch
        False,                                                # [51→50] Auto Score
        False,                                                # [52→51] Auto LRC
        0.5,                                                  # [53→52] Quality Score Sensitivity
        8,                                                    # [54→53] LM Batch Chunk Size (int!)
        None,                                                 # [55→54] Track Name
        [],                                                   # [56→55] Track Names / complete_track_classes
        True,                                                 # [57→56] Enable Normalization
        -1.0,                                                 # [58→57] Normalization dB
        float(prompt.get("fade_in", 0.0)),                    # [59→58] Fade In
        float(prompt.get("fade_out", 0.0)),                   # [60→59] Fade Out
        0.0,                                                  # [61→60] Latent Shift
        1.0,                                                  # [62→61] Latent Rescale
        "balanced",                                           # [63→62] Repaint Mode
        0.5,                                                  # [64→63] Repaint Strength
        float(prompt.get("retake_variance", 0.5)),            # [65→64] Retake Variance (0.0=clone source, 1.0=ignore source)
        seed_str,                                             # [66→65] Retake Seed
        False,                                                # [67→66] Flow Edit Morph
        "",                                                   # [68→67] Flow Edit Source Caption
        "",                                                   # [69→68] Flow Edit Source Lyrics
        0.0,                                                  # [70→69] Flow Edit N Min
        1.0,                                                  # [71→70] Flow Edit N Max
        1.0,                                                  # [72→71] Flow Edit N Avg
        False,                                                # [73→72] Autogen Checkbox
    ]


def _switch_model(client: Any, model: str) -> None:
    """Switch the server's loaded DiT model via /update_model_type_settings.

    This is a server-side reload — it affects all concurrent users but is
    necessary because the model is not a per-request parameter in generation_wrapper.
    The call is a no-op if the requested model is already loaded.
    """
    print(f"[ace_step_client] switching model to {model!r} ...", flush=True)
    try:
        client.predict(model, "Custom", api_name=MODEL_SWITCH_API)
        print(f"[ace_step_client] model switch complete", flush=True)
    except Exception as exc:
        print(f"[ace_step_client] WARNING: model switch failed: {exc}", flush=True)


def _call_via_gradio_client(url: str, args: list[Any], timeout: int = 600) -> Any:
    """Call ACE-STEP via gradio_client.predict().

    gradio_client handles the Gradio queue protocol, SSE streaming, and
    file serving automatically. It uses the named api_name endpoint so
    hidden State components are managed by Gradio on the server side.
    """
    try:
        from gradio_client import Client
    except ImportError as exc:
        raise SystemExit("gradio_client not installed. Run: pip install gradio_client") from exc

    client = Client(url, verbose=False)
    return client, client.predict(*args, api_name=API_NAME)

    return result


def _fetch_audio(url: str, result: Any) -> str | None:
    """Extract a local file path from the gradio_client result.

    generation_wrapper returns a tuple of all outputs. gradio_client
    downloads audio files to a local temp dir. We scan the whole result
    for the first string that looks like an audio file path.
    """
    if result is None:
        return None
    if isinstance(result, str):
        if Path(result).exists():
            return result
        return None
    if isinstance(result, dict):
        path = result.get("path") or result.get("name") or result.get("url")
        if path and isinstance(path, str) and Path(path).exists():
            return path
        # Recurse into dict values
        for v in result.values():
            found = _fetch_audio(url, v)
            if found:
                return found
    if isinstance(result, (list, tuple)):
        for item in result:
            found = _fetch_audio(url, item)
            if found:
                return found
    return None


def _download(file_url: str) -> str | None:
    try:
        import httpx
        resp = httpx.get(file_url, timeout=120, follow_redirects=True)
        resp.raise_for_status()
        suffix = Path(file_url.split("?")[0]).suffix or ".wav"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(resp.content)
            return f.name
    except Exception as exc:
        print(f"[ace_step_client] download failed: {exc}", flush=True)
        return None


def render(prompt_path: Path, out_path: Path, url: str) -> dict[str, Any]:
    raw_prompt: dict[str, Any] = json.loads(prompt_path.read_text(encoding="utf-8"))
    prompt = {k: v for k, v in raw_prompt.items() if not k.startswith("_")}

    model = prompt.pop("model", DEFAULT_MODEL)
    args = _build_args(prompt)

    print(f"[ace_step_client] connecting to {url} ...", flush=True)
    started = time.time()

    try:
        from gradio_client import Client
    except ImportError as exc:
        raise SystemExit("gradio_client not installed. Run: pip install gradio_client") from exc

    client = Client(url, verbose=False)
    _switch_model(client, model)

    print(f"[ace_step_client] rendering with {model} ...", flush=True)
    result = client.predict(*args, api_name=API_NAME)

    elapsed = round(time.time() - started, 2)
    print(f"[ace_step_client] render complete in {elapsed}s", flush=True)

    audio_src = _fetch_audio(url, result)
    if not audio_src:
        raise SystemExit(
            f"ACE-STEP returned no usable audio path.\nRaw result: {result!r}"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(audio_src, out_path)

    return {
        "ok": True,
        "url": url,
        "api_name": API_NAME,
        "model": model,
        "audio_out": str(out_path),
        "audio_bytes": out_path.stat().st_size,
        "elapsed_seconds": elapsed,
        "args_summary": {
            "tags": args[0][:120],
            "lyrics_chars": len(args[1]),
            "bpm": args[2],
            "key": args[3],
            "duration": args[11],
            "infer_steps": args[6],
            "guidance_scale": args[7],
            "random_seed": args[8],
            "seed": args[9],
            "batch_size": args[12],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render a song via ACE-STEP 1.5 /generation_wrapper."
    )
    parser.add_argument("--prompt", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--url", default=DEFAULT_URL,
                        help="ACE-STEP Gradio URL (default: $ACE_STEP_URL)")
    args = parser.parse_args()

    if not args.url:
        print(json.dumps({"ok": False, "error":
              "ACE-STEP URL not set. Pass --url or set ACE_STEP_URL."}))
        return 2

    if not args.prompt.exists():
        print(json.dumps({"ok": False, "error":
              f"prompt file not found: {args.prompt}"}))
        return 2

    summary = render(args.prompt, args.out, args.url)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
