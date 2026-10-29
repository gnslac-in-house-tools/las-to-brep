"""Línea de comandos.

    python -m las2brep inspect escaneo.las --scan
    python -m las2brep convert escaneo.las -o escaneo.las2brep.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

from . import __version__, export
from .reader import class_name, inspect, voxelize
from .segment import SegmentOptions, detect_planes
from .terrain import heightfield, terrain_from_las


def _ints(s):
    return [int(v) for v in s.split(",") if v.strip()] if s else []


def _floats(s):
    v = [float(x) for x in s.split(",")]
    if len(v) != 4:
        raise argparse.ArgumentTypeError("usa xmin,ymin,xmax,ymax")
    return tuple(v)


def cmd_inspect(a):
    info = inspect(a.las, scan=a.scan)
    print(json.dumps(info, indent=2, ensure_ascii=False))


def cmd_convert(a):
    t0 = time.time()
    out = a.output or os.path.splitext(a.las)[0] + ".las2brep.json"
    if a.terrain:
        return convert_terrain(a, out, t0)

    print(f"[1/4] Leyendo y reduciendo a voxeles de {a.voxel} m ...")
    vc = voxelize(
        a.las,
        voxel=a.voxel,
        chunk_size=a.chunk,
        keep_classes=_ints(a.classes) or None,
        ignore_classes=_ints(a.ignore),
        bbox=a.bbox,
    )
    print(f"      {vc.source_points:,} puntos -> {len(vc.xyz):,} voxeles")

    terrain_cls = set(_ints(a.terrain_classes))
    is_terrain = np.isin(vc.cls, list(terrain_cls)) if terrain_cls else np.zeros(len(vc.cls), bool)
    if terrain_cls and not is_terrain.any():
        print(f"      Aviso: no hay puntos de clase {a.terrain_classes} (terreno). "
              "Si el archivo es un terreno sin clasificar, usa --terrain.")

    print("[2/4] Terreno ...")
    terrains = []
    for c in sorted(set(vc.cls[is_terrain].tolist())):
        m = vc.cls == c
        t = heightfield(vc.xyz[m], None if vc.rgb is None else vc.rgb[m], a.terrain_cell, a.terrain_max)
        if t:
            t["class"] = int(c)
            terrains.append(t)
            print(f"      {class_name(c)}: rejilla {t['nx']}x{t['ny']} (celda {t['cell']:.2f} m)")

    print("[3/4] Detectando planos ...")
    opt = SegmentOptions(
        distance=a.distance,
        min_points=a.min_points,
        max_planes=a.max_planes,
        gap=a.gap,
        snap_deg=a.snap,
        manhattan=a.manhattan,
        outline=a.outline,
    )
    sel = np.flatnonzero(~is_terrain)
    prims, left = detect_planes(
        vc.xyz[sel], vc.cls[sel], None if vc.rgb is None else vc.rgb[sel], opt
    )

    residual = None
    if a.residual > 0 and left.any():
        rest = sel[left]
        if len(rest) > a.residual:
            rest = np.random.default_rng(0).choice(rest, a.residual, replace=False)
        residual = {
            "xyz": [round(float(v), 3) for v in vc.xyz[rest].ravel()],
            "rgb": None if vc.rgb is None else vc.rgb[rest].ravel().tolist(),
        }

    print("[4/4] Escribiendo", out)
    stats = {
        "las2brep": __version__,
        "source_points": vc.source_points,
        "voxels": int(len(vc.xyz)),
        "voxel_size": a.voxel,
        "planes": len(prims),
        "unassigned_voxels": int(left.sum()),
        "seconds": round(time.time() - t0, 1),
    }
    thickness = {"muro": a.wall_thickness, "losa": a.slab_thickness, "inclinado": a.slab_thickness}
    data = export.build(os.path.basename(a.las), vc.origin, prims, thickness, terrains, residual, stats)
    export.write(data, out)
    kb = os.path.getsize(out) / 1024
    print(f"Listo: {len(prims)} planos, {len(terrains)} terrenos, {kb:,.0f} KB en {stats['seconds']} s")


def convert_terrain(a, out, t0):
    """Modo --terrain: toda la nube es una sola superficie de terreno, sin planos."""
    print("[1/2] Leyendo el terreno ...")
    origin, t, image = terrain_from_las(
        a.las,
        cell=a.terrain_cell,
        max_cells=a.terrain_max,
        ground=a.ground,
        slope=a.ground_slope,
        window=a.ground_window,
        texture_px=a.texture,
        base_depth=a.base,
        chunk_size=a.chunk,
        keep_classes=_ints(a.classes) or None,
        ignore_classes=_ints(a.ignore),
        bbox=a.bbox,
    )
    print(f"      {t['mode']}: rejilla {t['nx']}x{t['ny']} (celda {t['cell']:.2f} m), {t['points']:,} puntos")

    print("[2/2] Escribiendo", out)
    if image is not None:
        png = os.path.splitext(out)[0] + ".png"
        export.write_png(image, png)
        t["texture"]["file"] = os.path.basename(png)
        print(f"      textura {image.shape[1]}x{image.shape[0]} px ({t['texture']['px']:g} m/px): {png}")
    stats = {
        "las2brep": __version__,
        "source_points": t["points"],
        "terrain_mode": t["mode"],
        "seconds": round(time.time() - t0, 1),
    }
    data = export.build(os.path.basename(a.las), origin, [], {}, [t], None, stats)
    export.write(data, out)
    kb = os.path.getsize(out) / 1024
    print(f"Listo: 1 terreno ({t['mode']}), {kb:,.0f} KB en {stats['seconds']} s")


def main(argv=None):
    p = argparse.ArgumentParser(prog="las2brep", description="LAS/LAZ -> primitivas para Breps de Rhino")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("inspect", help="muestra encabezado y (con --scan) las clases presentes")
    i.add_argument("las")
    i.add_argument("--scan", action="store_true", help="recorre el archivo para contar puntos por clase")
    i.set_defaults(fn=cmd_inspect)

    c = sub.add_parser("convert", help="genera el JSON de primitivas para Rhino")
    c.add_argument("las")
    c.add_argument("-o", "--output")
    g = c.add_argument_group("lectura")
    g.add_argument("--voxel", type=float, default=0.05, help="tamaño de voxel en m (default 0.05)")
    g.add_argument("--chunk", type=int, default=2_000_000, help="puntos por bloque de lectura")
    g.add_argument("--classes", help="solo estas clases, ej. 1,6")
    g.add_argument("--ignore", default="7,18", help="clases a descartar (default ruido 7,18)")
    g.add_argument("--bbox", type=_floats, help="recorte xmin,ymin,xmax,ymax en coords del LAS")
    g = c.add_argument_group("planos")
    g.add_argument("--distance", type=float, default=0.03, help="tolerancia punto-plano en m")
    g.add_argument("--min-points", type=int, default=300, help="voxeles mínimos por región")
    g.add_argument("--max-planes", type=int, default=400)
    g.add_argument("--gap", type=float, default=0.15, help="hueco máximo dentro de una región (m)")
    g.add_argument("--snap", type=float, default=3.0, help="grados para enderezar muros/losas (0 = no)")
    g.add_argument("--manhattan", action="store_true", help="alinea muros a retícula ortogonal")
    g.add_argument("--outline", choices=["rect", "hull"], default="rect")
    g.add_argument("--wall-thickness", type=float, default=0.15, help="espesor de muros (0 = superficie)")
    g.add_argument("--slab-thickness", type=float, default=0.20, help="espesor de losas (0 = superficie)")
    g = c.add_argument_group("terreno")
    g.add_argument("--terrain", action="store_true",
                   help="modo terreno: toda la nube es una sola superficie, sin importar la clase; no detecta planos")
    g.add_argument("--terrain-classes", default="2", help="clases tratadas como terreno (vacío = ninguna)")
    g.add_argument("--terrain-cell", type=float, default=1.0, help="celda de la rejilla en m")
    g.add_argument("--terrain-max", type=int, default=150, help="máx. puntos de control por lado")
    g.add_argument("--ground", action="store_true", help="con --terrain: solo el suelo (quita edificios y árboles)")
    g.add_argument("--ground-window", type=float, default=40.0,
                   help="con --ground: ancho en m del objeto más grande a quitar (default 40)")
    g.add_argument("--ground-slope", type=float, default=0.2,
                   help="con --ground: pendiente máx. del suelo, 0.2 = 20%% (súbela en terreno muy inclinado)")
    g.add_argument("--texture", type=float, default=0.5, help="con --terrain: m por pixel de la textura (0 = sin textura)")
    g.add_argument("--base", type=float, default=5.0, help="con --terrain: m bajo el punto más bajo para la base del sólido")
    g = c.add_argument_group("referencia")
    g.add_argument("--residual", type=int, default=0, help="exporta N puntos no asignados como nube ligera")
    c.set_defaults(fn=cmd_convert)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
