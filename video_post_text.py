from __future__ import annotations
import json
import logging
import re
import video_draft as vd
from ai_client import chat_json
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
SKIP_NEWS = (
    "quiz", "quizzes", "puzzle", "crossword", "podcast", "newsletter",
    "fantasy football", "predictor", "prediction game", "daily quiz",
    "flex your football", "test your", "brain teaser", "trivia",
    "live text", "as it happened", "gossip", "rumour mill", "transfer rumours",
    "iplayer", "match of the day", "motd", "watch live", "live stream",
)
KEEP_NEWS = (
    "football", "soccer", "premier league", "la liga", "bundesliga", "serie a",
    "ligue 1", "champions league", "europa league", "world cup", "euros",
    "transfer", "midfielder", "striker", "goalkeeper", "winger", "manager",
    "sacked", "signed", "hat-trick", "match", "goal", "fixture",
    "ฟุตบอล", "บอล", "พรีเมียร์", "ชามเปียนส์", "ย้ายทีม",
)
GENERIC_FALLBACK = (
    "เกิดประเด็นร้อนในวงการลูกหนัง",
    "รายละเอียดอยู่ในข่าวต้นทาง",
)
NAME_SKIP = {
    "The", "And", "For", "With", "From", "This", "That", "After", "Before",
    "Against", "Premier", "League", "United", "City", "News", "Sport",
    "Football", "Saturday", "Sunday", "Monday", "Tuesday", "Wednesday",
    "Thursday", "Friday", "Live", "Watch", "How", "Why", "What", "When",
}
CLUB_MAP = {
    "tottenham": "Tottenham Hotspur",
    "spurs": "Tottenham Hotspur",
    "hotspur": "Tottenham Hotspur",
    "สเปอร์ส": "Tottenham Hotspur",
    "ไก่เดือยทอง": "Tottenham Hotspur",
    "newcastle": "Newcastle United",
    "นิวคาส": "Newcastle United",
    "สาลิกาดง": "Newcastle United",
    "liverpool": "Liverpool F.C.",
    "ลิเวอร์พูล": "Liverpool F.C.",
    "arsenal": "Arsenal F.C.",
    "อาร์เซนอล": "Arsenal F.C.",
    "chelsea": "Chelsea F.C.",
    "เชลซี": "Chelsea F.C.",
    "manchester city": "Manchester City",
    "man city": "Manchester City",
    "แมนซิตี้": "Manchester City",
    "manchester united": "Manchester United",
    "man united": "Manchester United",
    "แมนยู": "Manchester United",
    "barcelona": "FC Barcelona",
    "บาร์ซา": "FC Barcelona",
    "บาร์เซโลนา": "FC Barcelona",
    "real madrid": "Real Madrid",
    "เรอัลมาดริด": "Real Madrid",
    "bayern": "Bayern Munich",
    "บาเยิร์น": "Bayern Munich",
    "psg": "Paris Saint-Germain",
    "เปแอสเช": "Paris Saint-Germain",
    "milan": "AC Milan",
    "มิลาน": "AC Milan",
    "inter": "Inter Milan",
    "อินเตอร์": "Inter Milan",
    "juventus": "Juventus",
    "ยูเวนตุส": "Juventus",
    "dortmund": "Borussia Dortmund",
    "ดอร์ทมุนด์": "Borussia Dortmund",
    "atletico": "Atletico Madrid",
    "แอตเลติโก": "Atletico Madrid",
    "west ham": "West Ham United",
    "เวสต์แฮม": "West Ham United",
    "aston villa": "Aston Villa",
    "แอสตันวิลลา": "Aston Villa",
    "brighton": "Brighton & Hove Albion",
    "ไบรท์ตัน": "Brighton & Hove Albion",
    "everton": "Everton F.C.",
    "เอฟเวอร์ตัน": "Everton F.C.",
}
THAI_BREAKS = (
    "ขณะที่", "ทำให้", "พร้อม", "เพราะ", "หลังจาก", "ก่อนที่",
    "โดยที่", "แต่", "โดย", "และ", "กับ", "จาก", "ของ", "ที่",
    "ใน", "เพื่อ", "จน", "เลย", "ยัง", "จะ", "ได้", "ไม่", "กว่า",
)


def has_thai(text: str) -> bool:
    return bool(THAI_RE.search(text or ""))


def news_text(item) -> str:
    return f"{getattr(item, 'title', '')} {getattr(item, 'summary', '')} {getattr(item, 'url', '')}".lower()


def is_football_news(item) -> bool:
    text = news_text(item)
    if any(word in text for word in SKIP_NEWS):
        return False
    return any(word in text for word in KEEP_NEWS)


def one_story(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return STORY_SPLIT.split(text, maxsplit=1)[0].strip(" ,.")


def story_tokens(text: str) -> set[str]:
    found = set(re.findall(r"[\u0E00-\u0E7F]{3,}|[A-Za-z]{4,}", text or ""))
    return {token.lower() for token in found if token not in STOP_TOKENS}


def thai_clusters(text: str) -> list[str]:
    clusters: list[str] = []
    for char in text:
        if clusters and THAI_MARK.fullmatch(char):
            clusters[-1] += char
        else:
            clusters.append(char)
    return clusters


def complete_phrase(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text.rstrip(" ,;:-/")
    acc = ""
    for cluster in thai_clusters(text):
        if acc and len(acc) + len(cluster) > limit:
            break
        acc += cluster
    rest = text[len(acc):]
    if rest and not acc.endswith(" ") and not rest[0].isspace():
        extra = re.match(r"\S{1,20}", rest)
        if extra:
            acc += extra.group(0)
    elif " " in acc and not acc.endswith(" "):
        acc = acc.rsplit(" ", 1)[0]
    return acc.rstrip(" ,;:-/")


def finish_last_word(short: str, long: str, limit: int) -> str:
    short = re.sub(r"\s+", " ", str(short or "")).strip()
    long = re.sub(r"\s+", " ", str(long or "")).strip()
    if not short:
        return complete_phrase(long, limit)
    if long.startswith(short):
        rest = long[len(short):]
        if rest and not short.endswith(" ") and not rest[0].isspace():
            more = re.match(r"\S+", rest)
            if more:
                short = short + more.group(0)
        return complete_phrase(short, limit + 12)
    last = short.split()[-1]
    for token in long.split():
        if token.startswith(last) and len(token) > len(last):
            rebuilt = short[: short.rfind(last)] + token
            return complete_phrase(rebuilt, limit + 12)
    return complete_phrase(short, limit)


HANGING_TAILS = {
    "สุด", "อย่าง", "ด้วย", "เพื่อ", "ที่", "ของ", "และ", "จะ", "ได้", "ไม่",
    "กับ", "จาก", "ส่วน", "ยัง", "ให้", "มา", "ไป", "อยู่", "แล้ว", "กว่า",
    "แบบ", "ความ", "การ", "ใน", "ต่อ", "หลัง", "เพราะ", "แต่", "เลย", "จน",
}


def next_source_token(short: str, long: str) -> str:
    short = re.sub(r"\s+", " ", short or "").strip()
    long = re.sub(r"\s+", " ", long or "").strip()
    if long.startswith(short):
        rest = long[len(short):].lstrip()
        match = re.match(r"\S+", rest)
        return match.group(0) if match else ""
    tokens = long.split()
    parts = short.split()
    if not parts:
        return tokens[0] if tokens else ""
    for index, token in enumerate(tokens):
        if token == parts[-1] and index + 1 < len(tokens):
            if parts == tokens[index + 1 - len(parts): index + 1]:
                return tokens[index + 1]
    return ""


def finish_phrase(short: str, long: str, limit: int) -> str:
    text = finish_last_word(short, long, limit)
    while text:
        last = text.split()[-1]
        if last not in HANGING_TAILS:
            break
        nxt = next_source_token(text, long)
        if nxt:
            candidate = f"{text} {nxt}".strip()
            if len(candidate) <= limit + 24:
                text = candidate
                continue
        if " " in text:
            text = text.rsplit(" ", 1)[0]
        break
    return text.strip()


def source_scores(item) -> list[str]:
    source = f"{getattr(item, 'title', '')} {getattr(item, 'summary', '')}"
    scores = []
    for score in SCORE_RE.findall(source):
        compact = re.sub(r"\s+", "", score).replace("\u2013", "-")
        if compact not in scores:
            scores.append(compact)
    return scores


def with_score(text: str, scores: list[str], limit: int) -> str:
    compact = re.sub(r"\s+", "", text or "")
    for score in scores:
        if score not in compact and score.replace("-", "") not in compact:
            text = complete_phrase(f"{text} {score}".strip(), limit + 8)
            compact = re.sub(r"\s+", "", text)
    return text


def first_sentence(text: str, limit: int = 56) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return ""
    parts = re.split(r"(?<=[ฮ.!\n])\s+", text, maxsplit=1)
    return complete_phrase(parts[0].strip(), limit)


def thai_or(text: str, fallback: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if has_thai(text) else fallback


def readable_thai(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return ""
    compact = re.sub(r"\s+", "", text)
    if compact and (len(text) - len(compact)) >= max(3, len(compact) // 10):
        return text
    spaced = text
    for word in THAI_BREAKS:
        spaced = re.sub(rf"(?<!\s)({re.escape(word)})(?!\s)", r" \1 ", spaced)
    return re.sub(r"\s+", " ", spaced).strip()


def news_names(item) -> list[str]:
    blob = f"{getattr(item, 'title', '')} {getattr(item, 'summary', '')}"
    names = []
    for name in NAME_RE.findall(blob):
        first = name.split()[0]
        if first in NAME_SKIP or name in names:
            continue
        names.append(name)
    return names[:4]


def news_clubs(item) -> list[str]:
    blob = f"{getattr(item, 'title', '')} {getattr(item, 'summary', '')}".lower()
    clubs = []
    for key, club in sorted(CLUB_MAP.items(), key=lambda row: len(row[0]), reverse=True):
        if key in blob and club not in clubs:
            clubs.append(club)
    return clubs[:3]


def photo_queries(item) -> list[str]:
    names = news_names(item)
    clubs = news_clubs(item)
    queries: list[str] = []
    for name in names:
        queries.append(f"{name} footballer")
        queries.append(f"{name} football")
        for club in clubs[:2]:
            queries.append(f"{name} {club}")
    for club in clubs:
        queries.append(f"{club} F.C.")
        queries.append(f"{club} football club")
    words = [word for word in re.findall(r"[A-Za-z][A-Za-z'\-]{2,}", f"{item.title} {item.summary}") if word not in NAME_SKIP]
    if words:
        queries.append(" ".join(words[:4]) + " football")
    seen: set[str] = set()
    unique = []
    for query in queries:
        key = query.lower()
        if key not in seen:
            seen.add(key)
            unique.append(query)
    return unique[:10]


def scene_visible(scene: dict) -> str:
    return " ".join(str(scene.get(key) or "").strip() for key in ("title", "line")).strip()


def text_key(text: str) -> str:
    text = re.sub(r"เกิดอะไรขึ้น", "", str(text or ""))
    return re.sub(r"\s+", "", text)


def same_story(scene1: str, scene2: str) -> bool:
    left = story_tokens(scene1)
    right = story_tokens(scene2)
    return bool(left and right and (left & right))


def copied_scene(scene1: str, scene2: str) -> bool:
    left = text_key(scene1)
    right = text_key(scene2)
    if not left or not right:
        return True
    if left == right or left in right or right in left:
        return True
    tokens_left = story_tokens(scene1)
    tokens_right = story_tokens(scene2)
    if tokens_left and tokens_right:
        overlap = len(tokens_left & tokens_right) / max(1, min(len(tokens_left), len(tokens_right)))
        if overlap >= 0.8:
            return True
    return False


def source_detail(item) -> str:
    names = news_names(item)
    clubs = news_clubs(item)
    scores = source_scores(item)
    blob = f"{getattr(item, 'title', '')} {getattr(item, 'summary', '')}"
    lowered = blob.lower()
    bits: list[str] = []
    if names and any(word in lowered for word in ("sold", "signed", "joined", "transfer", "reinvest")):
        dest = clubs[1] if len(clubs) > 1 else (clubs[0] if clubs else "")
        move = f"{names[0]} ย้ายทีม"
        if dest:
            move += f" ไปเกี่ยวข้องกับ {dest}"
        bits.append(move)
        if any(word in lowered for word in ("reinvest", "money", "fee")):
            bits.append("สโมสรนำเงินไปเสริมทีมต่อ")
    elif names:
        bits.append(f"ประเด็นหลักอยู่ที่ {names[0]}")
        if clubs:
            bits.append(f"กับ {clubs[0]}")
    if scores:
        if len(clubs) >= 2:
            bits.append(f"เกมล่าสุด {clubs[0]} เจอ {clubs[1]} สกอร์ {scores[0]}")
        else:
            bits.append(f"ผลสกอร์ {scores[0]}")
    elif clubs:
        bits.append(f"รายละเอียดเกมของ {clubs[0]}")
    text = " ".join(bits).strip() or "รายละเอียดเพิ่มจากข่าวต้นทางยังอยู่ในประเด็นเดียวกัน"
    return readable_thai(complete_phrase(text, 140))


def tidy_wrap_lines(lines: list[str]) -> list[str]:
    cleaned = [line.strip() for line in lines if line and line.strip()]
    while len(cleaned) >= 2 and len(re.sub(r"\s+", "", cleaned[-1])) <= 6:
        prev, last = cleaned[-2], cleaned[-1]
        cleaned[-2:] = [prev + last]
    return [line for line in cleaned if line]


def looks_truncated(text: str) -> bool:
    text = (text or "").strip()
    if len(text) < 8:
        return True
    if text[-1] in ".!ฮ?…":
        return False
    last = text.split()[-1] if text.split() else text
    if re.fullmatch(r"\d{1,2}[-–]\d{1,2}", last):
        return False
    if last in HANGING_TAILS:
        return True
    if 1 <= len(re.sub(r"\s+", "", last)) <= 3 and text[-1] not in ".!ฮ?…":
        return True
    return False
