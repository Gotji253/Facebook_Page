#!/usr/bin/env python3
"""Create a 15-second Thai football motion-comic and post it to the Facebook Page."""
from __future__ import annotations

import base64
import json
import logging
import math
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
FPS, DURATION, SCENE_COUNT = 30, 15, 4
BOX_PAD_X = 48
BOX_PAD_Y = 26
BOX_TOP_MIN = 1080
BOX_BOTTOM_MAX = 1810
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


def wrap(draw, text: str, font, width: int) -> list[str]:
    text = " ".join(str(text).split())
    if not text:
        return [""]
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if draw.textbbox((0, 0), candidate, font=font)[2] <= width:
            current = candidate
            continue
        if current.strip():
            lines.append(current.strip())
            current = "" if char == " " else char
            continue
        lines.append(char)
        current = ""
    if current.strip():
        lines.append(current.strip())
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
    return {
        "scenes": [
            {"title": "ข่าวมาแล้ว", "line": item.title[:110], "narration": "มาดูข่าวฟุตบอลนี้แบบการ์ตูนล้อเลียนกันครับ"},
            {"title": "ประเด็นสำคัญ", "line": "เรื่องนี้ทำให้แฟนบอลต้องหยิบเครื่องคิดเลขขึ้นมา", "narration": "รายละเอียดจริงอยู่ในข่าวต้นทาง ส่วนมุกนี้ทำเพื่อความบันเทิง"},
            {"title": "มุมแฟนบอล", "line": "แฟนบอลบอกว่า ขอเวลาตั้งสติก่อนหนึ่งตลาดนักเตะ", "narration": "นี่คือการล้อเลียนแบบสุภาพ ไม่ใช่การกล่าวหาใคร"},
            {"title": "บทสรุป", "line": "ตรวจสอบข่าวต้นทางก่อนแชร์ แล้วพบกันคลิปหน้า", "narration": "ติดตามข่าวจริงและใช้วิจารณญาณก่อนแชร์ครับ"},
        ],
        "caption": f"การ์ตูนล้อเลียนข่าวฟุตบอลเพื่อความบันเทิง: {item.title}",
    }


def generate_storyboard(item: NewsItem) -> dict[str, object]:
    validate_news(item)
    client = OpenAI()
    request = {
        "title": item.title[:300],
        "summary": item.summary[:1400],
        "source": item.source,
        "source_url": item.url,
        "instruction": (
            "ตอบเป็น valid JSON เท่านั้น โดยมี scenes จำนวน 4 ฉากและ caption. "
            "แต่ละ scene ต้องมี title, line, narration และ image_prompt. "
            "title สั้นไม่เกิน 18 คำ line สั้นไม่เกิน 28 คำ. "
            "สรุปข่าวให้ผู้ชมเข้าใจง่าย ใช้มุกตลกภาษาไทยแบบสุภาพและระบุว่าเป็นการล้อเลียน. "
            "ห้ามเติมข้อเท็จจริง ตัวเลข หรือข้อกล่าวหาที่ไม่มีในข่าว. "
            "image_prompt ต้องเป็นภาษาอังกฤษสำหรับภาพการ์ตูนบรรณาธิการแนวเสียดสีฟุตบอล "
            "ไม่มีตัวหนังสือ โลโก้ สโมสร หรือลายน้ำในภาพ และให้ใช้ตัวละครนักฟุตบอลแบบ caricature ไม่ใช่ภาพถ่าย"
        ),
    }
    try:
        response = client.chat.completions.create(
            model=env("OPENAI_MODEL", "gpt-5-mini"),
            messages=[
                {"role": "system", "content": "คุณเป็นบรรณาธิการข่าวฟุตบอลและนักเขียนมุกภาษาไทย ต้องแยกข้อเท็จจริงออกจากมุกล้อเลียน และตอบเป็น JSON เท่านั้น"},
                {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
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
                    "title": str(scene.get("title", "ฉากข่าวฟุตบอล"))[:70],
                    "line": str(scene.get("line", "ติดตามข่าวฟุตบอลแบบเข้าใจง่าย"))[:110],
                    "narration": str(scene.get("narration", "นี่คือการ์ตูนล้อเลียนเพื่อความบันเทิง"))[:320],
                    "image_prompt": str(scene.get("image_prompt", "editorial football caricature, expressive players, dramatic stadium lighting, no text, no logos, no watermark"))[:1200],
                })
    if len(clean) != SCENE_COUNT:
        LOG.warning("Storyboard had invalid scene count; using safe fallback storyboard")
        return fallback_storyboard(item)
    return {"scenes": clean, "caption": str(data.get("caption", f"การ์ตูนล้อเลียนข่าวฟุตบอล: {item.title}"))[:1800]}


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
    text = storyboard_text(item, storyboard)
    style = rule_music_style(text)
    reason = "เลือกจากประเด็นข่าวและโทนการ์ตูนล้อเลียน"
    try:
        response = OpenAI().chat.completions.create(
            model=env("OPENAI_MODEL", "gpt-5-mini"),
            messages=[
                {"role": "system", "content": "เลือกเพลงประกอบคลิปข่าวฟุตบอลการ์ตูนล้อเลียน ตอบ JSON เท่านั้น"},
                {"role": "user", "content": json.dumps({
                    "title": item.title[:240],
                    "summary": item.summary[:500],
                    "caption": str(storyboard.get("caption", ""))[:300],
                    "choices": list(MUSIC_STYLES),
                    "instruction": "เลือก style หนึ่งค่าจาก choices พร้อม reason ภาษาไทยสั้นๆ",
                }, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content or "{}")
        picked = str(data.get("style", "")).strip().lower()
        if picked in MUSIC_STYLES:
            style = picked
        if data.get("reason"):
            reason = str(data["reason"])[:180]
    except Exception as exc:
        LOG.warning("Music analysis AI unavailable; using rule-based style %s: %s", style, exc)
    profile = MUSIC_STYLES[style]
    plan = {
        "style": style,
        "label": profile["label"],
        "bpm": profile["bpm"],
        "minor": profile["minor"],
        "volume": profile["volume"],
        "reason": reason,
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
        fade = min(t / 0.35, 1.0, max(0.0, (seconds - t) / 1.2))
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
        subprocess.run([
            "ffmpeg", "-y", "-i", str(video), "-i", str(bed),
            "-filter_complex", f"[1:a]volume={volume},afade=t=in:st=0:d=0.4,afade=t=out:st={DURATION-1.4}:d=1.3[a]",
            "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-shortest", str(mixed),
        ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        mixed.replace(video)
    plan["attached"] = True
    LOG.info("Attached %s music bed to %s", plan["style"], video)
    return plan


def make_image_prompt(item: NewsItem, scene: dict[str, str], scene_index: int) -> str:
    return (
        "Create a vertical 9:16 editorial cartoon illustration for a Thai football news parody video. "
        "Keep the main character in the upper two-thirds of the frame with clear headroom at the bottom for captions. "
        "Use fictionalized, non-photorealistic footballer caricatures inspired only by the article context; "
        "do not copy a real person's exact face, do not use club logos, brand marks, readable text, scoreboards, "
        "or watermarks. Make the scene visually explain the news with clear action and expressive body language. "
        "Bright professional comic colors, clean shapes, high contrast, suitable for social video. "
        f"Article context: {item.title[:240]}. Scene {scene_index + 1}: {scene['image_prompt']}"
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
            size=env("OPENAI_IMAGE_SIZE", "1024x1536"),
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

    hook_size, summary_size = 64, 44
    hook_font = load_font(font_path, hook_size)
    summary_font = load_font(font_path, summary_size)
    hook_lines = _measure(probe, scene["title"], hook_font, inner_width)
    summary_lines = _measure(probe, scene["line"], summary_font, inner_width)
    hook_gap = hook_size + 12
    summary_gap = summary_size + 10
    brand_h = 42
    content_h = BOX_PAD_Y + brand_h + (len(hook_lines) * hook_gap) + 12 + (len(summary_lines) * summary_gap) + BOX_PAD_Y

    while content_h > (BOX_BOTTOM_MAX - BOX_TOP_MIN) and hook_size > 40:
        hook_size -= 4
        summary_size = max(28, summary_size - 3)
        hook_font = load_font(font_path, hook_size)
        summary_font = load_font(font_path, summary_size)
        hook_lines = _measure(probe, scene["title"], hook_font, inner_width)
        summary_lines = _measure(probe, scene["line"], summary_font, inner_width)
        hook_gap = hook_size + 12
        summary_gap = summary_size + 10
        content_h = BOX_PAD_Y + brand_h + (len(hook_lines) * hook_gap) + 12 + (len(summary_lines) * summary_gap) + BOX_PAD_Y

    box_top = BOX_TOP_MIN
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

    footer = "การ์ตูนล้อเลียนเพื่อความบันเทิง | ตรวจสอบข่าวต้นทางก่อนแชร์"
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
        scene_len = DURATION / SCENE_COUNT
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
    try:
        post = write_post(item, {"main_angle": "คลิปการ์ตูนล้อเลียนข่าวฟุตบอล", "reason": item.summary[:200]})
        tags = " ".join(str(tag).strip() for tag in post.get("hashtags", []) if str(tag).strip())
        parts = [post["hook"].strip(), post["body"].strip(), post["cta"].strip(), tags]
    except Exception as exc:
        LOG.warning("write_post failed; using storyboard caption: %s", exc)
        parts = [str(storyboard.get("caption") or item.title)]
    if music:
        parts.append(f"เพลงประกอบ: {music.get('label', music.get('style'))}")
    parts.extend([
        "การ์ตูนล้อเลียนเพื่อความบันเทิง ตรวจสอบข่าวต้นทางก่อนแชร์",
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
        "pipeline": ["news_validated", "storyboard_prompt_written", "music_analyzed", "four_ai_images_generated", "video_rendered", "music_attached"],
        "item": asdict(item),
        "storyboard": storyboard,
        "music": music,
        "scene_images": scene_results,
        "video": str(video_path),
        "caption": caption,
        "review_notes": "ตรวจชื่อผู้เล่น ตัวเลข ความหมายของข่าว ความเหมาะสมของมุก ภาพทั้ง 4 ฉาก เพลงประกอบ และสิทธิ์สื่อก่อนเผยแพร่",
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
