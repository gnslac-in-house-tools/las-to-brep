"""Lectura de archivos LAS/LAZ por bloques y reducción por voxeles.

El archivo nunca se carga completo en memoria: se lee en bloques de
``chunk_size`` puntos y cada bloque se acumula en una rejilla de voxeles.
Así un LAS de varios GB termina siendo una nube de unos pocos millones de
puntos representativos (uno por voxel y por clase).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Optional

import laspy
import numpy as np

# Clases estándar ASPRS (LAS 1.4). Las que no estén aquí se nombran "Clase N".
ASPRS_CLASSES = {
    0: "Creado nunca clasificado",
    1: "Sin clasificar",
    2: "Terreno",
    3: "Vegetación baja",
    4: "Vegetación media",
    5: "Vegetación alta",
    6: "Edificio",
    7: "Ruido bajo",
    9: "Agua",
    10: "Ferrocarril",
    11: "Superficie de camino",
    13: "Protector de cable",
    14: "Cable conductor",
    15: "Torre de transmisión",
    16: "Conector de cable",
    17: "Puente",
    18: "Ruido alto",
}


def class_name(code: int) -> str:
    return ASPRS_CLASSES.get(int(code), "Clase %d" % int(code))


@dataclass
class VoxelCloud:
    """Nube reducida. Coordenadas locales (restando ``origin``)."""

    xyz: np.ndarray  # (N, 3) float64
    rgb: Optional[np.ndarray]  # (N, 3) uint8 o None
    cls: np.ndarray  # (N,) uint8
    count: np.ndarray  # (N,) puntos originales que representa cada voxel
    origin: np.ndarray  # (3,) desplazamiento aplicado
    source_points: int  # puntos leídos del archivo


def inspect(path: str, scan: bool = False, chunk_size: int = 2_000_000) -> dict:
    """Lee el encabezado. Con ``scan=True`` recorre el archivo para contar clases."""
    with laspy.open(path) as f:
        h = f.header
        dims = list(h.point_format.dimension_names)
        info = {
            "version": "%d.%d" % (h.version.major, h.version.minor),
            "point_format": int(h.point_format.id),
            "point_count": int(h.point_count),
            "dimensions": dims,
            "has_rgb": {"red", "green", "blue"} <= set(dims),
            "mins": h.mins.tolist(),
            "maxs": h.maxs.tolist(),
            "scales": h.scales.tolist(),
        }
        try:
            crs = h.parse_crs()
            info["crs"] = crs.name if crs is not None else None
        except Exception:  # pyproj no instalado o CRS ilegible
            info["crs"] = None
        if scan:
            counts = np.zeros(256, dtype=np.int64)
            for pts in f.chunk_iterator(chunk_size):
                counts += np.bincount(_classification(pts), minlength=256)
            info["classes"] = {
                int(c): {"name": class_name(c), "points": int(n)}
                for c, n in enumerate(counts)
                if n
            }
    return info


def voxelize(
    path: str,
    voxel: float = 0.05,
    chunk_size: int = 2_000_000,
    keep_classes: Optional[Iterable[int]] = None,
    ignore_classes: Iterable[int] = (),
    bbox: Optional[tuple] = None,
    progress: Callable[[str], None] = print,
) -> VoxelCloud:
    """Lee el archivo por bloques y promedia los puntos dentro de cada voxel.

    Cada (voxel, clase) produce un punto: posición y color promedio.
    ``bbox`` = (xmin, ymin, xmax, ymax) en coordenadas originales del LAS.
    """
    keep = None if keep_classes is None else np.array(sorted(set(keep_classes)), dtype=np.uint8)
    ignore = np.array(sorted(set(ignore_classes)), dtype=np.uint8)

    with laspy.open(path) as f:
        h = f.header
        origin = np.floor(h.mins)
        n = np.ceil((h.maxs - origin) / voxel).astype(np.int64) + 1
        if float(n[0]) * float(n[1]) * float(n[2]) * 256 >= 2**62:
            raise ValueError(
                "La rejilla de voxeles es demasiado grande para este archivo; "
                "aumenta --voxel o recorta con --bbox."
            )
        has_rgb = {"red", "green", "blue"} <= set(h.point_format.dimension_names)
        total = int(h.point_count)

        keys = np.empty(0, dtype=np.int64)
        sums = np.empty((0, 6), dtype=np.float64)
        counts = np.empty(0, dtype=np.float64)
        rgb_max = 0
        done = 0

        for pts in f.chunk_iterator(chunk_size):
            done += len(pts)
            cls = _classification(pts)
            x = np.asarray(pts.x)
            y = np.asarray(pts.y)
            mask = np.ones(len(cls), dtype=bool)
            if keep is not None:
                mask &= np.isin(cls, keep)
            if len(ignore):
                mask &= ~np.isin(cls, ignore)
            if bbox is not None:
                mask &= (x >= bbox[0]) & (y >= bbox[1]) & (x <= bbox[2]) & (y <= bbox[3])
            if not mask.any():
                continue

            p = np.column_stack([x[mask], y[mask], np.asarray(pts.z)[mask]]) - origin
            c = cls[mask]
            vals = np.zeros((len(p), 6), dtype=np.float64)
            vals[:, :3] = p
            if has_rgb:
                rgb = np.column_stack([pts.red[mask], pts.green[mask], pts.blue[mask]])
                rgb_max = max(rgb_max, int(rgb.max()))
                vals[:, 3:] = rgb

            ijk = np.clip(np.floor(p / voxel).astype(np.int64), 0, n - 1)
            k = ((ijk[:, 2] * n[1] + ijk[:, 1]) * n[0] + ijk[:, 0]) * 256 + c

            keys, sums, counts = _reduce(
                np.concatenate([keys, k]),
                np.concatenate([sums, vals]),
                np.concatenate([counts, np.ones(len(k))]),
            )
            progress(f"  leídos {done:,} / {total:,} puntos -> {len(keys):,} voxeles")

    xyz = sums[:, :3] / counts[:, None]
    rgb = None
    if has_rgb and len(keys):
        rgb = sums[:, 3:] / counts[:, None]
        if rgb_max > 255:  # LAS guarda color en 16 bits
            rgb = rgb / 257.0
        rgb = np.clip(np.rint(rgb), 0, 255).astype(np.uint8)
    return VoxelCloud(
        xyz=xyz,
        rgb=rgb,
        cls=(keys % 256).astype(np.uint8),
        count=counts.astype(np.int64),
        origin=origin,
        source_points=done,
    )


def _classification(pts) -> np.ndarray:
    return np.asarray(pts.classification).astype(np.uint8)


def _reduce(keys, vals, counts):
    """Suma ``vals`` y ``counts`` agrupando por llave."""
    uniq, inv = np.unique(keys, return_inverse=True)
    out = np.empty((len(uniq), vals.shape[1]), dtype=np.float64)
    for j in range(vals.shape[1]):
        out[:, j] = np.bincount(inv, weights=vals[:, j], minlength=len(uniq))
    return uniq, out, np.bincount(inv, weights=counts, minlength=len(uniq))
