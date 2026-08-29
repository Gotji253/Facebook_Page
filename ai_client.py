#!/usr/bin/env python3
"""OpenAI first, then Gemini, then Hugging Face Serverless Inference."""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
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
    "rate limit",
    "429",
)
DEFAULT_HF_IMAGE_MODELS = (
    "stabilityai/sdxl-turbo",
    "black-forest-labs/FLUX.1-schnell",
    "stabilityai/stable-diffusion-xl-base-1.0",
)


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def hf_token() -> str:
    return env("HF_TOKEN") or env("HUGGINGFACE_API_TOKEN") or env("HUGGINGFACE_API_KEY")


def _text(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    body = ""
    try:
        if response is not None:
            body = response.text if hasattr(response, "text") else str(getattr(response, "json", lambda: {})())
    except Exception:
        body = ""
    return f"{exc} {body}".lower()


def is_exhausted(exc: BaseException) -> bool:
    text = _text(exc)
    return any(marker in text for marker in EXHAUSTED)


def openai_exhausted(exc: BaseException) -> bool:
    return is_exhausted(exc)


def _clean_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text).strip()
    if not text:
        raise RuntimeError("AI returned empty JSON")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


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


def _hf_chat(system: str, user: str) -> dict[str, Any]:
    token = hf_token()
    if not token:
        raise RuntimeError("HF_TOKEN missing")
    model = env("HF_CHAT_MODEL", "Qwen/Qwen2.5-7B-Instruct")
    url = "https://router.huggingface.co/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system + " Reply with valid JSON only."},
            {"role": "user", "content": user},
        ],
        "max_tokens": 1200,
        "temperature": 0.4,
    }
    response = requests.post(url, headers={"Authorization": f"Bearer {token}"}, json=payload, timeout=120)
    if not response.ok:
        raise RuntimeError(f"Hugging Face chat error {response.status_code}: {response.text[:400]}")
    content = (((response.json().get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    LOG.info("Hugging Face chat used model=%s", model)
    return _clean_json(content)


def chat_json(system: str, user: str) -> dict[str, Any]:
    errors: list[str] = []
    if env("OPENAI_API_KEY"):
        try:
            data = _openai_chat(system, user)
            LOG.info("LLM provider=openai")
            return data
        except Exception as exc:
            errors.append(f"openai: {exc}")
            LOG.warning("OpenAI chat failed: %s", exc)
    if env("GEMINI_API_KEY"):
        try:
            data = _gemini_chat(system, user)
            LOG.info("LLM provider=gemini")
            return data
        except Exception as exc:
            errors.append(f"gemini: {exc}")
            LOG.warning("Gemini chat failed: %s", exc)
    if hf_token():
        data = _hf_chat(system, user)
        LOG.info("LLM provider=huggingface")
        return data
    raise RuntimeError("No LLM provider available: " + " | ".join(errors))


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


def _looks_like_image(data: bytes) -> bool:
    return data[:8] == b"\x89PNG\r\n\x1a\n" or data[:2] == b"\xff\xd8" or data[:4] == b"RIFF"


def _hf_image_models() -> list[str]:
    custom = env("HF_IMAGE_MODEL")
    if custom:
        return [custom]
    return list(DEFAULT_HF_IMAGE_MODELS)


def _hf_image(prompt: str) -> bytes:
    token = hf_token()
    if not token:
        raise RuntimeError("HF_TOKEN missing")
    headers = {"Authorization": f"Bearer {token}", "x-wait-for-model": "true"}
    payload = {
        "inputs": prompt[:900],
        "parameters": {
            "width": 768,
            "height": 1344,
            "num_inference_steps": 6,
            "guidance_scale": 4.5,
            "negative_prompt": "text, watermark, logo, blurry, photo, low quality",
        },
    }
    errors: list[str] = []
    for model in _hf_image_models():
        urls = [
            f"https://router.huggingface.co/hf-inference/models/{model}",
            f"https://api-inference.huggingface.co/models/{model}",
        ]
        for url in urls:
            for attempt in range(3):
                response = requests.post(url, headers=headers, json=payload, timeout=180)
                if response.status_code == 503:
                    wait = min(20, 4 * (attempt + 1))
                    LOG.warning("Hugging Face model %s loading; wait %ss", model, wait)
                    time.sleep(wait)
                    continue
                if not response.ok:
                    errors.append(f"{model} {response.status_code}: {response.text[:160]}")
                    break
                data = response.content
                if data and _looks_like_image(data):
                    LOG.info("Hugging Face image used model=%s", model)
                    return data
                errors.append(f"{model} returned non-image payload")
                break
    raise RuntimeError("Hugging Face image error: " + " | ".join(errors[:6]))


def generate_image_bytes(prompt: str, size: str = "1024x1536") -> bytes:
    errors: list[str] = []
    if env("OPENAI_API_KEY"):
        try:
            data = _openai_image(prompt, size)
            LOG.info("Image provider=openai")
            return data
        except Exception as exc:
            errors.append(f"openai: {exc}")
            LOG.warning("OpenAI image failed: %s", exc)
    if env("GEMINI_API_KEY"):
        try:
            data = _gemini_image(prompt)
            LOG.info("Image provider=gemini")
            return data
        except Exception as exc:
            errors.append(f"gemini: {exc}")
            LOG.warning("Gemini image failed: %s", exc)
    if hf_token():
        data = _hf_image(prompt)
        LOG.info("Image provider=huggingface")
        return data
    raise RuntimeError("No image provider available: " + " | ".join(errors))
