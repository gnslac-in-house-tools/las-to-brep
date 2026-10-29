"""Terreno como rejilla de alturas -> superficie NURBS en Rhino (no malla).

Dos caminos:

- ``heightfield``: a partir de la nube ya reducida a voxeles (puntos de clase 2).
- ``terrain_from_las``: modo ``--terrain``. Lee el LAS completo por bloques sin
  voxeles y no depende de la clasificación. Puede quedarse solo con el suelo
  (filtro morfológico tipo SMRF) y genera una textura vista desde arriba con el
  color del escaneo.

En las dos, el valor de cada celda se publica en el centro de la celda.
"""
from __future__ import annotations

import math
from typing import Callable, Iterable, Optional

import laspy
import numpy as np
from scipy import ndimage
from scipy.interpolate import griddata
from scipy.spatial import QhullError

from .reader import has_color, read_chunks, to_rgb8

FINE_RES = 1.0  # m; rejilla fina para filtrar suelo y ruido
MAX_FINE = 4000  # celdas finas máx. por lado
MAX_TEXTURE = 8192  # pixeles máx. por lado
LOW_TOL = 2.0  # m bajo la mediana local para considerar un punto como ruido


def heightfield(xyz: np.ndarray, rgb: Optional[np.ndarray], cell: float = 1.0, max_cells: int = 150) -> Optional[dict]:
    """Promedia Z por celda y rellena huecos con el vecino más cercano.

    ``max_cells`` limita los puntos de control por lado; si el terreno es más
    grande, la celda crece automáticamente para que la superficie siga ligera.
    """
    if len(xyz) < 4:
        return None
    mn = xyz[:, :2].min(0)
    ext = xyz[:, :2].max(0) - mn
    cell, nx, ny = _grid(ext, cell, max_cells)

    ij = np.floor((xyz[:, :2] - mn) / cell).astype(int)
    flat = ij[:, 0] * ny + ij[:, 1]
    cnt = np.bincount(flat, minlength=nx * ny)
    zsum = np.bincount(flat, weights=xyz[:, 2], minlength=nx * ny)
    empty = (cnt == 0).reshape(nx, ny)
    z = np.where(cnt > 0, zsum / np.maximum(cnt, 1), 0.0).reshape(nx, ny)
    if empty.any():
        z = _fill_nearest(z, empty)

    color = None if rgb is None else [int(v) for v in np.rint(rgb.mean(0))]
    return {
        "origin": [float(mn[0] + cell / 2), float(mn[1] + cell / 2)],  # centro de la 1a celda
        "cell": float(cell),
        "nx": int(nx),
        "ny": int(ny),
        "z": _round(z),  # orden: i (X) lento, j (Y) rápido
        "color": color,
        "points": int(len(xyz)),
    }


def terrain_from_las(
    path: str,
    cell: float = 1.0,
    max_cells: int = 150,
    ground: bool = False,
    slope: float = 0.2,
    window: float = 40.0,
    texture_px: float = 0.5,
    base_depth: float = 5.0,
    chunk_size: int = 2_000_000,
    keep_classes: Optional[Iterable[int]] = None,
    ignore_classes: Iterable[int] = (),
    bbox: Optional[tuple] = None,
    progress: Callable[[str], None] = print,
) -> tuple:
    """Toda la nube -> una rejilla de terreno. Devuelve (origin, terreno, imagen o None).

    ``ground=True`` quita edificios y vegetación; si no, la superficie sigue lo
    que haya encima (promedio por celda). En los dos casos se descarta el ruido
    que queda muy por debajo de sus vecinos. La imagen es (alto, ancho, 3) uint8
    con el norte arriba.
    """
    with laspy.open(path) as f:
        h = f.header
        origin = np.floor(h.mins)
        lo, hi = np.array(h.mins[:2]), np.array(h.maxs[:2])
        if bbox is not None:
            lo, hi = np.maximum(lo, bbox[:2]), np.minimum(hi, bbox[2:])
        mn, ext = lo - origin[:2], hi - lo
        cell, nx, ny = _grid(ext, cell, max_cells)
        k = max(1, min(math.ceil(cell / FINE_RES), MAX_FINE // max(nx, ny)))
        res = cell / k
        fx, fy = nx * k, ny * k
        cnt = np.zeros(fx * fy)
        zsum = np.zeros(fx * fy)
        zmin = np.full(fx * fy, np.inf)

        tex = None
        if texture_px > 0 and has_color(h):
            px = max(texture_px, float(ext.max()) / MAX_TEXTURE)
            tw, th = np.maximum(np.ceil(ext / px).astype(int), 1).tolist()
            tex = {"px": px, "w": tw, "h": th, "cnt": np.zeros(tw * th), "sum": np.zeros((3, tw * th))}
        rgb_sum, rgb_max, used, total = np.zeros(3), 0, 0, int(h.point_count)

        for done, p, _, rgb in read_chunks(f, origin, chunk_size, keep_classes, ignore_classes, bbox):
            ij = np.clip(np.floor((p[:, :2] - mn) / res).astype(np.int64), 0, [fx - 1, fy - 1])
            u, inv = np.unique(ij[:, 0] * fy + ij[:, 1], return_inverse=True)
            cnt[u] += np.bincount(inv, minlength=len(u))
            zsum[u] += np.bincount(inv, weights=p[:, 2], minlength=len(u))
            np.minimum.at(zmin, u[inv], p[:, 2])
            used += len(p)
            if rgb is not None:
                rgb_max = max(rgb_max, int(rgb.max()))
                rgb_sum += rgb.sum(0)
                if tex is not None:
                    tij = np.clip(np.floor((p[:, :2] - mn) / tex["px"]).astype(np.int64), 0,
                                  [tex["w"] - 1, tex["h"] - 1])
                    u, inv = np.unique(tij[:, 0] * tex["h"] + tij[:, 1], return_inverse=True)
                    tex["cnt"][u] += np.bincount(inv, minlength=len(u))
                    for c in range(3):
                        tex["sum"][c, u] += np.bincount(inv, weights=rgb[:, c], minlength=len(u))
            progress(f"  leídos {done:,} / {total:,} puntos")

    if used < 4:
        raise ValueError("No hay puntos suficientes para el terreno (revisa --classes, --ignore o --bbox).")

    valid = (cnt > 0).reshape(fx, fy)
    if ground:
        progress(f"  filtrando suelo (rejilla de {res:.2f} m, ventana {window:g} m, pendiente {slope:g}) ...")
        zf = np.where(valid, zmin.reshape(fx, fy), np.nan)
        ok = _drop_low(zf, valid)
        ok &= ~_objects(_fill_nearest(zf, ~ok), res, slope, window)
        z = _block_mean(np.where(ok, zf, 0.0), ok.astype(float), k, nx, ny)
    else:
        zc, zs = cnt.reshape(fx, fy), zsum.reshape(fx, fy)
        ok = _drop_low(np.where(valid, zs / np.maximum(zc, 1), np.nan), valid)
        z = _block_mean(np.where(ok, zs, 0.0), np.where(ok, zc, 0.0), k, nx, ny)
    z = _fill(z)

    terrain = {
        "origin": [float(mn[0] + cell / 2), float(mn[1] + cell / 2)],  # centro de la 1a celda
        "cell": float(cell),
        "nx": int(nx),
        "ny": int(ny),
        "z": _round(z),  # orden: i (X) lento, j (Y) rápido
        "color": None if not rgb_max else to_rgb8(rgb_sum / used, rgb_max).tolist(),
        "points": int(used),
        "class": 2,
        "mode": "suelo" if ground else "superficie",
        "base": round(float(z.min()) - base_depth, 3),
    }

    image = None
    if tex is not None and tex["cnt"].any():
        tw, th = tex["w"], tex["h"]
        seen = tex["cnt"] > 0
        img = np.zeros((tw * th, 3))
        img[seen] = (tex["sum"][:, seen] / tex["cnt"][seen]).T
        img = img.reshape(tw, th, 3)
        if not seen.all():
            img = _fill_nearest(img, ~seen.reshape(tw, th))
        image = to_rgb8(img, rgb_max).transpose(1, 0, 2)[::-1]  # filas = Y, norte arriba
        terrain["texture"] = {
            "origin": [float(mn[0]), float(mn[1])],
            "size": [tw * tex["px"], th * tex["px"]],
            "px": tex["px"],
        }
    return origin, terrain, image


# ------------------------------------------------------------------ auxiliares


def _grid(ext, cell, max_cells):
    cell = max(cell, float(ext.max()) / (max_cells - 1))
    nx, ny = (np.floor(ext / cell).astype(int) + 1).tolist()
    return cell, max(nx, 2), max(ny, 2)


def _round(z):
    return [round(float(v), 3) for v in z.ravel()]


def _fill_nearest(a, empty):
    """Copia en cada celda vacía el valor de la celda llena más cercana."""
    _, (ii, jj) = ndimage.distance_transform_edt(empty, return_indices=True)
    return a[ii, jj]


def _drop_low(z, valid):
    """Celdas válidas que no quedan muy por debajo de sus vecinas (ruido bajo)."""
    zf = _fill_nearest(np.nan_to_num(z), ~valid)
    med = ndimage.median_filter(zf, size=5)
    return valid & (zf >= med - LOW_TOL)


def _objects(z, res, slope, window):
    """Filtro morfológico progresivo (SMRF, Pingel et al. 2013).

    Aperturas con ventanas cada vez mayores: lo que sobresale más de lo que
    permite la pendiente es objeto (edificio, árbol), no suelo. ``window`` es el
    ancho del objeto más grande que se quiere quitar, en metros.
    """
    obj = np.zeros(z.shape, dtype=bool)
    last = z
    for r in range(1, max(1, int(round(window / 2 / res))) + 1):
        opened = ndimage.grey_opening(last, size=(2 * r + 1, 2 * r + 1))
        obj |= (last - opened) > slope * r * res
        last = opened
    return obj


def _block_mean(vals, weights, k, nx, ny):
    """Promedio ponderado de bloques k x k de la rejilla fina. NaN si el bloque no tiene datos."""
    s = vals.reshape(nx, k, ny, k).sum(axis=(1, 3))
    w = weights.reshape(nx, k, ny, k).sum(axis=(1, 3))
    return np.where(w > 0, s / np.maximum(w, 1e-12), np.nan)


def _fill(z):
    """Rellena celdas sin dato: interpolación lineal y, fuera de alcance, vecino más cercano."""
    miss = np.isnan(z)
    if not miss.any():
        return z
    if miss.all():
        raise ValueError("El filtro dejó el terreno sin puntos; prueba con --ground-slope más alto.")
    z = z.copy()
    known = np.argwhere(~miss)
    if len(known) >= 3:
        try:
            z[miss] = griddata(known, z[~miss], np.argwhere(miss), method="linear")
        except QhullError:  # puntos alineados
            pass
    miss = np.isnan(z)
    return _fill_nearest(z, miss) if miss.any() else z
