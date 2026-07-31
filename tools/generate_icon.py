"""Generate the Midnight llama + Wi-Fi application icons (dev-only).

Run from the repository root:

    py -3 tools/generate_icon.py
    py -3 tools/generate_icon.py --out _scratch/icon-preview

Pillow is a developer dependency; the application never imports it.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "llama_router" / "assets"
ICO_SIZES = (16, 32, 48, 64, 128, 256)


def generate_llama_wifi_icon(output_dir: Path = DEFAULT_OUT,
                             size: int = 512) -> tuple[Path, Path]:
    """Generate matching PNG and multi-resolution ICO assets."""
    if size < 256:
        raise ValueError("size must be at least 256 pixels")

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    scale = size / 512

    def p(value: int) -> int:
        return round(value * scale)

    bg_color = (10, 16, 22, 255)
    border_color = (25, 42, 58, 255)
    cyan = (6, 182, 212, 255)
    cyan_mid = (6, 182, 212, 180)
    cyan_faint = (6, 182, 212, 110)

    draw.rounded_rectangle(
        [p(12), p(12), size - p(12), size - p(12)],
        radius=p(120), fill=bg_color, outline=border_color, width=p(4))

    draw.polygon([(p(145), p(175)), (p(165), p(255)),
                  (p(135), p(255))], fill=cyan)
    draw.polygon([(p(205), p(165)), (p(225), p(245)),
                  (p(195), p(245))], fill=cyan)
    draw.polygon([
        (p(135), p(255)), (p(250), p(245)), (p(310), p(295)),
        (p(310), p(335)), (p(235), p(335)), (p(235), p(415)),
        (p(165), p(415)), (p(165), p(305)), (p(135), p(285)),
    ], fill=cyan)
    draw.ellipse([p(245), p(270), p(260), p(285)], fill=bg_color)

    ox, oy = p(280), p(250)
    draw.ellipse([p(285), p(235), p(297), p(247)], fill=cyan)
    for radius, start, end, color, width in (
        (50, 295, 355, cyan, 8),
        (90, 290, 360, cyan, 8),
        (130, 285, 365, cyan_mid, 8),
        (170, 280, 370, cyan_faint, 7),
    ):
        r = p(radius)
        draw.arc([ox - r, oy - r, ox + r, oy + r], start=start, end=end,
                 fill=color, width=max(1, p(width)))

    output_dir.mkdir(parents=True, exist_ok=True)
    png_file = output_dir / "app_icon.png"
    ico_file = output_dir / "app_icon.ico"
    img.save(png_file)
    img.save(ico_file, sizes=[(n, n) for n in ICO_SIZES if n <= size])
    return png_file, ico_file


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="output directory (default: llama_router/assets)")
    parser.add_argument("--size", type=int, default=512,
                        help="source PNG size, at least 256 (default: 512)")
    args = parser.parse_args()
    try:
        png_file, ico_file = generate_llama_wifi_icon(args.out, args.size)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"icon saved:\n- {png_file}\n- {ico_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
