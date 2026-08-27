from pathlib import Path
from tempfile import TemporaryDirectory
from PIL import Image
from football_poster import make_image, validate_image_file

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
print("image guard tests passed")
