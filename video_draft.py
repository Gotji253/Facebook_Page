#!/usr/bin/env python3
"""Create a 5-second Thai football motion-comic and post it to the Facebook Page."""
from __future__ import annotations

import base64
import json
import logging
import math
import re
import os
import struct
import subprocess
import tempfile
import wave
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps
from openai import OpenAI

from football_poster import (
    DEFAULT_FEEDS,
    NewsItem,
    fetch_feed,
    find_related_image,
    required_env,
    write_post,
)

LOG = logging.getLogger("video_draft")
W, H = 1080, 1920
FPS, DURATION, SCENE_COUNT = 30, 5, 2
BOX_PAD_X = 48
BOX_PAD_Y = 26
BOX_TOP_MIN = 980
BOX_BOTTOM_MAX = 1824
BOX_TOP_HARD = 720
MUSIC_STYLES = {
    "hype": {"bpm": 128, "minor": False, "label": "ฮึกเหิม จังหวะตลาดนักเตะ", "volume": 0.16},
    "triumph": {"bpm": 118, "minor": False, "label": "ฉลองชัย ยิงประตู", "volume": 0.17},
    "tense": {"bpm": 96, "minor": True, "label": "ดราม่า กดดัน", "volume": 0.14},
    "comedy": {"bpm": 112, "minor": False, "label": "ล้อเลียน สนุกเบาสมอง", "volume": 0.15},
    "calm": {"bpm": 84, "minor": True, "label": "วิเคราะห์ นุ่มฟังง่าย", "volume": 0.12},
}


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def load_font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size=size)
    except Exception:
        return ImageFont.load_default()


THAI_COMBINING = re.compile(r"[\u0E31\u0E34-\u0E3A\u0E47-\u0E4E]")


def _clusters(text: str) -> list[str]:
    """Keep a Thai consonant together with its vowel and tone marks."""
    clusters: list[str] = []
    for char in text:
        if clusters and THAI_COMBINING.fullmatch(char):
            clusters[-1] += char
        else:
            clusters.append(char)
    return clusters


def wrap(draw, text: str, font, width: int) -> list[str]:
    text = " ".join(str(text).split())
    if not text:
        return [""]

    def fits(value: str) -> bool:
        return draw.textbbox((0, 0), value, font=font)[2] <= width

    words: list[str] = []
    buf = ""
    for piece in _clusters(text):
        if piece == " ":
            if buf:
                words.append(buf)
                buf = ""
            continue
        buf += piece
    if buf:
        words.append(buf)

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if fits(candidate):
            current = candidate
            continue
        if current:
            lines.append(current)
        if fits(word):
            current = word
            continue
        chunk = ""
        for cluster in _clusters(word):
            trial = chunk + cluster
            if chunk and not fits(trial):
                lines.append(chunk)
                chunk = cluster
            else:
                chunk = trial
        current = chunk
    if current:
        lines.append(current)

    while len(lines) >= 2 and len(lines[-1]) <= 6:
        prev, last = lines[-2], lines[-1]
        glued = prev + last
        if fits(glued):
            lines[-2:] = [glued]
            continue
        spaced = f"{prev} {last}"
        if fits(spaced):
            lines[-2:] = [spaced]
            continue
        break
    return lines or [text]


def load_video_state(path: Path) -> dict:
    if not path.exists():
        return {"drafted_ids": [], "posted_ids": [], "updated_at": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        LOG.warning("Cannot read video state: %s", exc)
        return {"drafted_ids": [], "posted_ids": [], "updated_at": None}
    return {
        "drafted_ids": list(data.get("drafted_ids", [])),
        "posted_ids": list(data.get("posted_ids", [])),
        "updated_at": data.get("updated_at"),
    }


def save_video_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["drafted_ids"] = list(dict.fromkeys(state.get("drafted_ids", [])))[-5000:]
    state["posted_ids"] = list(dict.fromkeys(state.get("posted_ids", [])))[-5000:]
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_news(item: NewsItem) -> None:
    if not item.title.strip() or not item.summary.strip() or not item.url.startswith("http"):
        raise ValueError("ข่าวไม่มี title, summary หรือ URL ที่ตรวจสอบได้")
    if len(item.title) < 12:
        raise ValueError("หัวข้อข่าวสั้นเกินไปสำหรับสร้างวิดีโอ")


def fallback_storyboard(item: NewsItem) -> dict[str, object]:
    title = item.title[:90]
    point = (item.summary or title).strip()[:110]
    return {
        "scenes": [
            {"title": "เกิดอะไรขึ้น", "line": title, "narration": title, "image_prompt": "editorial football caricature, breaking news, no text, no logos"},
            {"title": "สรุปสั้น", "line": point, "narration": point, "image_prompt": "editorial football caricature, recap, no text, no logos"},
        ],
        "caption": title,
        "hook": title[:40],
        "body": point,
        "cta": "แฟนบอลมองเรื่องนี้ยังไงครับ?",
        "hashtags": ["#ข่าวฟุตบอล", "#รอบรู้Insight", "#เล่าข่าวสั้น"],
        "music_style": "comedy",
    }


def generate_storyboard(item: NewsItem) -> dict[str, object]:
    validate_news(item)
    request = {
        "title": item.title[:180],
        "summary": item.summary[:280],
        "source": item.source,
        "instruction": "JSON สั้น: scenes 2 ฉาก, caption, hook, body, cta, hashtags, music_style. ฉาก1=เกิดอะไรขึ้น ฉาก2=สรุปประเด็น ภาษาไทยเข้าใจง่าย",
    }
    try:
        response = OpenAI().chat.completions.create(
            model=env("OPENAI_MODEL", "gpt-5-mini"),
            messages=[
                {"role": "system", "content": "บรรณาธิการข่าวฟุตบอล ตอบ JSON สั้นเท่านั้น"},
                {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=700,
        )
        data = json.loads(response.choices[0].message.content or "{}")
    except Exception as exc:
        LOG.warning("Storyboard AI failed; using safe fallback storyboard: %s", exc)
        return fallback_storyboard(item)
    scenes = data.get("scenes")
    if isinstance(scenes, dict):
        scenes = list(scenes.values())
    clean: list[dict[str, str]] = []
    if isinstance(scenes, list):
        for scene in scenes[:SCENE_COUNT]:
            if isinstance(scene, dict):
                clean.append({
                    "title": str(scene.get("title", "สรุปข่าว"))[:40],
                    "line": str(scene.get("line", item.title))[:90],
                    "narration": str(scene.get("narration", scene.get("line", item.title)))[:140],
                    "image_prompt": str(scene.get("image_prompt", "editorial football caricature, no text"))[:280],
                })
    if len(clean) != SCENE_COUNT:
        return fallback_storyboard(item)
    tags = data.get("hashtags") if isinstance(data.get("hashtags"), list) else []
    style = str(data.get("music_style", "")).strip().lower()
    if style not in MUSIC_STYLES:
        style = rule_music_style(f"{item.title} {item.summary}")
    return {
        "scenes": clean,
        "caption": str(data.get("caption") or data.get("hook") or item.title)[:500],
        "hook": str(data.get("hook") or clean[0]["line"])[:80],
        "body": str(data.get("body") or clean[1]["line"])[:400],
        "cta": str(data.get("cta") or "แฟนบอลมองเรื่องนี้ยังไงครับ?")[:120],
        "hashtags": [str(tag).strip()[:40] for tag in tags if str(tag).strip()][:5] or ["#ข่าวฟุตบอล", "#รอบรู้Insight", "#เล่าข่าวสั้น"],
        "music_style": style,
    }


def storyboard_text(item: NewsItem, storyboard: dict[str, object]) -> str:
    scenes = storyboard.get("scenes") or []
    bits = [item.title, item.summary, str(storyboard.get("caption", ""))]
    for scene in scenes:
        if isinstance(scene, dict):
            bits.extend([str(scene.get("title", "")), str(scene.get("line", "")), str(scene.get("narration", ""))])
    return " ".join(bits).lower()


def rule_music_style(text: str) -> str:
    if any(word in text for word in ("goal", "hat-trick", "winner", "ประตู", "ชนะ", "แชมป์", "ถ้วย")):
        return "triumph"
    if any(word in text for word in ("transfer", "sign", "deal", "ย้าย", "ตลาด", "ค่าตัว")):
        return "hype"
    if any(word in text for word in ("sack", "ban", "injury", "crisis", "ดราม่า", "โดนแบน", "บาดเจ็บ", "ไล่")):
        return "tense"
    if any(word in text for word in ("analysis", "tactics", "preview", "วิเคราะห์", "แผน")):
        return "calm"
    return "comedy"


def analyze_music(item: NewsItem, storyboard: dict[str, object]) -> dict[str, object]:
    picked = str(storyboard.get("music_style", "")).strip().lower()
    style = picked if picked in MUSIC_STYLES else rule_music_style(storyboard_text(item, storyboard))
    profile = MUSIC_STYLES[style]
    plan = {
        "style": style,
        "label": profile["label"],
        "bpm": profile["bpm"],
        "minor": profile["minor"],
        "volume": profile["volume"],
        "reason": "เลือกจากคำขอวิดีโอครั้งเดียวหรือกติกาโทนข่าว",
        "source": "original_bed_no_copyright",
    }
    LOG.info("Music plan: %s (%s bpm) %s", plan["style"], plan["bpm"], plan["reason"])
    return plan


def synthesize_bed(path: Path, plan: dict[str, object], seconds: int = DURATION) -> None:
    rate = 22050
    bpm = int(plan.get("bpm", 112))
    minor = bool(plan.get("minor"))
    step = 60.0 / bpm
    freqs = (196.0, 233.1, 293.7, 349.2) if minor else (196.0, 246.9, 293.7, 392.0)
    samples: list[float] = []
    total = int(rate * seconds)
    for i in range(total):
        t = i / rate
        beat = int(t / step)
        freq = freqs[beat % len(freqs)]
        kick = math.exp(-((t % step) * 18)) * math.sin(2 * math.pi * 70 * t)
        bass = 0.22 * math.sin(2 * math.pi * freq * t)
        spark = 0.08 * math.sin(2 * math.pi * freq * 2 * t) if beat % 2 == 0 else 0.0
        fade = min(t / 0.25, 1.0, max(0.0, (seconds - t) / 0.6))
        samples.append(max(-1.0, min(1.0, (kick * 0.35 + bass + spark) * fade)))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"".join(struct.pack("<h", int(sample * 30000)) for sample in samples))


def attach_music(video: Path, plan: dict[str, object]) -> dict[str, object]:
    if env("SKIP_MUSIC") in {"1", "true", "yes"}:
        plan["attached"] = False
        return plan
    with tempfile.TemporaryDirectory(prefix="football-music-") as tmp:
        bed = Path(tmp) / "bed.wav"
        mixed = Path(tmp) / "mixed.mp4"
        synthesize_bed(bed, plan)
        volume = float(plan.get("volume", 0.15))
        fade_out = max(0.4, DURATION - 0.8)
        subprocess.run([
            "ffmpeg", "-y", "-i", str(video), "-i", str(bed),
            "-filter_complex", f"[1:a]volume={volume},afade=t=in:st=0:d=0.2,afade=t=out:st={fade_out}:d=0.7[a]",
            "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-shortest", str(mixed),
        ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        mixed.replace(video)
    plan["attached"] = True
    LOG.info("Attached %s music bed to %s", plan["style"], video)
    return plan


def make_image_prompt(item: NewsItem, scene: dict[str, str], scene_index: int) -> str:
    extra = scene.get("image_prompt", "editorial football caricature, no text")
    return (
        "Vertical 9:16 editorial football caricature, no text, no logos, no watermark. "
        f"News: {item.title[:120]}. Scene {scene_index + 1}: {extra[:220]}"
    )


def generate_scene_images(item: NewsItem, storyboard: dict[str, object], output_dir: Path) -> list[dict[str, object]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    client = OpenAI()
    results: list[dict[str, object]] = []
    for index, scene in enumerate(storyboard["scenes"]):
        prompt = make_image_prompt(item, scene, index)
        LOG.info("Generating AI cartoon image %d/%d", index + 1, SCENE_COUNT)
        response = client.images.generate(
            model=env("OPENAI_IMAGE_MODEL", "gpt-image-1"),
            prompt=prompt,
            size=env("OPENAI_IMAGE_SIZE", "768x1344"),
        )
        payload = response.data[0]
        image_path = output_dir / f"scene_{index + 1:02d}.png"
        if getattr(payload, "b64_json", None):
            image_path.write_bytes(base64.b64decode(payload.b64_json))
        elif getattr(payload, "url", None):
            image_path.write_bytes(requests.get(payload.url, timeout=90).content)
        else:
            raise RuntimeError(f"AI image provider returned no image for scene {index + 1}")
        with Image.open(image_path) as check:
            check.verify()
        results.append({"scene": index + 1, "path": str(image_path), "prompt": prompt, "source": "AI-generated"})
    return results


def prepare_scene(image_path: Path) -> Image.Image:
    image = Image.open(image_path).convert("RGB")
    image = ImageOps.fit(image, (W, H), method=Image.Resampling.LANCZOS, centering=(0.5, 0.32))
    image = ImageEnhance.Color(image).enhance(1.12)
    image = ImageEnhance.Contrast(image).enhance(1.10)
    return image.filter(ImageFilter.EDGE_ENHANCE)


def _measure(draw, text: str, font, width: int) -> list[str]:
    return wrap(draw, text, font, width)


def _draw_centered_lines(draw, lines: list[str], font, y: int, fill, line_height: int, box_left: int, box_right: int) -> int:
    max_width = box_right - box_left
    for line in lines:
        box = draw.textbbox((0, 0), line, font=font)
        text_w = min(box[2] - box[0], max_width)
        x = box_left + max(0, (max_width - text_w) // 2)
        draw.text((x + 2, y + 3), line, font=font, fill=(0, 0, 0, 200), stroke_width=3, stroke_fill=(0, 0, 0, 210))
        draw.text((x, y), line, font=font, fill=fill, stroke_width=2, stroke_fill=(0, 0, 0, 230))
        y += line_height
    return y


def draw_scene(base: Image.Image, scene: dict[str, str], scene_index: int, font_path: str) -> Image.Image:
    frame = base.copy().convert("RGBA")
    zoom = 1.0 + scene_index * 0.02
    crop_w, crop_h = int(W / zoom), int(H / zoom)
    left = max(0, (W - crop_w) // 2)
    top = max(0, int(crop_h * 0.02))
    frame = frame.crop((left, top, left + crop_w, min(H, top + crop_h))).resize((W, H), Image.Resampling.LANCZOS)

    box_left, box_right = 40, W - 40
    inner_left = box_left + BOX_PAD_X
    inner_right = box_right - BOX_PAD_X
    inner_width = inner_right - inner_left
    probe = ImageDraw.Draw(Image.new("RGBA", (W, H)))

    hook_size, summary_size = 52, 38
    hook_font = load_font(font_path, hook_size)
    summary_font = load_font(font_path, summary_size)
    hook_lines = _measure(probe, scene["title"], hook_font, inner_width)
    summary_lines = _measure(probe, scene["line"], summary_font, inner_width)
    hook_gap = hook_size + 10
    summary_gap = summary_size + 8
    brand_h = 42

    def _content_h() -> int:
        return BOX_PAD_Y + brand_h + (len(hook_lines) * hook_gap) + 8 + (len(summary_lines) * summary_gap) + BOX_PAD_Y

    content_h = _content_h()
    max_h = BOX_BOTTOM_MAX - BOX_TOP_HARD
    while content_h > max_h and hook_size > 34:
        hook_size -= 2
        summary_size = max(24, summary_size - 1)
        hook_font = load_font(font_path, hook_size)
        summary_font = load_font(font_path, summary_size)
        hook_lines = _measure(probe, scene["title"], hook_font, inner_width)
        summary_lines = _measure(probe, scene["line"], summary_font, inner_width)
        hook_gap = hook_size + 10
        summary_gap = summary_size + 8
        content_h = _content_h()

    box_bottom = BOX_BOTTOM_MAX
    box_top = max(BOX_TOP_HARD, min(BOX_TOP_MIN, box_bottom - content_h))
    if box_top + content_h < box_bottom:
        box_bottom = min(BOX_BOTTOM_MAX, box_top + content_h)
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rectangle((0, box_top - 70, W, H), fill=(7, 18, 45, 70))
    od.rounded_rectangle((box_left, box_top, box_right, box_bottom), radius=28, fill=(7, 18, 45, 232), outline=(255, 214, 88, 230), width=3)
    frame = Image.alpha_composite(frame, overlay)
    draw = ImageDraw.Draw(frame)
    y = box_top + BOX_PAD_Y
    brand_font = load_font(font_path, 28)
    brand = "รอบรู้ : INSIGHT"
    brand_box = draw.textbbox((0, 0), brand, font=brand_font)
    brand_x = inner_left + (inner_width - (brand_box[2] - brand_box[0])) // 2
    draw.text((brand_x, y), brand, font=brand_font, fill=(255, 214, 88))
    y += brand_h
    y = _draw_centered_lines(draw, hook_lines, hook_font, y, "white", hook_gap, inner_left, inner_right)
    y += 8
    _draw_centered_lines(draw, summary_lines, summary_font, y, (255, 240, 160), summary_gap, inner_left, inner_right)
    footer = "สรุปข่าวสั้น | ตรวจสอบข่าวต้นทางก่อนแชร์"
    footer_font = load_font(font_path, 24)
    footer_box = draw.textbbox((0, 0), footer, font=footer_font)
    footer_x = (W - (footer_box[2] - footer_box[0])) // 2
    draw.text((footer_x, 1834), footer, font=footer_font, fill=(225, 235, 248))
    return frame.convert("RGB")


def render_video(scene_images: list[Path], storyboard: dict[str, object], output: Path, font_path: str) -> None:
    bases = [prepare_scene(path) for path in scene_images]
    composed = []
    with tempfile.TemporaryDirectory(prefix="football-video-") as tmp:
        tmp_path = Path(tmp)
        for index, base in enumerate(bases):
            frame = draw_scene(base, storyboard["scenes"][index], index, font_path)
            frame_path = tmp_path / f"scene_{index + 1:02d}.jpg"
            frame.save(frame_path, quality=92)
            composed.append(frame_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        scene_len = DURATION / max(1, len(composed))
        cmd = ["ffmpeg", "-y"]
        for path in composed:
            cmd.extend(["-loop", "1", "-t", f"{scene_len:.2f}", "-i", str(path)])
        inputs = "".join(f"[{i}:v]" for i in range(len(composed)))
        cmd.extend([
            "-filter_complex", f"{inputs}concat=n={len(composed)}:v=1:a=0,format=yuv420p[v]",
            "-map", "[v]", "-r", str(FPS), "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(output),
        ])
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def build_caption(item: NewsItem, storyboard: dict[str, object], music: dict[str, object] | None = None) -> str:
    hook = str(storyboard.get("hook") or "").strip()
    body = str(storyboard.get("body") or "").strip()
    cta = str(storyboard.get("cta") or "").strip()
    tags = " ".join(str(tag).strip() for tag in (storyboard.get("hashtags") or []) if str(tag).strip())
    if hook and body:
        parts = [hook, body, cta, tags]
    else:
        try:
            post = write_post(item, {"main_angle": "คลิปสรุปข่าวฟุตบอล", "reason": item.summary[:200]})
            tags = " ".join(str(tag).strip() for tag in post.get("hashtags", []) if str(tag).strip())
            parts = [post["hook"].strip(), post["body"].strip(), post["cta"].strip(), tags]
        except Exception as exc:
            LOG.warning("write_post failed; using storyboard caption: %s", exc)
            parts = [str(storyboard.get("caption") or item.title)]
    if music:
        parts.append(f"เพลงประกอบ: {music.get('label', music.get('style'))}")
    parts.extend([
        "สรุปข่าวสั้น ตรวจสอบข่าวต้นทางก่อนแชร์",
        f"แหล่งข่าว: {item.source} {item.url}",
    ])
    return "\n\n".join(part for part in parts if part).strip()


def publish_video(video: Path, caption: str, page_id: str, token: str) -> dict:
    if not video.is_file() or video.stat().st_size == 0:
        raise RuntimeError(f"Video file missing: {video}")
    version = env("FB_API_VERSION", "v23.0")
    identity = requests.get(
        f"https://graph.facebook.com/{version}/me",
        params={"fields": "id,name", "access_token": token},
        timeout=30,
    )
    me = identity.json() if identity.ok else {}
    LOG.info("Page token identity name=%s id=%s", me.get("name"), me.get("id"))
    payload = {
        "access_token": token,
        "description": caption,
        "title": caption.split("\n", 1)[0][:80],
        "published": "true",
    }
    endpoints = [
        f"https://graph.facebook.com/{version}/me/videos",
        f"https://graph.facebook.com/{version}/{me.get('id')}/videos" if me.get("id") else "",
        f"https://graph.facebook.com/{version}/{page_id}/videos",
    ]
    errors = []
    for url in endpoints:
        if not url:
            continue
        LOG.info("Uploading video to %s", url.split("?")[0])
        with video.open("rb") as handle:
            response = requests.post(
                url,
                data=payload,
                files={"source": (video.name, handle, "video/mp4")},
                timeout=(20, 180),
            )
        if response.ok:
            return response.json()
        errors.append(f"{url}: {response.status_code} {response.text[:240]}")
        LOG.warning("Video publish failed: %s", errors[-1])
    raise RuntimeError("Facebook video API error: " + " | ".join(errors))


def main() -> int:
    logging.basicConfig(level=getattr(logging, env("LOG_LEVEL", "INFO").upper(), logging.INFO), format="%(asctime)s %(levelname)s %(message)s")
    output_dir = Path(env("VIDEO_DRAFT_DIR", "video_drafts"))
    state_path = Path(env("VIDEO_STATE_FILE", "video_draft_state.json"))
    state = load_video_state(state_path)
    used = set(state.get("drafted_ids", [])) | set(state.get("posted_ids", []))
    feed_env = {"BBC Sport": "RSS_BBC_URL", "ESPN": "RSS_ESPN_URL", "The Guardian": "RSS_GUARDIAN_URL", "FourFourTwo": "RSS_FOURFOURTWO_URL"}
    feeds = {name: env(feed_env[name], url) for name, url in DEFAULT_FEEDS.items()}
    items = [item for source, url in feeds.items() for item in fetch_feed(source, url) if item.id not in used and item.image_url]
    if not items:
        LOG.info("No new news with RSS images available for video")
        return 0
    item = items[0]
    item.image_url, item.image_source, item.image_credit = find_related_image(item)
    if not item.image_url:
        raise RuntimeError("No real news image found; video was skipped")
    storyboard = generate_storyboard(item)
    music = analyze_music(item, storyboard)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    scene_dir = output_dir / f"scenes_{stamp}"
    scene_results = generate_scene_images(item, storyboard, scene_dir)
    video_path = output_dir / f"football_motion_comic_{stamp}.mp4"
    render_video([Path(entry["path"]) for entry in scene_results], storyboard, video_path, required_env("FONT_PATH"))
    music = attach_music(video_path, music)
    caption = build_caption(item, storyboard, music)
    draft = {
        "created_at": stamp,
        "duration_seconds": DURATION,
        "format": "vertical 1080x1920 MP4",
        "status": "ready_to_post",
        "pipeline": ["news_validated", "one_ai_storyboard", "two_ai_images", "video_rendered", "music_attached"],
        "item": asdict(item),
        "storyboard": storyboard,
        "music": music,
        "scene_images": scene_results,
        "video": str(video_path),
        "caption": caption,
        "review_notes": "ตรวจชื่อผู้เล่น ตัวเลข และสรุป 2 ฉากก่อนเผยแพร่",
    }
    should_post = env("POST_TO_FACEBOOK", "1") not in {"0", "false", "no"} and env("VIDEO_DRY_RUN") not in {"1", "true", "yes"}
    post_error = None
    if should_post:
        try:
            result = publish_video(video_path, caption, required_env("FB_PAGE_ID"), required_env("FB_PAGE_TOKEN"))
            draft["status"] = "posted"
            draft["facebook"] = result
            draft["pipeline"].append("posted_to_facebook")
            state.setdefault("posted_ids", []).append(item.id)
            LOG.info("Published video to Facebook: %s", result)
        except Exception as exc:
            post_error = exc
            draft["status"] = "render_ok_post_failed"
            draft["facebook_error"] = str(exc)[:800]
            LOG.exception("Facebook video publish failed: %s", exc)
    else:
        draft["status"] = "draft_pending_review"
        LOG.info("Skipped Facebook publish (dry-run or POST_TO_FACEBOOK disabled)")
    video_path.with_suffix(".json").write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    state.setdefault("drafted_ids", []).append(item.id)
    save_video_state(state_path, state)
    LOG.info("Created video: %s", video_path)
    if post_error:
        raise post_error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
