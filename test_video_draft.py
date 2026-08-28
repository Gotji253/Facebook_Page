from pathlib import Path
from tempfile import TemporaryDirectory
from PIL import Image
import video_draft

with TemporaryDirectory() as tmp:
    root = Path(tmp)
    scene_paths = []
    scenes = []
    for index in range(4):
        path = root / f"scene_{index + 1}.png"
        Image.new("RGB", (1024, 1536), (35 + index * 20, 80, 135)).save(path, "PNG")
        scene_paths.append(path)
        scenes.append({
            "title": f"ฉากที่ {index + 1}",
            "line": "มุกฟุตบอลแบบทดสอบ",
            "narration": "ฉากทดสอบ",
            "image_prompt": "editorial football cartoon, no text, no logos, no watermark",
        })
    output = root / "draft.mp4"
    storyboard = {"scenes": scenes, "caption": "draft test"}
    video_draft.render_video(scene_paths, storyboard, output, "fonts/NotoSansThai-Regular.ttf")
    assert output.is_file() and output.stat().st_size > 0
print("four-scene video draft render passed")
