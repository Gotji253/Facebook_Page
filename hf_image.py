#!/usr/bin/env python3
from __future__ import annotations

import logging
import os
from io import BytesIO

from huggingface_hub import InferenceClient
from PIL import Image

LOG = logging.getLogger("hf_image")
MODELS = (
    "Tongyi-MAI/Z-Image-Turbo",
    "krea/Krea-2-Turbo",
    "black-forest-labs/FLUX.1-schnell",
    "black-forest-labs/FLUX.1-dev",
    "stabilityai/stable-diffusion-xl-base-1.0",
)
PROVIDERS = ("fal-ai", "nscale", "replicate")
CREDIT_MARKERS = ("402", "payment required", "depleted your monthly included credits", "no credits")


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def hf_token() -> str:
    return env("HF_TOKEN") or env("HUGGINGFACE_API_TOKEN") or env("HUGGINGFACE_API_KEY")


def _to_png_bytes(image) -> bytes:
    if isinstance(image, bytes):
        return image
    if isinstance(image, Image.Image):
        buf = BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()
    raise RuntimeError(f"Unexpected image type: {type(image)}")


def _credits_gone(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in CREDIT_MARKERS)


def generate_hf_image(prompt: str) -> bytes:
    token = hf_token()
    if not token:
        raise RuntimeError("HF_TOKEN missing")
    models = [env("HF_IMAGE_MODEL")] if env("HF_IMAGE_MODEL") else list(MODELS)
    models = [model for model in models if model] + [model for model in MODELS if model not in models]
    errors: list[str] = []
    for provider in PROVIDERS:
        client = InferenceClient(provider=provider, api_key=token)
        for model in models[:4]:
            try:
                LOG.info("Trying Hugging Face provider=%s model=%s", provider, model)
                image = client.text_to_image(prompt[:900], model=model)
                data = _to_png_bytes(image)
                LOG.info("Hugging Face image OK provider=%s model=%s bytes=%s", provider, model, len(data))
                return data
            except Exception as exc:
                errors.append(f"{provider}/{model}: {exc}")
                LOG.warning("Hugging Face image failed %s/%s: %s", provider, model, exc)
                if _credits_gone(exc):
                    raise RuntimeError(f"Hugging Face credits exhausted: {exc}") from exc
    raise RuntimeError("Hugging Face image error: " + " | ".join(errors[:8]))
