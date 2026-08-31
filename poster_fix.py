#!/usr/bin/env python3
"""Keep scheduled poster runs alive when the top story has no usable photo."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from PIL import Image

import football_poster as fp

LOG = logging.getLogger("football_poster")


def image_is_acceptable(url: str, width: int, height: int, title: str = "", min_width: int | None = None, min_height: int | None = None) -> bool:
    min_width = int(min_width if min_width is not None else fp.env("IMAGE_MIN_WIDTH", "900"))
    min_height = int(min_height if min_height is not None else fp.env("IMAGE_MIN_HEIGHT", "500"))
    if not url or width < min_width or height < min_height:
        return False
    haystack = f"{url} {title}".lower()
    return not any(term in haystack for term in fp.IMAGE_BAD_TERMS)


def image_url_variants(url: str) -> list[str]:
    if not url:
        return []
    variants: list[str] = []
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    upgraded_query = dict(query)
    if any(key in upgraded_query for key in ("width", "w", "imwidth")):
        for key in ("width", "w", "imwidth"):
            if key in upgraded_query:
                upgraded_query[key] = ["1200"]
        variants.append(urlunparse(parsed._replace(query=urlencode(upgraded_query, doseq=True))))
    if parsed.query and ("i.guim.co.uk" in parsed.netloc or "ichef.bbci.co.uk" in parsed.netloc):
        variants.append(urlunparse(parsed._replace(query="")))
    variants.append(url)
    patterns = (
        (r"/140/", "/1200/"),
        (r"/240/", "/976/"),
        (r"/320/", "/976/"),
        (r"/400/", "/976/"),
        (r"/480/", "/976/"),
        (r"/640/", "/976/"),
        (r"/768/", "/976/"),
        (r"/1024/", "/1600/"),
    )
    expanded: list[str] = []
    for candidate in variants:
        expanded.append(candidate)
        for old, new in patterns:
            bigger = re.sub(old, new, candidate, count=1)
            if bigger not in expanded:
                expanded.append(bigger)
    return list(dict.fromkeys(expanded))


def search_rss_image(item) -> tuple[str, str, str]:
    for candidate in image_url_variants(item.image_url):
        try:
            response = fp.http_get(candidate, stream=True)
            with Image.open(response.raw) as image:
                width, height = image.size
            if not image_is_acceptable(candidate, width, height, item.title, min_width=800, min_height=450):
                LOG.warning("RSS image rejected: size=%sx%s or disallowed URL: %s", width, height, candidate)
                continue
            LOG.info("RSS image selected: %sx%s from %s", width, height, candidate)
            return candidate, f"{item.source} RSS", f"ภาพจาก {item.source}: {item.url}"
        except Exception as exc:
            LOG.warning("RSS image variant unavailable (%s): %s", candidate, exc)
    return "", "", ""


def search_article_image(item) -> tuple[str, str, str]:
    if not str(getattr(item, "url", "")).startswith("http"):
        return "", "", ""
    try:
        html_text = fp.http_get(item.url).text[:450000]
    except Exception as exc:
        LOG.warning("Article scrape failed (%s): %s", item.url, exc)
        return "", "", ""
    found: list[str] = []
    for pattern in (
        r'<meta[^>]+property=["\']og:image(?::url)?["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::url)?["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
        r'<img[^>]+src=["\']([^"\']+)',
    ):
        for match in re.finditer(pattern, html_text, re.I):
            found.append(urljoin(item.url, fp.html.unescape(match.group(1).strip())))
    seen: set[str] = set()
    for raw in found:
        if not raw or raw in seen:
            continue
        seen.add(raw)
        if not re.search(r"\.(jpe?g|png|webp)(?:$|\?)", raw, re.I) and "image" not in raw.lower():
            continue
        for candidate in image_url_variants(raw):
            try:
                response = fp.http_get(candidate, stream=True)
                with Image.open(response.raw) as image:
                    width, height = image.size
                if not image_is_acceptable(candidate, width, height, item.title, min_width=800, min_height=450):
                    continue
                LOG.info("Article image selected: %sx%s from %s", width, height, candidate)
                return candidate, f"{item.source} article", f"ภาพจาก {item.source}: {item.url}"
            except Exception:
                continue
    return "", "", ""


def find_related_image(item) -> tuple[str, str, str]:
    provider = fp.env("IMAGE_PROVIDER", "auto").lower()
    providers = [provider] if provider not in ("auto", "all") else [
        "rss", "article", "wikimedia", "openverse", "unsplash", "reddit", "bing", "google"
    ]
    searchers = {
        "rss": search_rss_image,
        "article": search_article_image,
        "openverse": fp.search_openverse,
        "wikimedia": fp.search_wikimedia,
        "unsplash": fp.search_unsplash,
        "reddit": fp.search_reddit,
        "bing": fp.search_bing,
        "google": fp.search_google,
    }
    for name in providers:
        searcher = searchers.get(name)
        if not searcher:
            continue
        url, source, credit = searcher(item)
        if url:
            return url, source, credit
    LOG.error("No approved news image found after trying providers: %s; refusing to post", providers)
    return "", "", ""


def main() -> int:
    import argparse
    from football_poster import DEFAULT_FEEDS, env, fetch_feed, load_state, publish, required_env, save_state, choose_template, make_image, validate_image_file, write_post, rank_news

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--state-file", default=env("STATE_FILE", "state.json"))
    parser.add_argument("--output", default=env("OUTPUT_IMAGE", "output/latest.jpg"))
    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, env("LOG_LEVEL", "INFO").upper(), logging.INFO), format="%(asctime)s %(levelname)s %(message)s")
    feeds = {
        "BBC Sport": env("RSS_BBC_URL", DEFAULT_FEEDS["BBC Sport"]),
        "ESPN": env("RSS_ESPN_URL", DEFAULT_FEEDS["ESPN"]),
        "The Guardian": env("RSS_GUARDIAN_URL", DEFAULT_FEEDS["The Guardian"]),
        "FourFourTwo": env("RSS_FOURFOURTWO_URL", DEFAULT_FEEDS["FourFourTwo"]),
    }
    if env("RSS_GOAL_URL"):
        feeds["Goal.com"] = env("RSS_GOAL_URL")
    state_path = Path(args.state_file)
    state = load_state(state_path)
    posted = set(state["posted_ids"])
    items = [item for source, url in feeds.items() for item in fetch_feed(source, url) if item.id not in posted]
    if not items:
        LOG.info("No new news to post")
        return 0
    try:
        scores = rank_news(items)
    except Exception as exc:
        LOG.exception("News ranking failed: %s", exc)
        return 1
    candidates = [item for item in items if scores.get(item.id, {}).get("is_worthy")]
    if not candidates:
        LOG.info("No news passed the worthiness threshold")
        return 0
    ranked = sorted(candidates, key=lambda item: float(scores[item.id].get("score", 0)), reverse=True)
    last_error = None
    for item in ranked[:12]:
        try:
            item.image_url, item.image_source, item.image_credit = find_related_image(item)
            if not item.image_url:
                LOG.warning("Skip poster candidate, no image: %s", item.title[:90])
                continue
            post = write_post(item, scores[item.id])
            output = Path(args.output)
            template = choose_template(item)
            make_image(post["hook"], item.image_url, output, required_env("FONT_PATH"), template, item)
            validate_image_file(output)
            tags = [str(tag).strip() for tag in post.get("hashtags", []) if str(tag).strip()]
            text = "\n\n".join([post["hook"].strip(), post["body"].strip(), post["cta"].strip(), " ".join(tags), item.image_credit]).strip()
            LOG.info("Selected %s | score=%s | template=%s | image=%s", item.title, scores[item.id].get("score"), template, output)
            if args.dry_run:
                print(json.dumps({"item": asdict(item), "score": scores[item.id], "post": post, "caption": text, "image": str(output)}, ensure_ascii=False, indent=2))
                return 0
            result = publish(output, text, required_env("FB_PAGE_ID"), required_env("FB_PAGE_TOKEN"))
            LOG.info("Published to Facebook: %s", result)
            state["posted_ids"].append(item.id)
            save_state(state_path, state)
            return 0
        except Exception as exc:
            last_error = exc
            LOG.warning("Poster candidate failed (%s): %s", item.title[:90], exc)
            continue
    if last_error:
        LOG.exception("Post preparation/publication failed: %s", last_error)
        return 1
    LOG.info("No poster candidate had a usable news image")
    return 0


def patch() -> None:
    fp.image_is_acceptable = image_is_acceptable
    fp.image_url_variants = image_url_variants
    fp.search_rss_image = search_rss_image
    fp.search_article_image = search_article_image
    fp.find_related_image = find_related_image
    fp.main = main
