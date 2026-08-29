#!/usr/bin/env python3
import os
import sys

import requests


def main() -> int:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        print("GEMINI_API_KEY is missing or empty")
        return 1
    response = requests.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        params={"key": key},
        timeout=30,
    )
    print(f"Gemini listModels HTTP {response.status_code}")
    if not response.ok:
        print(response.text[:400])
        return 1
    models = [item.get("name", "") for item in response.json().get("models", [])]
    print(f"Gemini model count: {len(models)}")
    print("GEMINI_API_KEY works")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
