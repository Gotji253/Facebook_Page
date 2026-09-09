#!/usr/bin/env python3
from types import SimpleNamespace
import image_pick as ip


def test_rejects_logo():
    assert ip.looks_bad("https://cdn.example.com/club-logo.png", "crest")
    assert ip.score_photo("https://cdn.example.com/badge-icon.png", 1600, 900, "logo") == 0


def test_rejects_tiny_and_banner():
    item = SimpleNamespace(title="Haaland City", summary="")
    assert ip.score_photo("https://ichef.bbci.co.uk/news/haaland.jpg", 400, 300, "Haaland", item, poster=True) == 0
    assert ip.score_photo("https://ichef.bbci.co.uk/news/haaland.jpg", 2000, 400, "Haaland", item, poster=True) == 0


def test_prefers_match_photo():
    item = SimpleNamespace(title="Erling Haaland Manchester City beat Arsenal", summary="")
    weak = ip.score_photo("https://upload.wikimedia.org/stadium.jpg", 1600, 900, "Stadium", item, poster=True)
    strong = ip.score_photo("https://ichef.bbci.co.uk/news/haaland-city.jpg", 1600, 900, "Haaland City", item, poster=True)
    assert strong > weak > 0


def test_best_candidate_picks_relevant():
    item = SimpleNamespace(title="Isak Arsenal", summary="")
    rows = [
        {"url": "https://cdn.example.com/logo-arsenal.png", "width": 1600, "height": 900, "title": "logo", "item": item},
        {"url": "https://ichef.bbci.co.uk/news/isak-arsenal.jpg", "width": 1400, "height": 788, "title": "Isak Arsenal", "item": item},
        {"url": "https://cdn.example.com/random-crowd.jpg", "width": 1000, "height": 600, "title": "crowd", "item": item},
    ]
    best = ip.best_candidate(rows, poster=True)
    assert best and "isak" in best["url"]


if __name__ == "__main__":
    for fn in (test_rejects_logo, test_rejects_tiny_and_banner, test_prefers_match_photo, test_best_candidate_picks_relevant):
        fn()
        print("PASS", fn.__name__)
    print("4/4 passed")
