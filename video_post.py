#!/usr/bin/env python3
"""Workflow entry that patches video_draft to use OpenAI-to-Gemini fallback."""
from __future__ import annotations
import logging
import ai_client
import football_poster as fp
import news_grade
import poster_llm
import shared_stories
import video_draft as vd
from hf_image import generate_hf_image
from video_post_text import *
from video_post_story import *
from video_post_photos import generate_scene_images

LOG = logging.getLogger("video_post")
ai_client._hf_image = generate_hf_image
vd.DURATION = 10

_orig_fetch = fetch_feed
_orig_story = generate_storyboard
_orig_save = vd.save_video_state
_orig_main = vd.main
_orig_find = fp.find_related_image


def fetch_feed(source, url):
    kept = []
    for item in _orig_fetch(source, url):
        if shared_stories.is_used(item):
            LOG.info("Skip story already used by poster/video: %s", getattr(item, "title", "")[:80])
            continue
        kept.append(item)
    return kept


def generate_storyboard(item):
    shared_stories.mark(item)
    return _orig_story(item)


def save_video_state(path, state):
    return _orig_save(path, shared_stories.merge_into(state))


def find_related_image(item):
    url, source, credit = _orig_find(item)
    if url:
        return url, source, credit
    fallback = getattr(item, "image_url", "") or ""
    if fallback:
        return fallback, getattr(item, "image_source", "rss"), getattr(item, "image_credit", "")
    return "", "", ""


def main() -> int:
    from football_poster import DEFAULT_FEEDS

    bucket = []
    for source, url in DEFAULT_FEEDS.items():
        bucket.extend(fetch_feed(source, vd.env(
            {
                "BBC Sport": "RSS_BBC_URL",
                "ESPN": "RSS_ESPN_URL",
                "The Guardian": "RSS_GUARDIAN_URL",
                "FourFourTwo": "RSS_FOURFOURTWO_URL",
            }.get(source, ""),
            url,
        )))
    bucket = [item for item in bucket if getattr(item, "image_url", "")]
    try:
        scores = poster_llm.rank_news(bucket)
    except Exception as exc:
        LOG.warning("Video rank fallback to rule grade: %s", exc)
        scores = {item.id: news_grade.finalize(item, None, news_grade.VIDEO_MIN) for item in bucket}
    else:
        scores = {
            item.id: news_grade.finalize(item, scores.get(item.id), news_grade.VIDEO_MIN)
            for item in bucket
        }
    ranked = sorted(
        [item for item in bucket if (scores.get(item.id) or {}).get("is_worthy")],
        key=lambda item: float((scores.get(item.id) or {}).get("score", 0)),
        reverse=True,
    )
    if not ranked:
        LOG.info("No story reached video grade %s; skipped this round", news_grade.VIDEO_MIN)
        return 0
    picked = ranked[0]
    LOG.info("Video pick %s | score=%s", picked.title[:90], scores.get(picked.id, {}).get("score"))
    first_source = next(iter(DEFAULT_FEEDS))

    def only_winner(source, url):
        return [picked] if source == first_source else []

    vd.fetch_feed = only_winner
    vd.find_related_image = find_related_image
    fp.find_related_image = find_related_image
    try:
        return _orig_main()
    except RuntimeError as exc:
        if "No real news image" in str(exc):
            LOG.info("Top graded story had no usable photo; skipped this round")
            return 0
        raise

vd.fallback_storyboard = fallback_storyboard
vd.generate_storyboard = generate_storyboard
vd.generate_scene_images = generate_scene_images
vd.draw_scene = draw_scene
vd.fetch_feed = fetch_feed
vd.validate_news = validate_news
vd.publish_video = publish_video
vd.save_video_state = save_video_state
vd.find_related_image = find_related_image
vd.main = main

if __name__ == "__main__":
    raise SystemExit(main())
