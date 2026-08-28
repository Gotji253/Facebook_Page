from pathlib import Path
from tempfile import TemporaryDirectory
from PIL import Image
import football_poster
from football_poster import NewsItem, make_image, validate_image_file, choose_template

class LocalResponse:
    def __init__(self, path):
        self.raw = path.open("rb")

def local_http_get(url, **kwargs):
    return LocalResponse(Path(url))

football_poster.http_get = local_http_get

item = NewsItem(
    "test-id", "Test", "Chelsea vs Arsenal: record 5 goals in match",
    "สถิติ 5 ประตูในนัดล่าสุด ทีมพบกันในรอบชิง", "https://example.com/news", "", ""
)

with TemporaryDirectory() as tmp:
    source = Path(tmp) / "source.jpg"
    Image.new("RGB", (1600, 900), (36, 76, 120)).save(source, "JPEG")
    for template in ("news", "stats", "match"):
        output = Path(tmp) / f"{template}.jpg"
        make_image("เชลซีปิดดีล! ข่าวฟุตบอลล่าสุด", str(source), output, "fonts/NotoSansThai-Regular.ttf", template, item)
        validate_image_file(output)
        with Image.open(output) as image:
            assert image.size == (1200, 630)
    assert choose_template(item) == "stats"
print("all image templates passed")
