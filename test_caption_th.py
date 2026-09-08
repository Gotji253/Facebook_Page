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
