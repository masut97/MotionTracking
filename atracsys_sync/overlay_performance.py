"""Bounded NumPy morphology for display-only reflector outlines.

No marker poses, camera pixels or recorded measurements are modified.
"""
import numpy as np


def outline_radius(image, x, y, scale):
    """Radius of the seeded bright component in the existing 25px neighbourhood."""
    if image is None:
        return 7.0
    height, width = image.shape[:2]
    cx, cy = int(round(x)), int(round(y))
    if not (0 <= cx < width and 0 <= cy < height):
        return 7.0
    # Retain the centre seed on tied intensities; otherwise choose the first
    # row-major maximum in the small seed window.
    sx, sy = max(0, cx - 2), max(0, cy - 2)
    seed_area = image[sy:min(height, cy + 3), sx:min(width, cx + 3)]
    peak = int(seed_area.max())
    bx, by = cx, cy
    if peak > int(image[cy, cx]):
        row, col = np.unravel_index(seed_area.argmax(), seed_area.shape)
        bx, by = sx + int(col), sy + int(row)
    threshold = max(24, int(peak * .18))
    left, top = max(0, cx - 12), max(0, cy - 12)
    mask = image[top:min(height, cy + 13), left:min(width, cx + 13)] >= threshold
    component = np.zeros(mask.shape, dtype=bool)
    if mask[by - top, bx - left]:
        component[by - top, bx - left] = True
        if mask.all():
            component = mask
        else:
            padded = np.zeros((mask.shape[0] + 2, mask.shape[1] + 2), dtype=bool)
            # Eight-connected dilation, constrained to the intensity mask.
            # Each iteration adds at least one pixel or terminates; at most 625
            # local pixels can be visited, independently of camera resolution.
            while True:
                padded[1:-1, 1:-1] = component
                horizontal = padded[:, :-2] | padded[:, 1:-1] | padded[:, 2:]
                grown = (horizontal[:-2] | horizontal[1:-1] | horizontal[2:]) & mask
                if np.array_equal(grown, component):
                    break
                component = grown
    rows, cols = np.nonzero(component)
    distance = float(np.hypot(cols + left - x, rows + top - y).max()) if len(rows) else 0.
    return min(70., max(7., (distance + .8) * scale + 2.))


def install(view_class=None):
    if view_class is None:
        from atracsys_tracker.ui.image_view import CameraImageView
        view_class = CameraImageView
    if getattr(view_class, '_fast_outline_installed', False):
        return

    def radius(self, point, scale):
        image = self._array
        cache = getattr(self, '_outline_radius_cache', None)
        if cache is None or cache[0] is not image:
            cache = (image, {})
            self._outline_radius_cache = cache
        key = (float(point.x), float(point.y), float(scale))
        if key not in cache[1]:
            if len(cache[1]) >= 1024:
                cache[1].clear()
            cache[1][key] = outline_radius(image, *key)
        return cache[1][key]

    view_class._fiducial_outline_radius = radius
    original_set_frame = getattr(view_class, 'set_frame', None)
    if original_set_frame is not None:
        def set_frame(self, *args, **kwargs):
            # Some backends may reuse the same writable image buffer.
            self._outline_radius_cache = None
            return original_set_frame(self, *args, **kwargs)
        view_class.set_frame = set_frame
    view_class._fast_outline_installed = True
