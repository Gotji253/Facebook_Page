#!/usr/bin/env python3
"""Standalone hourly football-news poster for a Facebook Page."""
from __future__ import annotations
import argparse, hashlib, json, logging, os, re, sys, tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
import feedparser
import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from openai import OpenAI

LOG = logging.getLogger("football_poster")
USER_AGENT = "FacebookPageFootballPoster/1.0"
DEFAULT_FEEDS = {"BBC Sport": "https://feeds.bbci.co.uk/sport/football/rss.xml", "ESPN": "https://www.espn.com/espn/rss/soccer/news", "Goal.com": "https://www.goal.com/feeds/en/news"}
W, H = 1200, 630

@dataclass
class NewsItem:
    id: str; source: str; title: str; summary: str; url: str; image_url: str = ""; published: str = ""

def env(name: str, default: str = "") -> str: return os.getenv(name, default).strip()
def required_env(name: str) -> str:
    value = env(name)
    if not value: raise RuntimeError(f"Missing required environment variable: {name}")
    return value

def http_get(url: str, **kwargs: Any) -> requests.Response:
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    r = requests.get(url, headers=headers, timeout=(10, 30), **kwargs); r.raise_for_status(); return r

def first_url(value: Any) -> str:
    if isinstance(value, str): return value
    if isinstance(value, dict): return str(value.get("href") or value.get("url") or "")
    return ""

def image_from_entry(entry: Any, feed_url: str) -> str:
    for key in ("media_content", "media_thumbnail", "enclosures"):
        for media in entry.get(key, []) or []:
            url = first_url(media)
            if url: return urljoin(feed_url, url)
    match = re.search(r'<img[^>]+src=["\']([^"\']+)', str(entry.get("summary", "")), re.I)
    return urljoin(feed_url, match.group(1)) if match else ""

def fetch_feed(source: str, feed_url: str) -> list[NewsItem]:
    try:
        parsed = feedparser.parse(http_get(feed_url).content)
        if getattr(parsed, "bozo", False) and not parsed.entries: raise RuntimeError(str(parsed.bozo_exception))
        result = []
        for entry in parsed.entries[:30]:
            title = re.sub(r"\s+", " ", str(entry.get("title", ""))).strip()
            if not title: continue
            summary = re.sub(r"<[^>]+>", " ", str(entry.get("summary", entry.get("description", ""))))
            summary = re.sub(r"\s+", " ", summary).strip()
            raw_id = str(entry.get("id") or entry.get("guid") or entry.get("link") or title)
            ident = hashlib.sha256(f"{source}:{raw_id}".encode()).hexdigest()
            result.append(NewsItem(ident, source, title, summary[:1200], str(entry.get("link", "")), image_from_entry(entry, feed_url), str(entry.get("published", entry.get("updated", "")))))
        LOG.info("%s: found %d entries", source, len(result)); return result
    except Exception as exc:
        LOG.warning("RSS unavailable (%s): %s", source, exc); return []

def load_state(path: Path) -> dict[str, Any]:
    if not path.exists(): return {"posted_ids": [], "updated_at": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8")); return {"posted_ids": list(data.get("posted_ids", [])), "updated_at": data.get("updated_at")}
    except Exception as exc:
        LOG.warning("Cannot read state file: %s", exc); return {"posted_ids": [], "updated_at": None}

def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); state["posted_ids"] = list(dict.fromkeys(state.get("posted_ids", [])))[-5000:]; state["updated_at"] = datetime.now(timezone.utc).isoformat()
    fd, tmp = tempfile.mkstemp(prefix="state-", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f: json.dump(state, f, ensure_ascii=False, indent=2); f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def schema(name: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}}}

def rank_news(items: list[NewsItem]) -> dict[str, dict[str, Any]]:
    # ส่งเฉพาะฟิลด์ที่จำเป็นและจำกัด summary เพื่อไม่ให้ context ใหญ่เกินไป
    payload = [
        {
            "id": item.id,
            "source": item.source,
            "title": item.title[:250],
            "summary": item.summary[:400],
            "published": item.published,
        }
        for item in items[:40]
    ]

    messages = [
        {
            "role": "system",
            "content": (
                "คุณเป็นบรรณาธิการข่าวฟุตบอลสำหรับผู้อ่านชาวไทย "
                "ให้คะแนนข่าวทุกรายการและตอบกลับเป็น JSON object เท่านั้น "
                "ห้ามใช้ Markdown หรือ code fence "
                "พิจารณาทีม/นักเตะดัง ดราม่า ทรานส์เฟอร์ ผลแข่งสำคัญ "
                "และความสดใหม่ คะแนนอยู่ระหว่าง 0-100 "
                "และ is_worthy=true เมื่อเหมาะสำหรับโพสต์บนเพจข่าวฟุตบอลไทย "
                "ตอบสั้น กระชับ ไม่ต้องอธิบายเพิ่มเติม "
                "JSON ต้องมีโครงสร้าง {\"items\":[{\"id\":\"...\",\"score\":0,"
                "\"is_worthy\":true,\"main_angle\":\"...\",\"reason\":\"...\"}]}"
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]

    client = OpenAI()
    request_args = {
        "model": env("OPENAI_MODEL", "gpt-5-mini"),
        "messages": messages,
        "response_format": {"type": "json_object"},
        "max_completion_tokens": 5000,
    }
    try:
        response = client.chat.completions.create(
            **request_args,
            reasoning_effort="minimal",
        )
    except Exception as exc:
        # บาง endpoint/SDK รุ่นเก่าอาจไม่รองรับ minimal ให้ลอง low แทน
        LOG.warning("reasoning_effort=minimal failed; retrying with low: %s", exc)
        response = client.chat.completions.create(
            **request_args,
            reasoning_effort="low",
        )

    if not response.choices:
        print("OpenAI ไม่ส่ง choices กลับมา")
        print(response.model_dump_json(indent=2))
        raise RuntimeError("OpenAI response มี choices ว่าง")

    choice = response.choices[0]
    message = choice.message
    raw_response = message.content or ""

    # แสดง metadata ที่ช่วยวินิจฉัยกรณี content ว่างหรือถูกตัดจบ
    if not raw_response.strip():
        print("OpenAI response ไม่มี content")
        print("finish_reason:", choice.finish_reason)
        print("refusal:", getattr(message, "refusal", None))
        print("OpenAI response:")
        print(response.model_dump_json(indent=2))
        raise RuntimeError(
            f"OpenAI ไม่ส่งข้อความกลับมา (finish_reason={choice.finish_reason})"
        )

    cleaned_response = raw_response.strip()
    cleaned_response = re.sub(
        r"^```(?:json)?\s*", "", cleaned_response, flags=re.IGNORECASE
    )
    cleaned_response = re.sub(r"\s*```$", "", cleaned_response).strip()

    try:
        parsed_response = json.loads(cleaned_response)
    except json.JSONDecodeError as exc:
        print("ไม่สามารถ parse JSON จาก OpenAI ได้")
        print("OpenAI raw response:")
        print(repr(raw_response))
        print(f"JSONDecodeError: {exc}")
        raise

    return {
        str(item["id"]): item
        for item in parsed_response.get("items", [])
    }

def write_post(item: NewsItem, score: dict[str, Any]) -> dict[str, Any]:
    post_input = {
        "title": item.title[:300],
        "summary": item.summary[:600],
        "source": item.source,
        "angle": score.get("main_angle", ""),
        "reason": score.get("reason", ""),
    }
    messages = [
        {
            "role": "system",
            "content": (
                "เขียนโพสต์ข่าวฟุตบอลภาษาไทยล้วนและตอบเป็น JSON object เท่านั้น "
                "ห้ามใช้ Markdown หรือ code fence และห้ามใช้อีโมจิใน hook "
                "hook ต้องสั้นและแรงไม่เกินประมาณ 40 ตัวอักษร "
                "body ต้องมี 3-5 บรรทัด เรียบเรียงไม่แปลตรงตัว "
                "cta ชวนคอมเมนต์หรือแชร์ และ hashtags ภาษาไทย 3-5 รายการ "
                "ห้ามใส่คำอธิบายนอก JSON"
            ),
        },
        {"role": "user", "content": json.dumps(post_input, ensure_ascii=False)},
    ]
    post_schema = {
        "hook": {"type": "string"},
        "body": {"type": "string"},
        "cta": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 5},
    }
    request_args = {
        "model": env("OPENAI_MODEL", "gpt-5-mini"),
        "messages": messages,
        "response_format": {"type": "json_object"},
        "max_completion_tokens": 3000,
    }
    client = OpenAI()
    try:
        response = client.chat.completions.create(**request_args, reasoning_effort="minimal")
    except Exception as exc:
        LOG.warning("write_post minimal reasoning failed; retrying with low: %s", exc)
        response = client.chat.completions.create(**request_args, reasoning_effort="low")

    if not response.choices:
        print("write_post: OpenAI ไม่ส่ง choices กลับมา")
        print(response.model_dump_json(indent=2))
        raise RuntimeError("OpenAI response มี choices ว่างใน write_post")

    choice = response.choices[0]
    message = choice.message
    raw_response = message.content or ""
    if not raw_response.strip():
        print("write_post: OpenAI response ไม่มี content")
        print("finish_reason:", choice.finish_reason)
        print("refusal:", getattr(message, "refusal", None))
        print("OpenAI response:")
        print(response.model_dump_json(indent=2))
        raise RuntimeError(
            f"OpenAI ไม่ส่งข้อความสำหรับโพสต์กลับมา (finish_reason={choice.finish_reason})"
        )

    cleaned_response = raw_response.strip()
    cleaned_response = re.sub(
        r"^```(?:json)?\s*", "", cleaned_response, flags=re.IGNORECASE
    )
    cleaned_response = re.sub(r"\s*```$", "", cleaned_response).strip()
    try:
        post = json.loads(cleaned_response)
    except json.JSONDecodeError as exc:
        print("write_post: ไม่สามารถ parse JSON จาก OpenAI ได้")
        print("OpenAI raw response:")
        print(repr(raw_response))
        print(f"JSONDecodeError: {exc}")
        raise

    required_fields = ("hook", "body", "cta", "hashtags")
    missing = [field for field in required_fields if field not in post]
    if missing:
        raise ValueError(f"write_post JSON ขาดฟิลด์: {', '.join(missing)}")
    post["hook"] = re.sub(r"[\U00010000-\U0010ffff]", "", str(post["hook"])).strip()[:100]
    return post

def load_font(path: str, size: int):
    try: return ImageFont.truetype(path, size=size)
    except Exception as exc: LOG.warning("Cannot load FONT_PATH=%s: %s", path, exc); return ImageFont.load_default()

def wrap_text(draw, text: str, font, max_width: int) -> list[str]:
    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width: lines.append(current); current = word
        else: current = candidate
    return lines + ([current] if current else []) or [text]

def make_image(hook: str, image_url: str, output: Path, font_path: str) -> None:
    base = None
    if image_url:
        try: base = Image.open(http_get(image_url, stream=True).raw).convert("RGB")
        except Exception as exc: LOG.warning("News image unavailable; using gradient fallback: %s", exc)
    if base is None:
        base = Image.new("RGB", (W, H)); px = base.load()
        for y in range(H):
            for x in range(W): px[x, y] = (10 + int(20*x/W), 28 + int(35*y/H), 70 + int(90*y/H))
    scale = max(W/base.width, H/base.height); base = base.resize((int(base.width*scale), int(base.height*scale)), Image.Resampling.LANCZOS)
    left, top = (base.width-W)//2, (base.height-H)//2; canvas = base.crop((left, top, left+W, top+H)).filter(ImageFilter.GaussianBlur(.2)).convert("RGBA")
    overlay = Image.new("RGBA", (W, H)); od = ImageDraw.Draw(overlay)
    for y in range(H//2, H): od.line((0, y, W, y), fill=(0, 0, 0, int(215*(y-H//2)/(H//2))))
    canvas = Image.alpha_composite(canvas, overlay); draw = ImageDraw.Draw(canvas)
    size = 92
    while size >= 42:
        font = load_font(font_path, size); lines = wrap_text(draw, hook, font, 980)
        if len(lines) <= 3: break
        size -= 4
    line_height = size + 16; y = H - line_height*len(lines) - 70
    for line in lines:
        box = draw.textbbox((0, 0), line, font=font); x = (W-(box[2]-box[0]))//2
        draw.text((x+3, y+4), line, font=font, fill=(0,0,0,180), stroke_width=2, stroke_fill=(0,0,0,180)); draw.text((x, y), line, font=font, fill="white", stroke_width=1, stroke_fill=(0,0,0,220)); y += line_height
    output.parent.mkdir(parents=True, exist_ok=True); canvas.convert("RGB").save(output, "JPEG", quality=92, optimize=True)

def publish(image: Path, text: str, page_id: str, token: str) -> dict[str, Any]:
    version = env("FB_API_VERSION", "v23.0"); url = f"https://graph.facebook.com/{version}/{page_id}/photos"
    with image.open("rb") as f:
        r = requests.post(url, data={"access_token": token, "caption": text}, files={"source": (image.name, f, "image/jpeg")}, timeout=(10,60))
    if not r.ok: raise RuntimeError(f"Facebook API error {r.status_code}: {r.text[:500]}")
    return r.json()

def main() -> int:
    p = argparse.ArgumentParser(); p.add_argument("--dry-run", action="store_true"); p.add_argument("--state-file", default=env("STATE_FILE", "state.json")); p.add_argument("--output", default=env("OUTPUT_IMAGE", "output/latest.jpg")); args = p.parse_args()
    logging.basicConfig(level=getattr(logging, env("LOG_LEVEL", "INFO").upper(), logging.INFO), format="%(asctime)s %(levelname)s %(message)s")
    feeds = {"BBC Sport": env("RSS_BBC_URL", DEFAULT_FEEDS["BBC Sport"]), "ESPN": env("RSS_ESPN_URL", DEFAULT_FEEDS["ESPN"]), "Goal.com": env("RSS_GOAL_URL", DEFAULT_FEEDS["Goal.com"])}
    state_path = Path(args.state_file); state = load_state(state_path); posted = set(state["posted_ids"])
    items = [x for source, url in feeds.items() for x in fetch_feed(source, url) if x.id not in posted]
    if not items: LOG.info("No new news to post"); return 0
    try: scores = rank_news(items)
    except Exception as exc: LOG.exception("News ranking failed: %s", exc); return 1
    candidates = [x for x in items if scores.get(x.id, {}).get("is_worthy")]
    if not candidates: LOG.info("No news passed the worthiness threshold"); return 0
    item = max(candidates, key=lambda x: float(scores[x.id].get("score", 0)))
    try:
        post = write_post(item, scores[item.id]); output = Path(args.output); make_image(post["hook"], item.image_url, output, required_env("FONT_PATH")); tags = [str(x).strip() for x in post.get("hashtags", []) if str(x).strip()]; text = "\n\n".join([post["hook"].strip(), post["body"].strip(), post["cta"].strip(), " ".join(tags)])
        LOG.info("Selected %s | score=%s | image=%s", item.title, scores[item.id].get("score"), output)
        if args.dry_run: print(json.dumps({"item": asdict(item), "score": scores[item.id], "post": post, "caption": text, "image": str(output)}, ensure_ascii=False, indent=2)); return 0
        result = publish(output, text, required_env("FB_PAGE_ID"), required_env("FB_PAGE_TOKEN")); LOG.info("Published to Facebook: %s", result); state["posted_ids"].append(item.id); save_state(state_path, state); return 0
    except Exception as exc: LOG.exception("Post preparation/publication failed: %s", exc); return 1

if __name__ == "__main__": sys.exit(main())
