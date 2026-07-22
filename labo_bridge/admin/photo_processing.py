"""
Automatic background removal for uploaded machine photos.

Every existing machine photo (xn330, ismart, selectra, cyanvision, xs500i)
is a product/spec-sheet style shot on a plain white/near-white background,
already saved as a transparent PNG so it blends cleanly into the machine
card regardless of theme (see style.css's .card-photo-frame). A photo
uploaded through Add Analyzer / Edit machine with its background still
intact (e.g. a straight product photo saved as .jpg) would show as a
visible white/colored box on the card instead of matching that look -
this is exactly what happened with the Mini VIDAS photo (2026-07-21),
fixed at the time by a one-off manual script. This module automates that
same fix for every future upload.

Method: flood-fill from the four corners, treating near-white pixels as
background and making them transparent. This works well for the kind of
photo every machine here has used so far (simple, mostly-uniform light
background) - it is NOT true subject-extraction/AI background removal, so
a photo with a busy, dark, or non-uniform background may not process
cleanly. If a photo looks wrong after upload, replacing it with a cleaner
source photo (or asking for a manual fix) is the fallback, same as before
this existed.
"""

import collections
import io

from PIL import Image

# How far from pure white (255,255,255) a pixel can be and still count as
# "background" - confirmed against the Mini VIDAS photo's actual pixels
# (drop-shadow gradient near the edges is a few shades off pure white but
# still clearly background, not part of the machine itself).
BACKGROUND_TOLERANCE = 18


def _is_background_color(r: int, g: int, b: int, tol: int = BACKGROUND_TOLERANCE) -> bool:
    return r > 255 - tol and g > 255 - tol and b > 255 - tol


def remove_background(image_bytes: bytes) -> bytes:
    """
    Take raw image bytes (any format Pillow can read - jpg/png/webp/etc.),
    flood-fill transparent from the four corners over near-white pixels,
    return PNG bytes with a real alpha channel. If anything goes wrong
    (unreadable image, unexpected format), returns the original bytes
    unchanged rather than raising - a failed background removal should
    never block the actual upload/save.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    except Exception:
        return image_bytes

    w, h = img.size
    px = img.load()

    visited = bytearray(w * h)

    def idx(x, y):
        return y * w + x

    start_points = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
    queue = collections.deque(start_points)
    for x, y in start_points:
        visited[idx(x, y)] = 1

    while queue:
        x, y = queue.popleft()
        r, g, b, a = px[x, y]
        if not _is_background_color(r, g, b):
            continue
        px[x, y] = (r, g, b, 0)
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and not visited[idx(nx, ny)]:
                visited[idx(nx, ny)] = 1
                nr, ng, nb, na = px[nx, ny]
                if _is_background_color(nr, ng, nb):
                    queue.append((nx, ny))

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()
