#!/usr/bin/env python3
"""Thai fan-language captions for Insight posters and videos."""
from __future__ import annotations
import re
from typing import Any

THAI_RE = re.compile(r"[\u0E00-\u0E7F]")
SCORE_RE = re.compile(r"\b(\d{1,2})\s*[-\u2013]\s*(\d{1,2})\b")
NAME_RE = re.compile(r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){0,2})\b")
BANNED_HOOKS = ("สรุปสั้น", "สรุปข่าว", "สรุปข่าวสั้น", "เกิดประเด็นร้อนในวงการลูกหนัง", "รายละเอียดอยู่ในข่าวต้นทาง", "วงการลูกหนัง")
VOICE_RULES = "เพจ: รอบรู้ : Insight เขียนภาษาแฟนบอลไทย อ่านง่ายบนมือถือ ห้ามสรุปสั้น"
TERM_TH = (("sacked", "โดนปลด"), ("sack", "ปลด"), ("in advanced talks", "คุยกันใกล้ปิด"), ("in talks", "กำลังคุยกัน"), ("medical", "ตรวจร่างกาย"), ("permanent deal", "ซื้อขาด"), ("permanent", "ซื้อขาด"), ("hat-trick", "แฮตทริก"), ("hat trick", "แฮตทริก"), ("appointed", "ได้กุนซือใหม่"), ("manager", "กุนซือ"), ("head coach", "กุนซือ"), ("injured", "บาดเจ็บ"), ("injury", "บาดเจ็บ"), ("sidelined", "ต้องพัก"), ("signs", "เซ็น"), ("signed", "เซ็นแล้ว"), ("joins", "ย้ายไป"), ("joined", "ย้ายไป"), ("transfer", "ย้ายทีม"), ("beat", "อัด"), ("beats", "อัด"), ("thrashed", "ถล่ม"), ("striker", "กองหน้า"), ("winger", "ปีก"), ("midfielder", "กองกลาง"))
CLUB_NICK = {"tottenham": ("ไก่เดือยทอง", "สเปอร์ส", "#Tottenham"), "newcastle": ("สาลิกาดง", "นิวคาสเซิล", "#Newcastle"), "liverpool": ("หงส์แดง", "ลิเวอร์พูล", "#Liverpool"), "arsenal": ("ปืนใหญ่", "อาร์เซนอล", "#Arsenal"), "chelsea": ("สิงห์บลู", "เชลซี", "#Chelsea"), "manchester city": ("เรือใบสีฟ้า", "แมนซิตี้", "#ManCity"), "man city": ("เรือใบสีฟ้า", "แมนซิตี้", "#ManCity"), "aston villa": ("วิลลา", "แอสตันวิลลา", "#AstonVilla")}
PLAYER_NICK = {"haaland": "ฮาลันด์", "saka": "ซาก้า", "isak": "ไอซัก", "son": "ซอน", "heung-min": "ซอน", "de bruyne": "เดอ บรอยน์"}
NAME_SKIP = {"The", "And", "For", "With", "From", "This", "That", "After", "Before", "Against", "Premier", "League", "United", "City", "News", "Sport", "Football", "Manager", "Coach", "Club", "Team", "Chelsea", "Arsenal", "Liverpool", "Newcastle", "Tottenham", "Aston", "Villa", "Manchester", "Erling", "Alexander", "Bukayo"}
TYPE_CTA = {"result": "สกอร์นี้แฟนมองว่าสมกับฟอร์มมั้ย?", "transfer": "ดีลนี้คุ้มมั้ย แฟนทีมไหนเฮกว่า?", "sack": "เปลี่ยนกุนซือรอบนี้สายไปหรือพอดี?", "injury": "กระทบแผนทั้งซีซั่นแค่ไหน?", "quote": "เห็นด้วยกับประโยคนี้มั้ย?", "other": "แฟนบอลมองเรื่องนี้ยังไงครับ?"}
GOOD_EXAMPLES = [{"type": "result", "hook": "ฮาลันด์ซัดแฮตทริก ซิตี้ถล่ม 4-0", "why": "แมตช์นี้เรือใบสีฟ้าทิ้งห่างฝูงอีกก้าว"}]


def has_thai(text: str) -> bool:
    return bool(THAI_RE.search(text or ""))


def _blob(item) -> str:
    return f"{getattr(item, 'title', '')} {getattr(item, 'summary', '')}"


def localize_terms(text: str) -> str:
    out = str(text or "")
    for src, th in TERM_TH:
        out = re.sub(re.escape(src), th, out, flags=re.I)
    return out


def classify_story(item) -> str:
    text = _blob(item).lower()
    if any(w in text for w in ("sack", "sacked", "appoint", "โดนปลด", "ได้กุนซือใหม่")):
        return "sack"
    if any(w in text for w in ("injur", "sidelined", "hamstring", "acl", "บาดเจ็บ", "ต้องพัก")):
        return "injury"
    if any(w in text for w in ("transfer", "sign", "signed", "joins", "loan", "ย้าย", "เซ็น", "in talks", "in advanced talks")):
        return "transfer"
    if SCORE_RE.search(text) or any(w in text for w in ("beat", "beats", "won", "wins", "thrashed", "draw", "ชนะ", "ถล่ม", "พ่าย")):
        return "result"
    return "other"


def clubs_in(item):
    text = _blob(item).lower()
    hits, seen = [], set()
    for key, row in sorted(CLUB_NICK.items(), key=lambda kv: len(kv[0]), reverse=True):
        idx = text.find(key)
        if idx >= 0 and row not in seen:
            hits.append((idx, row)); seen.add(row)
    hits.sort(key=lambda row: row[0])
    return [row for _idx, row in hits[:3]]


def players_in(item):
    names = []
    text = _blob(item).lower()
    for key, nick in PLAYER_NICK.items():
        if key in text and nick not in names:
            names.append(nick)
    for name in NAME_RE.findall(_blob(item)):
        if name.split()[0] in NAME_SKIP:
            continue
        label = PLAYER_NICK.get(name.lower()) or name
        if label not in names:
            names.append(label)
    return names[:4]


def scores_in(item):
    return [f"{a}-{b}" for a, b in SCORE_RE.findall(_blob(item))][:2]


def short_hook(text: str, limit: int = 40) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    text = re.sub(r"[\U00010000-\U0010ffff]", "", text)
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] or text[:limit]


def facts_from(item, storyboard=None):
    clubs, players, scores = clubs_in(item), players_in(item), scores_in(item)
    who = players[0] if players else (clubs[0][1] if clubs else "ทีมดัง")
    dest = ""
    blob = _blob(item).lower()
    for key, row in sorted(CLUB_NICK.items(), key=lambda kv: len(kv[0]), reverse=True):
        if re.search(rf"(join(?:s|ing)?|to|signed for|signs for|ซบ|ไป)\s+{re.escape(key)}", blob):
            dest = row[1]
            break
    if not dest and clubs:
        dest = clubs[0][1]
    return {"type": classify_story(item), "clubs": clubs, "players": players, "scores": scores, "who": who, "club_th": clubs[0][1] if clubs else "", "nick": clubs[0][0] if clubs else "", "dest": dest}


def _hook_for(facts):
    who, club, kind = facts["who"], facts["club_th"], facts["type"]
    score = facts["scores"][0] if facts["scores"] else ""
    if kind == "result" and score:
        other = facts["clubs"][1][1] if len(facts["clubs"]) > 1 else ""
        return short_hook(f"{club or who} เจอ {other} {score}" if other else f"{who} จัดไป {score}")
    if kind == "transfer":
        dest = facts.get("dest") or club
        return short_hook(f"{who} ใกล้ย้าย{(' ไป ' + dest) if dest else ''}")
    if kind == "sack":
        return short_hook(f"{club or who} เปลี่ยนกุนซือ")
    if kind == "injury":
        return short_hook(f"{who} เจ็บ ต้องพัก")
    return short_hook(f"{who} มีข่าวใหญ่")


def _why_for(facts):
    nick = facts["nick"] or facts["club_th"] or facts["who"]
    return {"result": f"แมตช์นี้{nick}ได้แต้มและโมเมนตัมทันที", "transfer": "ตลาดนักเตะขยับ เกมรุกหรือเกมรับเปลี่ยนได้ในนัดหน้า", "sack": "เปลี่ยนกุนซือรอบนี้ทั้งห้องแต่งตัวและตารางคะแนนสั่น", "injury": "แผนหมุนเวียนนักเตะทั้งเดือนอาจต้องรื้อใหม่"}.get(facts["type"], "ประเด็นนี้แฟนบอลแชร์ต่อได้ทันที")


def _latin_ratio(text: str) -> float:
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return 1.0
    return len(re.findall(r"[A-Za-z]", compact)) / max(1, len(compact))


def _bullets_for(facts):
    bits = []
    if facts["players"]:
        bits.append(f"ตัวเอกข่าวคือ {facts['players'][0]}")
    if facts["clubs"]:
        bits.append("เกี่ยวข้องกับ " + " / ".join(row[1] for row in facts["clubs"][:2]))
    if facts["scores"]:
        bits.append(f"สกอร์ในข่าว {facts['scores'][0]}")
    extra = {"transfer": "สถานะดีล: คุยกันใกล้ปิดจากข่าวต้นทาง", "sack": "สโมสรเริ่มคุยหาคนมาคุมทีมต่อ", "injury": "อาจพลาดเกมสำคัญรอบนี้"}
    if facts["type"] in extra:
        bits.append(extra[facts["type"]])
    unique = []
    for line in bits:
        line = line.strip(" .")
        if line and line not in unique:
            unique.append(line)
    while len(unique) < 3:
        unique.append("เช็กข่าวต้นทางก่อนแชร์ต่อ")
    return unique[:3]


def _hashtags_for(facts):
    tags = ["#รอบรู้Insight", "#ข่าวฟุตบอล"]
    for _n, _t, tag in facts["clubs"]:
        if tag not in tags:
            tags.append(tag)
    tags.append({"result": "#พรีเมียร์ลีก", "transfer": "#ตลาดนักเตะ", "sack": "#กุนซือ"}.get(facts["type"], "#ข่าวบอล"))
    out = []
    for tag in tags:
        if tag not in out:
            out.append(tag)
    return out[:5]


def write_caption_th(item, storyboard=None, score=None):
    facts = facts_from(item, storyboard)
    hook = short_hook(str((score or {}).get("main_angle") or "")) if score and has_thai(str((score or {}).get("main_angle") or "")) else _hook_for(facts)
    if any(bad in hook for bad in BANNED_HOOKS) or not hook:
        hook = _hook_for(facts)
    why, bullets = _why_for(facts), _bullets_for(facts)
    post = {"hook": hook, "why": why, "bullets": bullets, "body": why + "\n" + "\n".join(f"• {line}" for line in bullets), "cta": TYPE_CTA.get(facts["type"], TYPE_CTA["other"]), "hashtags": _hashtags_for(facts), "type": facts["type"], "who": facts["who"]}
    post["grade"] = grade_caption_th(post, item)
    return post


def format_facebook(post, *, with_emoji=True):
    hook = str(post.get("hook") or "").strip()
    if with_emoji and hook and not hook.startswith("🔥"):
        hook = f"🔥 {hook}"
    bullets = post.get("bullets") or []
    mid = str(post.get("body") or "").strip() if not bullets else str(post.get("why") or "").strip() + "\n\n" + "\n".join(f"• {line}" for line in bullets)
    tags = " ".join(str(tag) for tag in (post.get("hashtags") or []) if str(tag).strip())
    return "\n\n".join(part for part in (hook, mid, str(post.get("cta") or "").strip(), tags) if part).strip()


def grade_caption_th(post, item=None):
    errors, warnings = [], []
    hook = str(post.get("hook") or "").strip()
    body = str(post.get("body") or "").strip()
    why = str(post.get("why") or "").strip()
    cta = str(post.get("cta") or "").strip()
    tags = [str(tag).strip() for tag in (post.get("hashtags") or []) if str(tag).strip()]
    bullets = [str(x).strip() for x in (post.get("bullets") or []) if str(x).strip()]
    full = format_facebook(post, with_emoji=False)
    if not has_thai(full):
        errors.append("แคปชันไม่มีภาษาไทย")
    if any(bad in hook for bad in BANNED_HOOKS):
        errors.append("hook ใช้ประโยคกว้างที่ห้าม")
    if not hook or len(hook) > 48:
        errors.append("hook ว่างหรือยาวเกินมือถือ")
    if not why and not body:
        errors.append("ไม่มีบรรทัดอธิบาย")
    if len(bullets) < 2:
        warnings.append("รายละเอียดไม่ครบ 3 ข้อ")
    if not cta or ("?" not in cta and "มั้ย" not in cta and "ยังไง" not in cta):
        errors.append("ไม่มีคำถามชวนคอมเมนต์")
    if len(tags) < 3 or len(tags) > 5:
        errors.append("แฮชแท็กต้องมี 3-5 อัน")
    if "\n" not in full:
        errors.append("ไม่มีขึ้นบรรทัดใหม่ อ่านบนมือถือยาก")
    if _latin_ratio(full) > 0.45:
        errors.append("อังกฤษปนไทยมากเกิน")
    if item is not None:
        names = players_in(item) + [row[1] for row in clubs_in(item)]
        if names and not any(name in full for name in names):
            errors.append("แคปชันไม่พูดถึงทีมหรือนักเตะในข่าว")
        if scores_in(item) and not any(score in full for score in scores_in(item)):
            warnings.append("ข่าวมีสกอร์แต่แคปชันไม่ใส่")
    score = max(0, min(100, 100 - 18 * len(errors) - 6 * len(warnings)))
    return {"ok": not errors and score >= 70, "score": score, "errors": errors, "warnings": warnings}


def polish_post(item, post=None, score=None):
    built = write_caption_th(item, None, score)
    incoming = post or {}
    hook = short_hook(re.sub(r"[\U00010000-\U0010ffff]", "", str(incoming.get("hook") or "")))
    if hook and has_thai(hook) and not any(bad in hook for bad in BANNED_HOOKS):
        built["hook"] = hook
    built["body"] = built["why"] + "\n" + "\n".join(f"• {line}" for line in built["bullets"])
    built["grade"] = grade_caption_th(built, item)
    if not built["grade"]["ok"]:
        rebuilt = write_caption_th(item, None, score)
        rebuilt["grade"] = grade_caption_th(rebuilt, item)
        return rebuilt
    return built


def prompt_block() -> str:
    sample = GOOD_EXAMPLES[0]
    return VOICE_RULES + " ตัวอย่าง hook: " + sample["hook"] + " / why: " + sample["why"] + " ตอบ JSON {hook, why, bullets, cta, hashtags}"
