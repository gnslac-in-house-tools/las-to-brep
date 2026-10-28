"""Detección de planos (RANSAC) y conversión a primitivas sólidas simples.

Cada plano detectado se separa en regiones conectadas y cada región se
convierte en un rectángulo (o polígono convexo) dentro de su propio marco
local. En Rhino esa primitiva se extruye con un espesor y queda como una Brep
cerrada de 6 caras: fácil de cortar, perforar o hacer booleanas.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

import numpy as np
from scipy import ndimage
from scipy.spatial import ConvexHull, QhullError

Z = np.array([0.0, 0.0, 1.0])


@dataclass
class PlanePrimitive:
    origin: np.ndarray  # esquina/origen del marco, coords locales
    xaxis: np.ndarray
    yaxis: np.ndarray
    normal: np.ndarray
    outline: np.ndarray  # (k, 2) polígono en coordenadas (u, v) del marco
    shape: str  # "rect" | "hull"
    kind: str  # "muro" | "losa" | "inclinado"
    cls: int
    color: Optional[tuple]
    n_points: int
    rms: float
    size: tuple = field(default=(0.0, 0.0))  # ancho, alto si shape == "rect"


@dataclass
class SegmentOptions:
    distance: float = 0.03  # tolerancia punto-plano (m)
    min_points: int = 300  # mínimo de voxeles por región
    max_planes: int = 400
    iterations: int = 256
    sample: int = 150_000  # puntos usados para proponer planos
    gap: float = 0.15  # separación máxima dentro de una misma región (m)
    snap_deg: float = 3.0  # endereza muros a vertical y losas a horizontal
    manhattan: bool = False  # alinea muros a una retícula ortogonal
    outline: str = "rect"  # "rect" | "hull"
    seed: int = 0


def detect_planes(
    xyz: np.ndarray,
    cls: np.ndarray,
    rgb: Optional[np.ndarray],
    opt: SegmentOptions,
    progress: Callable[[str], None] = print,
) -> tuple:
    """Devuelve (lista de PlanePrimitive, máscara de puntos no usados)."""
    rng = np.random.default_rng(opt.seed)
    remaining = np.ones(len(xyz), dtype=bool)
    prims: List[PlanePrimitive] = []
    fails = 0
    grid_angle = None  # azimut de la retícula manhattan

    while len(prims) < opt.max_planes and fails < 3:
        idx = np.flatnonzero(remaining)
        if len(idx) < opt.min_points:
            break
        sub = idx if len(idx) <= opt.sample else rng.choice(idx, opt.sample, replace=False)
        n, d, _ = _ransac(xyz[sub], opt.distance, opt.iterations, rng)
        if n is None:
            break

        pts = xyz[idx]
        inl = np.abs(pts @ n - d) < opt.distance
        for _ in range(2):  # ajuste por mínimos cuadrados + enderezado
            if inl.sum() < 3:
                break
            n, d = _fit(pts[inl], rng)
            n = _snap(n, opt.snap_deg)
            if opt.manhattan and abs(n[2]) < 1e-6:
                n, grid_angle = _manhattan(n, grid_angle, opt.snap_deg)
            d = float(pts[inl].mean(0) @ n)
            inl = np.abs(pts @ n - d) < opt.distance

        if inl.sum() < opt.min_points:
            fails += 1
            continue
        fails = 0
        plane_idx = idx[inl]
        remaining[plane_idx] = False

        xaxis, yaxis = _frame(n)
        base = n * d
        uv = np.column_stack([(xyz[plane_idx] - base) @ xaxis, (xyz[plane_idx] - base) @ yaxis])
        for comp in _components(uv, opt.gap, opt.min_points):
            ids = plane_idx[comp]
            prim = _primitive(uv[comp], base, n, xaxis, yaxis, xyz[ids], cls[ids],
                              None if rgb is None else rgb[ids], opt.outline)
            if prim is not None:
                prims.append(prim)
        progress(f"  plano {len(prims):>4}: {inl.sum():,} pts, quedan {remaining.sum():,}")

    return prims, remaining


# --------------------------------------------------------------------------- RANSAC


def _ransac(P, dist, iters, rng, batch=32):
    best = (None, None, -1)
    m = len(P)
    if m < 3:
        return best
    for start in range(0, iters, batch):
        b = min(batch, iters - start)
        tri = rng.integers(0, m, size=(b, 3))
        a = P[tri[:, 0]]
        N = np.cross(P[tri[:, 1]] - a, P[tri[:, 2]] - a)
        L = np.linalg.norm(N, axis=1)
        ok = L > 1e-9
        if not ok.any():
            continue
        N = N[ok] / L[ok, None]
        D = np.einsum("ij,ij->i", N, a[ok])
        cnt = (np.abs(P @ N.T - D) < dist).sum(0)
        k = int(np.argmax(cnt))
        if cnt[k] > best[2]:
            best = (N[k], float(D[k]), int(cnt[k]))
    return best


def _fit(P, rng, max_pts=200_000):
    if len(P) > max_pts:
        P = P[rng.choice(len(P), max_pts, replace=False)]
    c = P.mean(0)
    n = np.linalg.svd(P - c, full_matrices=False)[2][-1]
    return n / np.linalg.norm(n), float(c @ n)


def _snap(n, deg):
    if deg <= 0:
        return n
    if abs(n[2]) >= np.cos(np.radians(deg)):
        return np.array([0.0, 0.0, 1.0 if n[2] >= 0 else -1.0])
    if abs(n[2]) <= np.sin(np.radians(deg)):
        h = np.array([n[0], n[1], 0.0])
        return h / np.linalg.norm(h)
    return n


def _manhattan(n, grid_angle, deg):
    ang = np.arctan2(n[1], n[0])
    if grid_angle is None:
        return n, ang % (np.pi / 2)
    k = np.round((ang - grid_angle) / (np.pi / 2))
    target = grid_angle + k * np.pi / 2
    if abs(ang - target) <= np.radians(deg):
        n = np.array([np.cos(target), np.sin(target), 0.0])
    return n, grid_angle


# ------------------------------------------------------------------ geometría 2D


def _frame(n):
    """Marco del plano. En muros: X horizontal, Y hacia arriba."""
    if abs(n[2]) > 0.95:
        x = np.array([1.0, 0.0, 0.0]) - n * n[0]
    else:
        x = np.cross(Z, n)
    x /= np.linalg.norm(x)
    return x, np.cross(n, x)


def _components(uv, gap, min_points, max_cells=40_000_000):
    """Separa los puntos de un plano en regiones conectadas (raster + label)."""
    cell = gap
    mn = uv.min(0)
    shape = np.floor((uv.max(0) - mn) / cell).astype(np.int64) + 1
    if shape.prod() > max_cells:
        cell *= np.sqrt(shape.prod() / max_cells)
        shape = np.floor((uv.max(0) - mn) / cell).astype(np.int64) + 1
    ij = np.floor((uv - mn) / cell).astype(np.int64)
    grid = np.zeros(shape, dtype=bool)
    grid[ij[:, 0], ij[:, 1]] = True
    lab, nlab = ndimage.label(grid, structure=np.ones((3, 3)))
    pl = lab[ij[:, 0], ij[:, 1]]
    order = np.argsort(pl, kind="stable")
    sizes = np.bincount(pl, minlength=nlab + 1)
    groups = np.split(order, np.cumsum(sizes)[:-1])
    return [g for k, g in enumerate(groups) if k > 0 and len(g) >= min_points]


def _min_area_rect(uv):
    """Rectángulo de área mínima: (ángulo, mínimos, máximos) en el marco rotado."""
    try:
        H = uv[ConvexHull(uv).vertices]
    except QhullError:
        H = uv
    e = np.diff(np.vstack([H, H[:1]]), axis=0)
    best = None
    for a in np.unique(np.round(np.arctan2(e[:, 1], e[:, 0]) % (np.pi / 2), 6)):
        c, s = np.cos(a), np.sin(a)
        r = H @ np.array([[c, -s], [s, c]])
        lo, hi = r.min(0), r.max(0)
        area = np.prod(hi - lo)
        if best is None or area < best[0]:
            best = (area, a, lo, hi)
    return best[1], best[2], best[3]


def _primitive(uv, base, n, xaxis, yaxis, pts, cls, rgb, outline):
    if outline == "hull":
        try:
            poly = uv[ConvexHull(uv).vertices]
        except QhullError:
            return None
        origin, x, y, size = base, xaxis, yaxis, (0.0, 0.0)
    else:
        # Muros: rectángulo alineado (X horizontal, Y vertical). Resto: área mínima.
        if abs(n[2]) < 1e-6:
            ang, lo, hi = 0.0, uv.min(0), uv.max(0)
        else:
            ang, lo, hi = _min_area_rect(uv)
        c, s = np.cos(ang), np.sin(ang)
        x = c * xaxis + s * yaxis
        y = -s * xaxis + c * yaxis
        origin = base + lo[0] * x + lo[1] * y
        w, h = hi - lo
        if w <= 0 or h <= 0:
            return None
        poly = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=float)
        size = (float(w), float(h))

    if abs(n[2]) < 0.1:
        kind = "muro"
    elif abs(n[2]) > 0.9:
        kind = "losa"
    else:
        kind = "inclinado"
    rms = float(np.sqrt(np.mean(((pts - base) @ n) ** 2)))
    color = None if rgb is None else tuple(int(v) for v in np.rint(rgb.mean(0)))
    return PlanePrimitive(
        origin=origin, xaxis=x, yaxis=y, normal=n, outline=poly,
        shape="hull" if outline == "hull" else "rect", kind=kind,
        cls=int(np.bincount(cls).argmax()), color=color,
        n_points=len(pts), rms=rms, size=size,
    )
