"""Formato JSON intermedio que lee ``rhino/las2brep_importer.py``.

Es deliberadamente simple: solo primitivas (planos con contorno + espesor,
rejillas de terreno y una muestra opcional de puntos de referencia). Pesa KB
o pocos MB aunque el LAS original pese varios GB. La textura del terreno, si
la hay, va en un PNG aparte junto al JSON.
"""
from __future__ import annotations

import json
import struct
import zlib
from typing import List, Optional

import numpy as np

from .reader import class_name
from .segment import PlanePrimitive

FORMAT_VERSION = 1


def _r(a, nd=4):
    return [round(float(v), nd) for v in np.asarray(a).ravel()]


def build(
    source: str,
    origin: np.ndarray,
    prims: List[PlanePrimitive],
    thickness: dict,
    terrains: List[dict],
    residual: Optional[dict],
    stats: dict,
) -> dict:
    classes = sorted({p.cls for p in prims} | {t["class"] for t in terrains})
    return {
        "format": "las2brep",
        "version": FORMAT_VERSION,
        "source": source,
        "units": "m",
        "origin": _r(origin, 3),
        "classes": {str(c): class_name(c) for c in classes},
        "stats": stats,
        "planes": [
            {
                "class": p.cls,
                "kind": p.kind,
                "shape": p.shape,
                "origin": _r(p.origin),
                "xaxis": _r(p.xaxis, 6),
                "yaxis": _r(p.yaxis, 6),
                "outline": [_r(q) for q in p.outline],
                "size": _r(p.size),
                "thickness": float(thickness.get(p.kind, 0.0)),
                "color": None if p.color is None else list(p.color),
                "points": p.n_points,
                "rms": round(p.rms, 4),
            }
            for p in prims
        ],
        "terrains": terrains,
        "residual": residual,
    }


def write(data: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


def write_png(img: np.ndarray, path: str) -> None:
    """Guarda una imagen (alto, ancho, 3) uint8 como PNG, sin dependencias extra."""
    h, w, _ = img.shape
    rows = img.reshape(h, w * 3)
    up = (rows.astype(np.int16) - np.vstack([np.zeros((1, w * 3), np.int16), rows[:-1]])) % 256
    raw = np.column_stack([np.full(h, 2, np.uint8), up.astype(np.uint8)])  # filtro "Up" por fila

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw.tobytes(), 6)))
        f.write(chunk(b"IEND", b""))
