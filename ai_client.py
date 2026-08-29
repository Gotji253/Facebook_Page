#!/usr/bin/env python3
"""OpenAI-first LLM/image client with automatic Gemini fallback."""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from typing import Any

import requests
from openai import OpenAI

LOG = logging.getLogger("ai_client")
EXHAUSTED = (
    "insufficient_quota",
    "credit_balance_exhausted",
    "you have no credits remaining",
    "billing_hard_limit_reached",
    "quota exceeded",
    "resource_exhausted",
)


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _text(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    body = ""
    try:
        if response is not None:
            body = response.text if hasattr(response, "text") else str(getattr(response, "json", lambda: {})())
    except Exception:
        body = ""
    return f"{exc} {body}".lower()


def openai_exhausted(exc: BaseException) -> bool:
    text = _text(exc)
    return any(marker in text for marker in EXHAUSTED)


def _clean_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text).strip()
    if not text:
        raise RuntimeError("AI returned empty JSON")
    return json.loads(text)


def _openai_chat(system: str, user: str) -> dict[str, Any]:
    if not env("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY missing")
    client = OpenAI()
    request_args = {
        "model": env("OPENAI_MODEL", "gpt-5-mini"),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
        "max_completion_tokens": 4000,
    }
    try:
        response = client.chat.completions.create(**request_args, reasoning_effort="minimal")
    except Exception as exc:
        if openai_exhausted(exc):
            raise
        LOG.warning("OpenAI minimal reasoning failed; retrying low: %s", exc)
        response = client.chat.completions.create(**request_args, reasoning_effort="low")
    content = ((response.choices or [None])[0].message.content if response.choices else "") or ""
    return _clean_json(content)


def _gemini_chat(system: str, user: str) -> dict[str, Any]:
    key = env("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY missing")
    model = env("GEMINI_MODEL", "gemini-2.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4},
    }
    response = requests.post(url, params={"key": key}, json=payload, timeout=90)
    if not response.ok:
        raise RuntimeError(f"Gemini chat error {response.status_code}: {response.text[:400]}")
    data = response.json()
    parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    text = "".join(str(part.get("text", "")) for part in parts)
    LOG.info("Gemini chat used model=%s", model)
    return _clean_json(text)


def chat_json(system: str, user: str) -> dict[str, Any]:
    if env("OPENAI_API_KEY"):
        try:
            data = _openai_chat(system, user)
            LOG.info("LLM provider=openai")
            return data
        except Exception as exc:
            if env("GEMINI_API_KEY") and (openai_exhausted(exc) or "OPENAI_API_KEY missing" in str(exc)):
                LOG.warning("OpenAI unavailable (%s); falling back to Gemini", exc)
            else:
                raise
    data = _gemini_chat(system, user)
    LOG.info("LLM provider=gemini")
    return data


def _openai_image(prompt: str, size: str) -> bytes:
    if not env("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY missing")
    response = OpenAI().images.generate(
        model=env("OPENAI_IMAGE_MODEL", "gpt-image-1"),
        prompt=prompt,
        size=size,
    )
    payload = response.data[0]
    if getattr(payload, "b64_json", None):
        return base64.b64decode(payload.b64_json)
    if getattr(payload, "url", None):
        return requests.get(payload.url, timeout=90).content
    raise RuntimeError("OpenAI image response had no data")


def _gemini_image(prompt: str) -> bytes:
    key = env("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY missing")
    model = env("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {"aspectRatio": "2:3"},
        },
    }
    response = requests.post(url, params={"key": key}, json=payload, timeout=180)
    if not response.ok:
        raise RuntimeError(f"Gemini image error {response.status_code}: {response.text[:400]}")
    parts = (((response.json().get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    for part in parts:
        inline = part.get("inlineData") or part.get("inline_data") or {}
        data = inline.get("data")
        if data:
            LOG.info("Gemini image used model=%s", model)
            return base64.b64decode(data)
    raise RuntimeError("Gemini image response had no inline image data")


def generate_image_bytes(prompt: str, size: str = "1024x1536") -> bytes:
    if env("OPENAI_API_KEY"):
        try:
            data = _openai_image(prompt, size)
            LOG.info("Image provider=openai")
            return data
        except Exception as exc:
            if env("GEMINI_API_KEY") and (openai_exhausted(exc) or "OPENAI_API_KEY missing" in str(exc)):
                LOG.warning("OpenAI image unavailable (%s); falling back to Gemini", exc)
            else:
                raise
    data = _gemini_image(prompt)
    LOG.info("Image provider=gemini")
    return data
