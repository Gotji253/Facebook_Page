from __future__ import annotations
import html
import logging
import re
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin
from PIL import Image, ImageOps
import video_draft as vd
from football_poster import http_get, search_rss_image
from video_post_text import LOG, IMAGE_EXT_RE, MAX_PHOTO_BYTES, photo_queries
OG_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image(?::url)?|twitter:image(?::src)?)["\'][^>]+content=["\']([^"\']+)',
    re.I,
)
OG_IMAGE_RE_FLIP = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image(?::url)?|twitter:image(?::src)?)["\']',
    re.I,
)
IMG_TAG_RE = re.compile(
    r'<img[^>]+(?:src|data-src|data-original|data-lazy-src|data-image)=["\']([^"\']+)',
    re.I,
)
SRCSET_RE = re.compile(r'(?:srcset|data-srcset)=["\']([^"\']+)', re.I)
HTTP_IMG_RE = re.compile(r'(https?:)?//[^\s,"\']+\.(?:jpe?g|png|webp)', re.I)
SKIP_IMG_HINTS = (
    "logo", "sprite", "icon", "avatar", "pixel", "1x1", "advert", "adservice",
    "placeholder", "blank.gif", "share-icon", "social", "amp-logo", "badge",
    "favicon", "emoji", "tracking", "spacer",
)


def _abs_image_url(base: str, raw: str) -> str:
    raw = html.unescape(str(raw or "").strip())
    if not raw or raw.startswith("data:"):
        return ""
    if raw.startswith("//"):
        raw = "https:" + raw
    return urljoin(base, raw)


def scrape_article_images(item) -> list[tuple[str, str, str]]:
    page_url = str(getattr(item, "url", "") or "").strip()
    if not page_url.startswith("http"):
        return []
    try:
        page = http_get(page_url).text[:450_000]
    except Exception as exc:
        LOG.warning("Article image scrape failed: %s", exc)
        return []
    candidates: list[str] = []
    for regex in (OG_IMAGE_RE, OG_IMAGE_RE_FLIP):
        candidates.extend(regex.findall(page))
    candidates.extend(IMG_TAG_RE.findall(page))
    for srcset in SRCSET_RE.findall(page):
        parts = HTTP_IMG_RE.findall(srcset)
        if parts:
            last = parts[-1]
            candidates.append(last if last.startswith("http") or last.startswith("//") else parts[-1])
    found: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for raw in candidates:
        url = _abs_image_url(page_url, raw)
        if not url or not _looks_like_photo(url):
            continue
        lowered = url.lower()
        if any(hint in lowered for hint in SKIP_IMG_HINTS):
            continue
        key = _photo_key(url)
        if key in seen:
            continue
        seen.add(key)
        source = f"{getattr(item, 'source', '') or 'article'} article"
        credit = f"ภาพจากข่าว: {page_url}"
        found.append((url, source, credit))
        if len(found) >= 8:
            break
    LOG.info("Article scrape found %d image candidates from %s", len(found), page_url[:80])
    return found


def _photo_key(url: str) -> str:
    path = re.sub(r"[?#].*$", "", str(url or "")).lower().rstrip("/")
    name = path.rsplit("/", 1)[-1]
    name = re.sub(r"[-_/](?:\d{2,4}x\d{2,4}|\d{3,4})", "", name)
    return name or path


def _looks_like_photo(url: str) -> bool:
    lowered = url.lower()
    news_hosts = (
        "pollinations.ai", "openverse.org", "wikimedia.org", "bbci.co.uk",
        "guim.co.uk", "theguardian.com", "espncdn.com", "fourfourtwo.com",
        "goal.com", "skysports.com", "cloudfront.net",
    )
    if any(host in lowered for host in news_hosts):
        if any(bad in lowered for bad in (".pdf", ".svg", ".tif", ".tiff", ".gif", "tiny_town")):
            return False
        return True
    if any(bad in lowered for bad in (".pdf", ".svg", ".tif", ".tiff", ".gif", "tiny_town")):
        return False
    return bool(IMAGE_EXT_RE.search(url))


def _add_photo(found: list, seen: set, url: str, source: str, credit: str) -> None:
    if not url or not _looks_like_photo(url):
        return
    key = _photo_key(url)
    if not key or key in seen:
        return
    seen.add(key)
    found.append({"url": url, "source": source, "credit": credit})


def search_wikipedia_thumbs(query: str):
    response = http_get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "generator": "search",
            "gsrsearch": query,
            "gsrlimit": 8,
            "prop": "pageimages",
            "piprop": "thumbnail",
            "pithumbsize": 1280,
            "format": "json",
        },
    )
    pages = (response.json().get("query") or {}).get("pages") or {}
    tokens = [token.lower() for token in re.findall(r"[A-Za-z0-9]{3,}", query)]
    sport_words = ("football", "soccer", "f.c", "fc ", "club", "stadium", "manager", "player")
    for page in pages.values():
        source = (page.get("thumbnail") or {}).get("source") or ""
        title = str(page.get("title") or "").lower()
        if not source:
            continue
        titled = any(word in title for word in sport_words) or any(token in title for token in tokens)
        if titled:
            yield source, "Wikipedia", page.get("title", query)


def search_openverse(query: str):
    response = http_get(
        "https://api.openverse.org/v1/images/",
        params={"q": f"{query} football", "page_size": 8},
    )
    for row in response.json().get("results") or []:
        url = row.get("url") or ""
        if url:
            yield url, "Openverse", row.get("title") or query


def collect_real_photos(item) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()
    if item.image_url:
        _add_photo(found, seen, item.image_url, item.image_source or "RSS", item.image_credit or item.title)
    try:
        for url, source, credit in scrape_article_images(item):
            _add_photo(found, seen, url, source, credit)
    except Exception as exc:
        LOG.warning("Article photo scrape failed: %s", exc)
    try:
        url, source, credit = search_rss_image(item)
        _add_photo(found, seen, url, source, credit)
    except Exception as exc:
        LOG.warning("RSS photo search failed: %s", exc)
    article_count = len(found)
    LOG.info("News-page photos collected: %s", article_count)
    queries = photo_queries(item)
    LOG.info("Photo queries: %s", queries[:6])
    if len(found) < 2:
        for query in queries:
            if len(found) >= 4:
                break
            try:
                for url, source, credit in search_wikipedia_thumbs(query.strip()[:80]):
                    _add_photo(found, seen, url, source, credit)
            except Exception as exc:
                LOG.warning("Wikipedia thumb search failed (%s): %s", query[:40], exc)
    if len(found) < 2:
        for query in queries[:4] or ("football match",):
            try:
                for url, source, credit in search_openverse(query):
                    _add_photo(found, seen, url, source, credit)
            except Exception as exc:
                LOG.warning("Openverse search failed (%s): %s", query, exc)
    topic = " ".join(queries[:2]) or "football player club"
    encoded = re.sub(r"[^A-Za-z0-9]+", "%20", topic)[:120]
    _add_photo(
        found, seen,
        f"https://image.pollinations.ai/prompt/real%20photo%20{encoded}%20football?width=1080&height=1920&nologo=true",
        "Pollinations", topic,
    )
    _add_photo(
        found, seen,
        f"https://image.pollinations.ai/prompt/real%20photo%20{encoded}%20football%20club?width=1080&height=1920&nologo=true",
        "Pollinations", topic,
    )
    LOG.info("Collected %d real photo candidates", len(found))
    return found


def save_photo(url: str, path: Path) -> bytes:
    response = http_get(url)
    raw = response.content
    if len(raw) > MAX_PHOTO_BYTES:
        raise RuntimeError(f"Photo too large: {len(raw)} bytes")
    image = Image.open(BytesIO(raw)).convert("RGB")
    if image.size[0] < 400 or image.size[1] < 300:
        raise RuntimeError(f"Photo too small: {image.size}")
    image.save(path, format="JPEG", quality=88, optimize=True)
    return path.read_bytes()


def generate_scene_images(item, storyboard, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    photos = collect_real_photos(item)
    results = []
    used_bytes = set()
    used_keys = set()
    last_error = None
    for index in range(vd.SCENE_COUNT):
        image_path = output_dir / f"scene_{index + 1:02d}.jpg"
        saved = False
        for photo in photos:
            url = str(photo.get("url") or "")
            key = _photo_key(url)
            if key in used_keys:
                continue
            try:
                data = save_photo(url, image_path)
                if data in used_bytes:
                    used_keys.add(key)
                    continue
                used_bytes.add(data)
                used_keys.add(key)
                results.append({
                    "scene": index + 1,
                    "path": str(image_path),
                    "prompt": url,
                    "source": photo.get("source", "photo"),
                    "credit": photo.get("credit", ""),
                })
                saved = True
                LOG.info("Scene %s photo from %s", index + 1, photo.get("source"))
                photos = [item for item in photos if item.get("url") != url]
                break
            except Exception as exc:
                last_error = exc
                LOG.warning("Skip photo %s: %s", url[:90], exc)
        if not saved and results:
            alt = ImageOps.mirror(Image.open(results[0]["path"]).convert("RGB"))
            alt.save(image_path, format="JPEG", quality=82, optimize=True)
            results.append({
                "scene": index + 1,
                "path": str(image_path),
                "prompt": results[0]["prompt"],
                "source": "rss-alt",
                "credit": results[0].get("credit", ""),
            })
            saved = True
            LOG.warning("Scene %s used mirrored fallback photo", index + 1)
        if not saved:
            raise RuntimeError(f"Need two different real photos; failed on scene {index + 1}: {last_error}")
    return results
