#!/usr/bin/env python3
"""Small stdin/stdout-safe launcher for the official IndexTTS-2.5 runtime."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
import wave


def _save_pcm_wav(path: str, wav_data, sample_rate: int) -> None:
    """Write IndexTTS PCM-scale output without the torchaudio backend."""
    import torch

    tensor = wav_data.detach().to(device="cpu")
    if tensor.ndim == 2:
        if tensor.shape[0] != 1:
            raise ValueError("IndexTTS output must be mono")
        tensor = tensor[0]
    if tensor.ndim != 1:
        raise ValueError("IndexTTS output shape is invalid")
    tensor = tensor.clamp(-32767.0, 32767.0).to(dtype=torch.int16)
    with wave.open(path, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(tensor.numpy().tobytes())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("synth", choices=("synth",))
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--voice", required=True)
    parser.add_argument("--stdin", action="store_true")
    parser.add_argument("--output", required=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--fp16", action="store_true")
    args = parser.parse_args()
    if not args.stdin:
        parser.error("--stdin is required")
    text = sys.stdin.read()
    if not text.strip():
        raise ValueError("text is empty")

    # Import the model lazily so ordinary DeskHelm startup stays lightweight.
    import indextts.infer_v2_5 as infer_v2_5

    # The bundled runtime's torchaudio/libsndfile writer can fail after
    # inference on this host. Patch only the external wrapper's save boundary.
    infer_v2_5.save_pcm_wav = _save_pcm_wav
    IndexTTS2 = infer_v2_5.IndexTTS2

    language = "ZH" if re.search(r"[\u4e00-\u9fff]", text) else "EN"
    model_dir = Path(args.model_dir)
    tts = IndexTTS2(
        cfg_path=str(model_dir / "config.yaml"),
        model_dir=str(model_dir),
        use_bf16=args.fp16,
        use_cuda_kernel=False,
        use_qwen_emo=False,
    )
    tts.infer(
        spk_audio_prompt=args.voice,
        text=text,
        lang=language,
        output_path=args.output,
        verbose=False,
        text_normalization=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
