#!/usr/bin/env python3
"""Create a review-only 20-second Thai football AI cartoon motion-comic draft."""
from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps
from openai import OpenAI

from football_poster import DEFAULT_FEEDS, NewsItem, fetch_feed, find_related_image, load_state, required_env, save_state

LOG = logging.getLogger("video_draft")
W, H = 1080, 1920
FPS, DURATION, SCENE_COUNT = 30, 20, 4


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()
