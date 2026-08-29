#!/usr/bin/env python3
"""Create a review-only 15-second Thai football AI cartoon motion-comic draft."""
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
FPS, DURATION, SCENE_COUNT = 30, 15, 4
BOX_PAD_X = 48
BOX_PAD_Y = 26
BOX_TOP_MIN = 1080
BOX_BOTTOM_MAX = 1810


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def load_font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size=size)
    except Exception:
        return ImageFont.load_default()


def wrap(draw, text: str, font, width: int) -> list[str]:
    """Wrap Thai and English so a line never exceeds width."""
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
    """Validate the article, then ask the model for a four-scene Thai storyboard."""
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
    with tempfile.TemporaryDirectory(prefix="football-video-") as tmp:
        frames = Path(tmp)
        frames_per_scene = FPS * DURATION // SCENE_COUNT
        for frame_no in range(FPS * DURATION):
            scene_index = min(SCENE_COUNT - 1, frame_no // frames_per_scene)
            rendered = draw_scene(bases[scene_index], storyboard["scenes"][scene_index], scene_index, font_path)
            rendered.save(frames / f"frame_{frame_no:05d}.jpg", quality=90)
        output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([
            "ffmpeg", "-y", "-framerate", str(FPS), "-i", str(frames / "frame_%05d.jpg"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
        ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def main() -> int:
    logging.basicConfig(level=getattr(logging, env("LOG_LEVEL", "INFO").upper(), logging.INFO), format="%(asctime)s %(levelname)s %(message)s")
    output_dir = Path(env("VIDEO_DRAFT_DIR", "video_drafts"))
    state_path = Path(env("VIDEO_STATE_FILE", "video_draft_state.json"))
    state = load_state(state_path)
    drafted = set(state.get("drafted_ids", []))
    feed_env = {"BBC Sport": "RSS_BBC_URL", "ESPN": "RSS_ESPN_URL", "The Guardian": "RSS_GUARDIAN_URL", "FourFourTwo": "RSS_FOURFOURTWO_URL"}
    feeds = {name: env(feed_env[name], url) for name, url in DEFAULT_FEEDS.items()}
    items = [item for source, url in feeds.items() for item in fetch_feed(source, url) if item.id not in drafted and item.image_url]
    if not items:
        LOG.info("No new news with RSS images available for video draft")
        return 0
    item = items[0]
    item.image_url, item.image_source, item.image_credit = find_related_image(item)
    if not item.image_url:
        raise RuntimeError("No real news image found; video draft was skipped")
    storyboard = generate_storyboard(item)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    scene_dir = output_dir / f"scenes_{stamp}"
    scene_results = generate_scene_images(item, storyboard, scene_dir)
    video_path = output_dir / f"football_motion_comic_{stamp}.mp4"
    render_video([Path(entry["path"]) for entry in scene_results], storyboard, video_path, required_env("FONT_PATH"))
    draft = {
        "created_at": stamp,
        "duration_seconds": DURATION,
        "format": "vertical 1080x1920 MP4",
        "status": "draft_pending_review",
        "pipeline": ["news_validated", "storyboard_prompt_written", "four_ai_images_generated", "video_rendered", "pending_review"],
        "item": asdict(item),
        "storyboard": storyboard,
        "scene_images": scene_results,
        "video": str(video_path),
        "review_notes": "ตรวจชื่อผู้เล่น ตัวเลข ความหมายของข่าว ความเหมาะสมของมุก ภาพทั้ง 4 ฉาก และสิทธิ์สื่อก่อนเผยแพร่",
    }
    video_path.with_suffix(".json").write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    state.setdefault("drafted_ids", []).append(item.id)
    save_state(state_path, state)
    LOG.info("Created four-scene AI video draft: %s", video_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
