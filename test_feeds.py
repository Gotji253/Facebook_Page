from football_poster import DEFAULT_FEEDS, fetch_feed

for source, url in DEFAULT_FEEDS.items():
    items = fetch_feed(source, url)
    print(f"{source}: {len(items)} items; images={sum(bool(item.image_url) for item in items)}")
    if items:
        print(f"  sample={items[0].title[:80]}")
