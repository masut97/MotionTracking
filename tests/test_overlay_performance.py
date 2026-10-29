import math
from types import SimpleNamespace

import numpy as np
import pytest

from atracsys_sync.overlay_performance import install, outline_radius


def reference(image, x, y, scale):
    """Independent eight-neighbour component traversal for display equivalence."""
    height, width = image.shape
    cx, cy = round(x), round(y)
    if not (0 <= cx < width and 0 <= cy < height):
        return 7.
    seed, peak = (cx, cy), int(image[cy, cx])
    for row in range(max(0, cy-2), min(height, cy+3)):
        for col in range(max(0, cx-2), min(width, cx+3)):
            if int(image[row, col]) > peak:
                peak, seed = int(image[row, col]), (col, row)
    threshold = max(24, int(peak*.18))
    todo, seen, radius = [seed], set(), 0.
    while todo:
        col, row = todo.pop()
        if (col, row) in seen:
            continue
        seen.add((col, row))
        if int(image[row, col]) < threshold:
            continue
        radius = max(radius, math.hypot(col-x, row-y))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                nx, ny = col+dx, row+dy
                if ((dx or dy) and 0 <= nx < width and 0 <= ny < height
                        and abs(nx-cx) <= 12 and abs(ny-cy) <= 12):
                    todo.append((nx, ny))
    return min(70., max(7., (radius+.8)*scale+2))


@pytest.mark.parametrize('seed', range(8))
def test_random_disconnected_and_edge_components_match_reference(seed):
    rng = np.random.default_rng(seed)
    for low in (0, 20, 100):
        image = rng.integers(low, 256, (33, 41), dtype=np.uint8)
        for x, y in ((20.3, 16.7), (.1, .2), (39.6, 31.8), (-2., 2.)):
            for scale in (.3, 1., 10.):
                assert outline_radius(image, x, y, scale) == pytest.approx(reference(image, x, y, scale))


def test_diagonal_connectivity_and_tied_seed():
    image = np.zeros((31, 31), dtype=np.uint8)
    np.fill_diagonal(image, 255)
    image[13, 17] = 255  # Equal, disconnected seed must not override centre.
    assert outline_radius(image, 15, 15, 2) == pytest.approx(reference(image, 15, 15, 2))
    assert outline_radius(None, 1, 1, 1) == 7.


def test_cache_invalidation_on_new_frame_and_zoom():
    class View:
        def set_frame(self, image):
            self._array = image
    install(View)
    view = View()
    view._array = np.full((40, 40), 255, dtype=np.uint8)
    point = SimpleNamespace(x=20, y=20)
    a = view._fiducial_outline_radius(point, 1)
    assert view._fiducial_outline_radius(point, 2) > a
    view._array = np.zeros((40, 40), dtype=np.uint8)
    assert view._fiducial_outline_radius(point, 1) == 7
    view._array[:] = 255
    view.set_frame(view._array)
    assert view._fiducial_outline_radius(point, 1) == a
