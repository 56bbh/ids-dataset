"""Create three layout variants from the accumulated confusion-matrix PNGs."""

from pathlib import Path

from PIL import Image


SOURCE_DIR = Path(__file__).parent / "summary_10runs" / "confusion_matrix_figures"
FILES = (
    "lstm_accumulated_confusion_matrix_percent.png",
    "resnet1d_accumulated_confusion_matrix_percent.png",
    "transformer_accumulated_confusion_matrix_percent.png",
)
GAP = 24


def paste(canvas: Image.Image, image: Image.Image, x: int, y: int) -> None:
    """Paste an image while preserving transparency, if present."""
    canvas.alpha_composite(image, (x, y))


def main() -> None:
    images = [Image.open(SOURCE_DIR / filename).convert("RGBA") for filename in FILES]
    width = max(image.width for image in images)
    height = max(image.height for image in images)

    # Normalize dimensions so all variants have consistent alignment.
    normalized = []
    for image in images:
        panel = Image.new("RGBA", (width, height), "white")
        paste(panel, image, (width - image.width) // 2, (height - image.height) // 2)
        normalized.append(panel)

    horizontal = Image.new("RGBA", (3 * width + 2 * GAP, height), "white")
    for index, image in enumerate(normalized):
        paste(horizontal, image, index * (width + GAP), 0)
    horizontal.convert("RGB").save(SOURCE_DIR / "confusion_matrices_horizontal.png")

    vertical = Image.new("RGBA", (width, 3 * height + 2 * GAP), "white")
    for index, image in enumerate(normalized):
        paste(vertical, image, 0, index * (height + GAP))
    vertical.convert("RGB").save(SOURCE_DIR / "confusion_matrices_vertical.png")

    two_plus_one = Image.new("RGBA", (2 * width + GAP, 2 * height + GAP), "white")
    paste(two_plus_one, normalized[0], 0, 0)
    paste(two_plus_one, normalized[1], width + GAP, 0)
    paste(two_plus_one, normalized[2], (width + GAP) // 2, height + GAP)
    two_plus_one.convert("RGB").save(SOURCE_DIR / "confusion_matrices_2_plus_1.png")


if __name__ == "__main__":
    main()
