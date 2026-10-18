"""Exercise the staged V1 plotter and inspect actual figure annotations and PNGs."""

from pathlib import Path
from preview import preview
import seismic.plotting as plotting


labels = []
save_original = plotting.save_png_svg


def capture_and_save(fig, *args, **kwargs):
    labels.extend(text.get_text() for ax in fig.axes for text in ax.texts)
    return save_original(fig, *args, **kwargs)


plotting.save_png_svg = capture_and_save
output = Path(__file__).resolve().parent.parent / "image_annotation_test_output"
result = preview(Path("config.json"), output)
assert "x=30, z=24" in labels, labels
assert len(result["png"]) == len(result["svg"]) == 4
assert all(Path(p).read_bytes().startswith(b"\x89PNG\r\n\x1a\n") for p in result["png"])
print("Image annotation verification passed: x=30, z=24; 4 PNG and 4 SVG")
