#!/usr/bin/env python3
"""Workflow entry that patches video_draft to use OpenAI-to-Gemini fallback."""
from __future__ import annotations

import html
import json
import logging
import re
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin

from PIL import Image, ImageOps

import ai_client
import video_draft as vd
from ai_client import chat_json
from football_poster import http_get, image_url_variants, search_rss_image
from hf_image import generate_hf_image

ai_client._hf_image = generate_hf_image
LOG = logging.getLogger("video_post")
THAI_RE = re.compile(r"[\u0E00-\u0E7F]")
THAI_MARK = re.compile(r"[\u0E31\u0E34-\u0E3A\u0E47-\u0E4E]")
IMAGE_EXT_RE = re.compile(r"\.(jpe?g|png|webp)(?:$|\?)", re.I)
SCORE_RE = re.compile(r"\b\d{1,2}\s*[-\u2013]\s*\d{1,2}\b")
NAME_RE = re.compile(r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?)\b")
STORY_SPLIT = re.compile(r"\s*(?:นอกจากนี้|ในขณะเดียวกัน|ส่วน(?=[ก-ฮ])|meanwhile|separately)\s+", re.I)
MAX_PHOTO_BYTES = 4_000_000
SCENE_LABELS = {"สรุปสั้น", "สรุปข่าว", "สรุปข่าวสั้น"}
STOP_TOKENS = {
    "เกิดอะไรขึ้น", "นอกจากนี้", "อย่าง", "ใกล้ชิด", "สนใจ", "สถานการณ์",
    "ของ", "และ", "ที่", "ใน", "จะ", "ได้", "ไม่", "กับ", "จาก", "เพื่อ", "ส่วน", "ยัง",
}
