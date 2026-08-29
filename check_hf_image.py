#!/usr/bin/env python3
from pathlib import Path

from ai_client import generate_image_bytes


def main() -> int:
    prompt = (
        "editorial football caricature, expressive cartoon footballer celebrating, "
        "bright comic colors, vertical 9:16, no text, no logos, no watermark"
    )
    data = generate_image_bytes(prompt)
    out = Path("hf_test.png")
    out.write_bytes(data)
    print(f"Hugging Face image OK bytes={len(data)} file={out}")
    if data[:8] != b"\x89PNG\r\n\x1a\n" and data[:2] != b"\xff\xd8":
        raise RuntimeError("Generated file is not an image")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
