#!/usr/bin/env python3
"""Workflow entry that patches video_draft to use OpenAI-to-Gemini fallback."""
from __future__ import annotations
import ai_client
import video_draft as vd
from hf_image import generate_hf_image
from video_post_text import *
from video_post_story import *
from video_post_photos import generate_scene_images

ai_client._hf_image = generate_hf_image
vd.fallback_storyboard = fallback_storyboard
vd.generate_storyboard = generate_storyboard
vd.generate_scene_images = generate_scene_images
vd.draw_scene = draw_scene
vd.fetch_feed = fetch_feed
vd.validate_news = validate_news
vd.publish_video = publish_video

if __name__ == "__main__":
    raise SystemExit(vd.main())
