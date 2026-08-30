#!/usr/bin/env python3
"""Workflow entry that patches video_draft to use OpenAI-to-Gemini fallback."""
from __future__ import annotations
import logging
import ai_client
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

vd.fallback_storyboard = fallback_storyboard
vd.generate_storyboard = generate_storyboard
vd.generate_scene_images = generate_scene_images
vd.draw_scene = draw_scene
vd.fetch_feed = fetch_feed
vd.validate_news = validate_news
vd.publish_video = publish_video
vd.save_video_state = save_video_state

if __name__ == "__main__":
    raise SystemExit(vd.main())
