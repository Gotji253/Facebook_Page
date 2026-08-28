from pathlib import Path
from tempfile import TemporaryDirectory
from PIL import Image
import video_draft

class LocalResponse:
    def __init__(self, path):
        self.raw = path.open("rb")

def local_http_get(url, **kwargs):
    return LocalResponse(Path(url))

with TemporaryDirectory() as tmp:
    root = Path(tmp)
    source = root / "source.jpg"
    output = root / "draft.mp4"
    Image.new("RGB", (1600, 900), (35, 80, 135)).save(source, "JPEG")
    video_draft.http_get = local_http_get
    joke = {"scenes": [
        {"title": "ข่าวมาแล้ว", "line": "ตลาดนักเตะคึกคัก", "narration": "ฉากหนึ่ง"},
        {"title": "แฟนบอลถาม", "line": "งบอยู่ไหนครับบอส", "narration": "ฉากสอง"},
        {"title": "บทสรุป", "line": "รอติดตามตอนต่อไป", "narration": "ฉากสาม"},
    ], "caption": "มุกฟุตบอลประจำวัน"}
    video_draft.render_video(str(source), joke, output, "fonts/NotoSansThai-Regular.ttf")
    assert output.is_file() and output.stat().st_size > 0
print("video draft render passed")
