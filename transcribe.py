"""Transcribe one voice file to text (JSON on stdout).

Usage: transcribe.py <path-to-silk-or-audio>
- SILK input (WeChat voice, incl. Tencent 0x02-prefixed) is decoded to WAV via
  pilk first; other audio is passed to faster-whisper directly.
- Model: faster-whisper (default 'small', int8 CPU), cached under ./models.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
os.environ.setdefault("HF_HOME", str(HERE / "models"))

import pilk
from faster_whisper import WhisperModel

MODEL_NAME = os.environ.get("WHISPER_MODEL", "small")
_model: WhisperModel | None = None


def get_model() -> WhisperModel:
    global _model
    if _model is None:
        _model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
    return _model


def is_silk(path: Path) -> bool:
    try:
        head = path.read_bytes()[:10]
    except Exception:
        return False
    return head.startswith(b"#!SILK") or head.startswith(b"\x02#!SILK")


def main() -> int:
    src = Path(sys.argv[1])
    audio_path = src
    tmp_wav = None
    if is_silk(src):
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        tmp_wav = tmp.name
        pilk.silk_to_wav(str(src), tmp_wav, rate=24000)
        audio_path = Path(tmp_wav)
    try:
        model = get_model()
        segments, info = model.transcribe(
            str(audio_path), language="zh", beam_size=5, vad_filter=True
        )
        text = "".join(seg.text for seg in segments).strip()
        print(json.dumps(
            {"text": text, "language": info.language, "duration": round(info.duration, 2)},
            ensure_ascii=False,
        ))
        return 0
    finally:
        if tmp_wav:
            try:
                os.unlink(tmp_wav)
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
