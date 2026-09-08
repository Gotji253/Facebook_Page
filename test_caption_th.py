#!/usr/bin/env python3
from __future__ import annotations

from types import SimpleNamespace

import caption_th as cap


def item(**kwargs):
    data = {
        "id": "t1",
        "title": "",
        "summary": "",
        "source": "BBC Sport",
        "url": "https://example.com/news",
    }
    data.update(kwargs)
    return SimpleNamespace(**data)


def test_localize_and_classify():
    news = item(
        title="Erling Haaland hat-trick as Manchester City beat Arsenal 4-0",
        summary="Haaland scored three and De Bruyne created two in a Premier League thrashing.",
    )
    assert cap.classify_story(news) == "result"
    assert "ฮาลันด์" in cap.players_in(news)
    clubs = [row[1] for row in cap.clubs_in(news)]
    assert "แมนซิตี้" in clubs
    assert "อาร์เซนอล" in clubs
    assert cap.scores_in(news) == ["4-0"]
    assert "แฮตทริก" in cap.localize_terms(news.title)


def test_transfer_template_passes_grade():
    news = item(
        title="Alexander Isak close to joining Arsenal from Newcastle in advanced talks",
        summary="The striker is in advanced talks. A medical could follow if the clubs agree a permanent deal.",
    )
    post = cap.write_caption_th(news)
    grade = cap.grade_caption_th(post, news)
    text = cap.format_facebook(post)
    assert post["type"] == "transfer"
    assert "อาร์เซนอล" in post["hook"]
    assert "นิวคาสเซิล" not in post["hook"]
    assert grade["ok"], grade
    assert "ไอซัก" in text or "Isak" not in post["hook"]
    assert "• " in text
    assert text.count("#") >= 3
    assert "สรุปสั้น" not in text
    assert "เกิดประเด็นร้อน" not in text
    assert "?" in text or "มั้ย" in text


def test_sacked_manager_uses_fan_words():
    news = item(
        title="Chelsea sack manager after Aston Villa defeat",
        summary="Chelsea have sacked their manager following the loss. Talks to appoint a new head coach have started.",
    )
    post = cap.write_caption_th(news)
    text = cap.format_facebook(post, with_emoji=False)
    assert post["type"] == "sack"
    assert "เชลซี" in text
    assert "แอสตันวิลลา เปลี่ยนกุนซือ" not in post["hook"]
    assert "โดนปลด" in cap.localize_terms(news.title) or "กุนซือ" in text
    assert post["grade"]["ok"], post["grade"]
    assert "ถูกปลดออกจากตำแหน่งผู้จัดการทีม" not in text


def test_rejects_generic_english_caption():
    news = item(
        title="Liverpool sign midfielder in permanent deal",
        summary="Liverpool have signed a midfielder on a permanent deal from Serie A.",
    )
    bad = {
        "hook": "Breaking news in world football",
        "why": "Details in the source article",
        "body": "Details in the source article",
        "bullets": ["source article"],
        "cta": "Comment",
        "hashtags": ["#news"],
    }
    grade = cap.grade_caption_th(bad, news)
    assert not grade["ok"]
    assert grade["errors"]


def test_polish_repairs_bad_llm_output():
    news = item(
        title="Son Heung-min scores as Tottenham beat Newcastle 2-1",
        summary="Son scored the winner after Newcastle equalised late in the Premier League.",
    )
    polished = cap.polish_post(
        news,
        {
            "hook": "สรุปสั้น",
            "body": "วงการลูกหนังเกิดประเด็นร้อน",
            "cta": "ok",
            "hashtags": ["a"],
        },
    )
    assert polished["grade"]["ok"], polished["grade"]
    assert "สรุปสั้น" not in polished["hook"]
    text = cap.format_facebook(polished)
    assert "ไก่เดือยทอง" in text or "สเปอร์ส" in text or "Tottenham" in " ".join(polished["hashtags"])
    assert "2-1" in text


def test_injury_and_prompt_block():
    news = item(
        title="Bukayo Saka injured and sidelined for Arsenal",
        summary="The winger picked up a hamstring injury and could miss upcoming fixtures.",
    )
    post = cap.write_caption_th(news)
    assert post["type"] == "injury"
    assert post["grade"]["ok"], post["grade"]
    block = cap.prompt_block()
    assert "รอบรู้ : Insight" in block
    assert "hook" in block


def run() -> int:
    tests = [
        test_localize_and_classify,
        test_transfer_template_passes_grade,
        test_sacked_manager_uses_fan_words,
        test_rejects_generic_english_caption,
        test_polish_repairs_bad_llm_output,
        test_injury_and_prompt_block,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL  {fn.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run())
