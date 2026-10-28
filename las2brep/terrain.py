"""Terreno como rejilla de alturas -> superficie NURBS en Rhino (no malla)."""
from __future__ import annotations

from typing import Optional

import numpy as np
from scipy import ndimage


def heightfield(xyz: np.ndarray, rgb: Optional[np.ndarray], cell: float = 1.0, max_cells: int = 150) -> Optional[dict]:
    """Promedia Z por celda y rellena huecos con el vecino más cercano.

    ``max_cells`` limita los puntos de control por lado; si el terreno es más
    grande, la celda crece automáticamente para que la superficie siga ligera.
    """
    if len(xyz) < 4:
        return None
    mn = xyz[:, :2].min(0)
    ext = xyz[:, :2].max(0) - mn
    cell = max(cell, float(ext.max()) / (max_cells - 1))
    nx, ny = (np.floor(ext / cell).astype(int) + 1).tolist()
    nx, ny = max(nx, 2), max(ny, 2)

    ij = np.floor((xyz[:, :2] - mn) / cell).astype(int)
    flat = ij[:, 0] * ny + ij[:, 1]
    cnt = np.bincount(flat, minlength=nx * ny)
    zsum = np.bincount(flat, weights=xyz[:, 2], minlength=nx * ny)
    empty = (cnt == 0).reshape(nx, ny)
    z = np.where(cnt > 0, zsum / np.maximum(cnt, 1), 0.0).reshape(nx, ny)
    if empty.any():
        _, (ii, jj) = ndimage.distance_transform_edt(empty, return_indices=True)
        z = z[ii, jj]

    color = None if rgb is None else [int(v) for v in np.rint(rgb.mean(0))]
    return {
        "origin": [float(mn[0]), float(mn[1])],
        "cell": float(cell),
        "nx": int(nx),
        "ny": int(ny),
        "z": z.ravel().tolist(),  # orden: i (X) lento, j (Y) rápido
        "color": color,
        "points": int(len(xyz)),
    }
