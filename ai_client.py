#!/usr/bin/env python3
"""OpenAI first, then Gemini, then Hugging Face, then Pollinations."""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from typing import Any

import requests
from openai import OpenAI

from pollinations_image import generate_pollinations_image

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
    "402",
    "payment required",
    "depleted your monthly included credits",
)
HF_CHAT_MODELS = (
    "HuggingFaceTB/SmolLM3-3B",
    "Qwen/Qwen3-4B",
    "Qwen/Qwen2.5-7B-Instruct",
    "meta-llama/Llama-3.2-3B-Instruct",
    "openai/gpt-oss-20b",
)
HF_IMAGE_MODELS = (
    "black-forest-labs/FLUX.1-schnell",
    "black-forest-labs/FLUX.2-klein-4B",
    "stabilityai/sdxl-turbo",
    "ByteDance/SDXL-Lightning",
)


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def hf_token() -> str:
    return env("HF_TOKEN") or env("HUGGINGFACE_API_TOKEN") or env("HUGGINGFACE_API_KEY")


def _hf_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {hf_token()}", "Content-Type": "application/json"}


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
    return any(marker in _text(exc) for marker in EXHAUSTED)


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
    model = env("GEMINI_MODEL", "gemini-3.6-flash")
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


def _hf_router_models() -> list[str]:
    try:
        response = requests.get("https://router.huggingface.co/v1/models", headers=_hf_headers(), timeout=30)
        if not response.ok:
            return []
        items = response.json().get("data") or response.json()
        if isinstance(items, list):
            return [str(item.get("id") or item) for item in items if item]
    except Exception as exc:
        LOG.warning("Cannot list Hugging Face router models: %s", exc)
    return []


def _hf_chat_models() -> list[str]:
    custom = env("HF_CHAT_MODEL")
    available = set(_hf_router_models())
    ordered = ([custom] if custom else []) + list(HF_CHAT_MODELS)
    if available:
        matched = [model for model in ordered if model in available]
        extras = [model for model in available if any(tag in model.lower() for tag in ("instruct", "chat", "smol", "qwen", "llama", "gpt-oss"))]
        return list(dict.fromkeys(matched + extras + ordered))
    return list(dict.fromkeys(ordered))


def _hf_chat(system: str, user: str) -> dict[str, Any]:
    if not hf_token():
        raise RuntimeError("HF_TOKEN missing")
    errors: list[str] = []
    for model in _hf_chat_models()[:8]:
        try:
            response = requests.post(
                "https://router.huggingface.co/v1/chat/completions",
                headers=_hf_headers(),
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system + " Reply with valid JSON only."},
                        {"role": "user", "content": user},
                    ],
                    "max_tokens": 1200,
                    "temperature": 0.4,
                },
                timeout=120,
            )
            if not response.ok:
                errors.append(f"{model} {response.status_code}: {response.text[:160]}")
                continue
            content = (((response.json().get("choices") or [{}])[0].get("message") or {}).get("content") or "")
            LOG.info("Hugging Face chat used model=%s", model)
            return _clean_json(content)
        except Exception as exc:
            errors.append(f"{model}: {exc}")
    raise RuntimeError("Hugging Face chat error: " + " | ".join(errors[:5]))


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
    return bool(data) and (data[:8] == b"\x89PNG\r\n\x1a\n" or data[:2] == b"\xff\xd8" or data[:4] == b"RIFF")


def _bytes_from_image_payload(payload: Any) -> bytes | None:
    if isinstance(payload, bytes) and _looks_like_image(payload):
        return payload
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") or payload.get("images") or []
    first = data[0] if isinstance(data, list) and data else payload
    if isinstance(first, dict):
        b64 = first.get("b64_json") or first.get("b64") or ""
        if b64:
            return base64.b64decode(b64)
        url = first.get("url") or ""
        if isinstance(url, str) and url.startswith("http"):
            return requests.get(url, timeout=90).content
    return None


def _hf_image_models() -> list[str]:
    custom = env("HF_IMAGE_MODEL")
    available = set(_hf_router_models())
    ordered = ([custom] if custom else []) + list(HF_IMAGE_MODELS)
    if available:
        matched = [model for model in ordered if model in available]
        extras = [model for model in available if any(tag in model.lower() for tag in ("flux", "sdxl", "image", "schnell"))]
        return list(dict.fromkeys(matched + extras + ordered))
    return list(dict.fromkeys(ordered))


def _hf_image(prompt: str) -> bytes:
    if not hf_token():
        raise RuntimeError("HF_TOKEN missing")
    errors: list[str] = []
    for model in _hf_image_models()[:6]:
        attempts = [
            (
                "https://router.huggingface.co/v1/images/generations",
                {"model": model, "prompt": prompt[:900], "n": 1, "size": "768x1344"},
            ),
            (
                f"https://router.huggingface.co/hf-inference/models/{model}",
                {"inputs": prompt[:900], "parameters": {"width": 768, "height": 1344, "num_inference_steps": 4}},
            ),
        ]
        for url, payload in attempts:
            try:
                response = requests.post(url, headers=_hf_headers(), json=payload, timeout=180)
            except Exception as exc:
                errors.append(f"{model} {url}: {exc}")
                continue
            if not response.ok:
                errors.append(f"{model} {response.status_code}: {response.text[:160]}")
                continue
            content_type = (response.headers.get("content-type") or "").lower()
            if "image/" in content_type and _looks_like_image(response.content):
                LOG.info("Hugging Face image used model=%s endpoint=%s", model, url)
                return response.content
            try:
                parsed = response.json()
            except Exception:
                parsed = None
            data = _bytes_from_image_payload(parsed) if parsed is not None else None
            if data and _looks_like_image(data):
                LOG.info("Hugging Face image used model=%s endpoint=%s", model, url)
                return data
            errors.append(f"{model} returned non-image payload from {url}")
    raise RuntimeError("Hugging Face image error: " + " | ".join(errors[:8]))


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
        try:
            data = _hf_image(prompt)
            LOG.info("Image provider=huggingface")
            return data
        except Exception as exc:
            errors.append(f"huggingface: {exc}")
            LOG.warning("Hugging Face image failed; switching to Pollinations: %s", exc)
    try:
        data = generate_pollinations_image(prompt)
        LOG.info("Image provider=pollinations")
        return data
    except Exception as exc:
        errors.append(f"pollinations: {exc}")
        LOG.warning("Pollinations image failed: %s", exc)
    raise RuntimeError("No image provider available: " + " | ".join(errors))
