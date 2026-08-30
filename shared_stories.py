#!/usr/bin/env python3
"""Keep poster and video from posting the same football story."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

LOG = logging.getLogger("shared_stories")
POSTER_STATE = Path("state.json")
VIDEO_STATE = Path("video_draft_state.json")
_pending_titles: list[str] = []
_pending_ids: list[str] = []


def normalize_title(title: str) -> str:
    text = re.sub(r"\s+", " ", str(title or "")).strip().lower()
    text = re.sub(r"[^a-z0-9\u0e00-\u0e7f ]+", "", text)
    return re.sub(r"\s+", " ", text).strip()[:96]


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        LOG.warning("Cannot read %s: %s", path, exc)
        return {}


def used_ids() -> set[str]:
    ids: set[str] = set(_pending_ids)
    for path in (POSTER_STATE, VIDEO_STATE):
        data = _read(path)
        ids.update(str(x) for x in data.get("posted_ids", []) if x)
        ids.update(str(x) for x in data.get("drafted_ids", []) if x)
    return ids


def used_titles() -> set[str]:
    titles: set[str] = set(_pending_titles)
    for path in (POSTER_STATE, VIDEO_STATE):
        data = _read(path)
        titles.update(normalize_title(x) for x in data.get("used_titles", []) if x)
    return {title for title in titles if title}


def is_used(item) -> bool:
    ident = str(getattr(item, "id", "") or "")
    title = normalize_title(getattr(item, "title", ""))
    if ident and ident in used_ids():
        return True
    if title and title in used_titles():
        return True
    if title:
        tokens = {tok for tok in title.split() if len(tok) >= 4}
        for seen in used_titles():
            seen_tokens = {tok for tok in seen.split() if len(tok) >= 4}
            if tokens and seen_tokens and len(tokens & seen_tokens) >= 3:
                return True
    return False


def mark(item) -> None:
    ident = str(getattr(item, "id", "") or "")
    title = normalize_title(getattr(item, "title", ""))
    if ident:
        _pending_ids.append(ident)
    if title:
        _pending_titles.append(title)


def merge_into(state: dict) -> dict:
    titles = [normalize_title(x) for x in state.get("used_titles", []) if x]
    titles.extend(_pending_titles)
    state["used_titles"] = list(dict.fromkeys(title for title in titles if title))[-500:]
    return state
