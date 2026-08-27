from pathlib import Path
from tempfile import TemporaryDirectory
from PIL import Image
import football_poster
from football_poster import NewsItem, make_image, validate_image_file

with TemporaryDirectory() as tmp:
    output = Path(tmp) / "post.jpg"
    make_image("ทดสอบข่าวฟุตบอล", "", output, "missing-font.ttf")
    validate_image_file(output)
    with Image.open(output) as image:
        assert image.format == "JPEG"
        assert image.size == (1200, 630)
    try:
        validate_image_file(Path(tmp) / "missing.jpg")
    except RuntimeError:
        pass
    else:
        raise AssertionError("missing image must be rejected")
class FakeResponse:
    def json(self):
        return {"results": [{
            "url": "https://images.example.com/football.jpg",
            "width": 1600,
            "height": 900,
            "title": "Football match",
            "creator": "Test Creator",
            "license": "CC BY",
            "foreign_landing_url": "https://example.com/source",
        }]}


def fake_http_get(*args, **kwargs):
    return FakeResponse()


original_http_get = football_poster.http_get
football_poster.http_get = fake_http_get
try:
    image = football_poster.search_openverse(
        NewsItem("id", "test", "Football match", "summary", "https://example.com/news")
    )
    assert image[0] == "https://images.example.com/football.jpg"
    assert image[1] == "Openverse"
finally:
    football_poster.http_get = original_http_get

print("image guard and Openverse tests passed")
