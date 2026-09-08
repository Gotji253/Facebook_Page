#!/usr/bin/env python3
from types import SimpleNamespace
import news_grade as ng


def item(title, summary=""):
    return SimpleNamespace(id="x", title=title, summary=summary, published="2026-09-08")


def test_sharp_result_passes():
    row = ng.finalize(item("Haaland hat-trick as Manchester City beat Arsenal 4-0", "City thrashed Arsenal."), None, ng.POSTER_MIN)
    assert row["sharp"] and row["is_worthy"], row


def test_sharp_transfer_passes():
    row = ng.finalize(item("Isak close to signing for Arsenal from Newcastle", "Permanent deal talks."), None, ng.VIDEO_MIN)
    assert row["sharp"], row


def test_sack_is_sharp():
    row = ng.finalize(item("Chelsea sack manager after Aston Villa defeat"), None, ng.POSTER_MIN)
    assert row["sharp"] and not row["dull"], row


def test_quiz_is_dull():
    row = ng.finalize(item("Premier League quiz: name the hat-trick heroes"), None, ng.POSTER_MIN)
    assert row["dull"] and not row["is_worthy"]
    assert row["score"] <= 40


def test_preview_without_news_skipped():
    row = ng.finalize(item("Premier League preview: what to watch this weekend"), None, ng.POSTER_MIN)
    assert not row["is_worthy"]


def test_gossip_roundup_skipped():
    row = ng.finalize(item("Transfer rumours and gossip: Friday paper talk"), None, ng.POSTER_MIN)
    assert row["gossip"] and not row["is_worthy"]


def test_learned_feature_skipped():
    row = ng.finalize(item("Five things we learned from the weekend talking points"), None, ng.POSTER_MIN)
    assert not row["is_worthy"]


def test_empty_day_picks_nothing():
    items, scores = [], {}
    for i, title in enumerate(("Fantasy football tips", "Premier League preview: talking points", "Quiz: guess the club")):
        it = item(title)
        it.id = f"n{i}"
        items.append(it)
        scores[it.id] = ng.finalize(it, None, ng.POSTER_MIN)
    assert ng.pick(items, scores, ng.POSTER_MIN) is None


if __name__ == "__main__":
    tests = [test_sharp_result_passes, test_sharp_transfer_passes, test_sack_is_sharp, test_quiz_is_dull, test_preview_without_news_skipped, test_gossip_roundup_skipped, test_learned_feature_skipped, test_empty_day_picks_nothing]
    failed = 0
    for fn in tests:
        try:
            fn(); print("PASS", fn.__name__)
        except Exception as exc:
            failed += 1; print("FAIL", fn.__name__, exc)
    print(f"{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(failed)
