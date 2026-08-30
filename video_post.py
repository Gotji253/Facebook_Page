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
