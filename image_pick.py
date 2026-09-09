#!/usr/bin/env python3
"""Pick sharper, story-matching photos for posters and clips."""
from __future__ import annotations
import re
from urllib.parse import urlparse

BAD_HINTS = (
    "logo", "sprite", "icon", "avatar", "pixel", "1x1", "advert", "adservice",
    "placeholder", "blank.gif", "share-icon", "social", "amp-logo", "badge",
    "favicon", "emoji", "tracking", "spacer", "collage", "montage", "banner",
    "watermark", "crest", "wordmark", "infographic", "graphic", "cartoon",
    "illustration", "chart", "masthead", "newsletter", "app-icon",
)
NAME_RE = re.compile(r"\b([A-Za-z][A-Za-z]{2,})\b")
SKIP_TOKENS = {
    "the", "and", "for", "with", "from", "this", "that", "after", "before",
    "against", "premier", "league", "news", "sport", "football", "soccer",
    "live", "watch", "how", "why", "what", "when", "club", "team", "season",
}


def photo_key(url: str) -> str:
    path = re.sub(r"[?#].*$", "", str(url or "")).lower().rstrip("/")
    name = path.rsplit("/", 1)[-1]
    name = re.sub(r"[-_/](?:\d{2,4}x\d{2,4}|\d{3,4})", "", name)
    return name or path


def story_tokens(item) -> set[str]:
    blob = f"{getattr(item, 'title', '')} {getattr(item, 'summary', '')}"
    tokens = {tok.lower() for tok in NAME_RE.findall(blob)}
    return {tok for tok in tokens if tok not in SKIP_TOKENS and len(tok) >= 3}


def looks_bad(url: str, title: str = "") -> bool:
    hay = f"{url} {title}".lower()
    return any(hint in hay for hint in BAD_HINTS)


def aspect_ok(width: int, height: int, *, poster: bool = False) -> bool:
    if width < 1 or height < 1:
        return False
    ratio = width / height
    if poster:
        return 1.15 <= ratio <= 2.6
    return 0.45 <= ratio <= 2.6


def score_photo(url: str, width: int, height: int, title: str = "", item=None, *, poster: bool = False) -> int:
    if not url or looks_bad(url, title):
        return 0
    min_w, min_h = (900, 500) if poster else (640, 400)
    if width < min_w or height < min_h:
        return 0
    if not aspect_ok(width, height, poster=poster):
        return 0
    score = 20 + min(40, width // 80) + min(20, height // 80)
    pixels = width * height
    if pixels >= 1_200_000:
        score += 15
    elif pixels >= 700_000:
        score += 8
    host = urlparse(url).netloc.lower()
    if any(part in host for part in ("bbci.co.uk", "guim.co.uk", "espncdn.com", "skysports.com", "goal.com")):
        score += 12
    tokens = story_tokens(item) if item is not None else set()
    hay = f"{url} {title}".lower()
    hits = sum(1 for tok in tokens if tok in hay)
    score += min(25, hits * 8)
    if hits == 0 and tokens and "wiki" in host:
        score -= 10
    return max(0, score)


def best_candidate(rows: list[dict], *, poster: bool = False) -> dict | None:
    ranked = []
    for row in rows:
        value = score_photo(
            str(row.get("url") or ""),
            int(row.get("width") or 0),
            int(row.get("height") or 0),
            str(row.get("title") or ""),
            row.get("item"),
            poster=poster,
        )
        if value > 0:
            ranked.append((value, row))
    if not ranked:
        return None
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return ranked[0][1]
