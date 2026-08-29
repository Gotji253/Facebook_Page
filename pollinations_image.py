#!/usr/bin/env python3
from __future__ import annotations

import logging
import os
import time
from urllib.parse import quote

import requests

LOG = logging.getLogger("pollinations")


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _looks_like_image(data: bytes) -> bool:
    return bool(data) and (data[:8] == b"\x89PNG\r\n\x1a\n" or data[:2] == b"\xff\xd8" or data[:4] == b"RIFF")


def generate_pollinations_image(prompt: str, width: int = 768, height: int = 1344) -> bytes:
    clean = " ".join((prompt or "editorial football caricature").split())[:700]
    model = env("POLLINATIONS_IMAGE_MODEL", "flux")
    encoded = quote(clean, safe="")
    urls = [
        f"https://image.pollinations.ai/prompt/{encoded}",
        f"https://gen.pollinations.ai/image/{encoded}",
    ]
    params = {
        "width": width,
        "height": height,
        "model": model,
        "nologo": "true",
        "safe": "true",
        "private": "true",
    }
    headers = {"User-Agent": "FacebookPageVideoBot/1.0"}
    key = env("POLLINATIONS_API_KEY") or env("POLLINATIONS_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
        params["key"] = key
    errors: list[str] = []
    for url in urls:
        for attempt in range(3):
            try:
                response = requests.get(url, params=params, headers=headers, timeout=180)
            except Exception as exc:
                errors.append(f"{url}: {exc}")
                time.sleep(2)
                continue
            if response.status_code in {429, 502, 503}:
                wait = 8 * (attempt + 1)
                LOG.warning("Pollinations busy %s; wait %ss", response.status_code, wait)
                time.sleep(wait)
                continue
            if not response.ok:
                errors.append(f"{url} {response.status_code}: {response.text[:160]}")
                break
            if _looks_like_image(response.content):
                LOG.info("Pollinations image OK bytes=%s url=%s", len(response.content), url)
                return response.content
            errors.append(f"{url} returned non-image payload")
            break
    raise RuntimeError("Pollinations image error: " + " | ".join(errors[:6]))
