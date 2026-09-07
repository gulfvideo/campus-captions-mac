"""Regenerate winapp/icons/*.ico from the macOS app icon.

    python winapp/icons/make_icons.py        # needs Pillow

Run this only when the artwork changes; the .ico files it writes are
committed, so a Windows build needs neither Pillow nor this script.

Why not reuse macapp/icons/menubar_*.png directly: those are macOS *template*
images - solid black plus alpha, which macOS recolours to match the menu bar.
Windows does no such thing. A black tray icon is invisible on the default dark
taskbar, and a white one is invisible on a light one. So the tray icons here
are the app's own blue tile (legible against either) with a small status dot
in the corner, which is the convention Windows tray apps follow anyway.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent.parent / "macapp" / "icons" / "AppIcon.icns"

# Windows picks the nearest size for each surface: 16-32 for the tray and the
# title bar, 48 for the desktop, 256 for the large icon in Explorer and for
# the installer's own header. Shipping all of them avoids Windows scaling one
# down badly.
APP_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
TRAY_SIZES = (16, 20, 24, 32, 40, 48, 64)

# Read against both a light and a dark taskbar, and distinguishable from each
# other by shape as well as hue - a colour-blind operator should still be able
# to tell "running" from "something is wrong" at tray size.
DOT_STREAMING = (63, 191, 111, 255)     # green, matching --ok in the viewers
DOT_INACTIVE = (217, 92, 92, 255)       # red, matching --bad


def base(size: int) -> Image.Image:
    return Image.open(SOURCE).convert("RGBA").resize((size, size), Image.LANCZOS)


def with_dot(size: int, colour: tuple[int, int, int, int],
             grey_tile: bool = False) -> Image.Image:
    """The app tile with a status dot in the bottom-right corner.

    The dot is nearly half the icon and carries a dark ring, because at 16 px
    a bare coloured circle on a blue tile is a smudge. `grey_tile` drains the
    colour from the tile *before* the dot goes on - draining it afterwards
    would take the dot's own red with it, which is the whole signal.
    """
    img = base(size)
    if grey_tile:
        img = desaturate(img)
    draw = ImageDraw.Draw(img)
    d = max(6, round(size * 0.42))
    x0, y0 = size - d, size - d
    draw.ellipse((x0, y0, size - 1, size - 1), fill=(20, 22, 26, 255))
    inset = max(1, round(size * 0.05))
    draw.ellipse((x0 + inset, y0 + inset, size - 1 - inset, size - 1 - inset), fill=colour)
    return img


def desaturate(img: Image.Image) -> Image.Image:
    """Grey tile for the inactive state: the red dot says something is wrong,
    and draining the tile's colour says it at a glance even at the size where
    the dot is only a few pixels."""
    grey = img.convert("LA").convert("RGBA")
    grey.putalpha(img.getchannel("A"))
    return grey


def save(path: Path, frames: list[Image.Image]) -> None:
    frames[-1].save(path, format="ICO",
                    sizes=[(f.width, f.height) for f in frames],
                    append_images=frames[:-1])
    print(f"wrote {path.relative_to(HERE.parent.parent)}")


def main() -> None:
    save(HERE / "app.ico", [base(s) for s in APP_SIZES])
    save(HERE / "tray_idle.ico", [base(s) for s in TRAY_SIZES])
    save(HERE / "tray_streaming.ico",
         [with_dot(s, DOT_STREAMING) for s in TRAY_SIZES])
    save(HERE / "tray_inactive.ico",
         [with_dot(s, DOT_INACTIVE, grey_tile=True) for s in TRAY_SIZES])


if __name__ == "__main__":
    main()
