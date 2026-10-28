#! python3
# -*- coding: utf-8 -*-
"""Importa un archivo *.las2brep.json a Rhino como Breps.

Sin dependencias externas: solo RhinoCommon. Funciona en Rhino 8 (CPython 3)
y en Rhino 7 (IronPython 2.7).

Uso: comando _ScriptEditor (Rhino 8) o _EditPythonScript (Rhino 7), abrir
este archivo y ejecutar. También: _-RunPythonScript "ruta\\las2brep_importer.py"

Resultado:
  - Muros y losas -> Brep cerrada (caja) por región detectada.
  - Terreno       -> superficie NURBS (Brep de una cara), no malla.
  - Capas LAS2Brep::<Clase>::<tipo> con material de render por capa.
  - Color de objeto = color promedio del escaneo (si el LAS tiene RGB).
"""
from __future__ import print_function

import json

import Rhino
import Rhino.Geometry as rg
import scriptcontext as sc
import System.Drawing as sd

# ---------------------------------------------------------------- opciones
ROOT_LAYER = "LAS2Brep"
PLACE_AT_SOURCE_COORDS = False  # True = mover a coordenadas reales del LAS
IMPORT_RESIDUAL_POINTS = True
KIND_NAMES = {"muro": "Muros", "losa": "Losas", "inclinado": "Inclinados"}
FALLBACK_COLORS = [
    (180, 180, 180), (141, 160, 203), (102, 194, 165), (252, 141, 98),
    (231, 138, 195), (166, 216, 84), (255, 217, 47), (229, 196, 148),
]


def pick_file():
    dlg = Rhino.UI.OpenFileDialog()
    dlg.Title = "Selecciona el archivo de las2brep"
    dlg.Filter = "las2brep (*.json)|*.json"
    if not dlg.ShowOpenDialog():
        return None
    return dlg.FileName


def color(rgb, i=0):
    if rgb is None:
        rgb = FALLBACK_COLORS[i % len(FALLBACK_COLORS)]
    return sd.Color.FromArgb(int(rgb[0]), int(rgb[1]), int(rgb[2]))


class Layers(object):
    def __init__(self):
        self.cache = {}

    def get(self, path, col):
        """path = ["LAS2Brep", "Edificio", "Muros"]. Crea las capas que falten."""
        key = "::".join(path)
        if key in self.cache:
            return self.cache[key]
        parent_id = None
        idx = -1
        for depth in range(len(path)):
            full = "::".join(path[: depth + 1])
            idx = sc.doc.Layers.FindByFullPath(full, -1)
            if idx < 0:
                layer = Rhino.DocObjects.Layer()
                layer.Name = path[depth]
                layer.Color = col
                if parent_id is not None:
                    layer.ParentLayerId = parent_id
                idx = sc.doc.Layers.Add(layer)
                if depth == len(path) - 1:
                    mat = Rhino.DocObjects.Material()
                    mat.Name = "LAS2Brep " + " ".join(path[1:])
                    mat.DiffuseColor = col
                    mi = sc.doc.Materials.Add(mat)
                    layer = sc.doc.Layers[idx]
                    layer.RenderMaterialIndex = mi
                    sc.doc.Layers.Modify(layer, idx, True)
            parent_id = sc.doc.Layers[idx].Id
        self.cache[key] = idx
        return idx


def attrs(layer_idx, rgb, info):
    a = Rhino.DocObjects.ObjectAttributes()
    a.LayerIndex = layer_idx
    if rgb is not None:
        a.ColorSource = Rhino.DocObjects.ObjectColorSource.ColorFromObject
        a.ObjectColor = color(rgb)
    for k in info:
        a.SetUserString("las2brep_" + k, str(info[k]))
    return a


def plane_brep(p, tol):
    pl = rg.Plane(rg.Point3d(*p["origin"]), rg.Vector3d(*p["xaxis"]), rg.Vector3d(*p["yaxis"]))
    t = float(p["thickness"])
    if p["shape"] == "rect":
        w, h = p["size"]
        if t > 0:
            box = rg.Box(pl, rg.Interval(0, w), rg.Interval(0, h), rg.Interval(-t / 2.0, t / 2.0))
            return box.ToBrep()
        return rg.Brep.CreateFromCornerPoints(
            pl.PointAt(0, 0), pl.PointAt(w, 0), pl.PointAt(w, h), pl.PointAt(0, h), tol)

    pts = [pl.PointAt(u, v) for u, v in p["outline"]]
    pts.append(pts[0])
    crv = rg.PolylineCurve(pts)
    if t <= 0:
        breps = rg.Brep.CreatePlanarBreps(crv, tol)
        return breps[0] if breps else None
    crv.Translate(pl.ZAxis * (-t / 2.0))
    srf = rg.Surface.CreateExtrusion(crv, pl.ZAxis * t)
    if srf is None:
        return None
    brep = srf.ToBrep()
    capped = brep.CapPlanarHoles(tol)
    return capped or brep


def terrain_brep(t):
    nx, ny, cell = t["nx"], t["ny"], t["cell"]
    x0, y0 = t["origin"]
    z = t["z"]
    pts = []
    for i in range(nx):  # u = X (lento), v = Y (rápido)
        for j in range(ny):
            pts.append(rg.Point3d(x0 + i * cell, y0 + j * cell, z[i * ny + j]))
    srf = rg.NurbsSurface.CreateFromPoints(pts, nx, ny, min(3, nx - 1), min(3, ny - 1))
    return srf.ToBrep() if srf else None


def run(path=None):
    path = path or pick_file()
    if not path:
        return
    with open(path) as f:
        data = json.load(f)
    if data.get("format") != "las2brep":
        print("El archivo no es de las2brep:", path)
        return

    tol = sc.doc.ModelAbsoluteTolerance
    xf = rg.Transform.Identity
    scale = Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Meters, sc.doc.ModelUnitSystem)
    if PLACE_AT_SOURCE_COORDS:
        xf = rg.Transform.Translation(rg.Vector3d(*data["origin"]))
    if abs(scale - 1.0) > 1e-9:
        xf = rg.Transform.Scale(rg.Point3d.Origin, scale) * xf
    sc.doc.Strings.SetString("las2brep_origin", ",".join(str(v) for v in data["origin"]))

    classes = data.get("classes", {})
    layers = Layers()
    ok = bad = 0
    sc.doc.Views.RedrawEnabled = False
    try:
        for n, p in enumerate(data["planes"]):
            cname = classes.get(str(p["class"]), "Clase %s" % p["class"])
            li = layers.get([ROOT_LAYER, cname, KIND_NAMES.get(p["kind"], p["kind"])],
                            color(p.get("color"), p["class"]))
            brep = plane_brep(p, tol)
            if brep is None:
                bad += 1
                continue
            brep.Transform(xf)
            info = {"class": cname, "kind": p["kind"], "points": p["points"], "rms": p["rms"]}
            sc.doc.Objects.AddBrep(brep, attrs(li, p.get("color"), info))
            ok += 1

        for t in data.get("terrains", []):
            cname = classes.get(str(t["class"]), "Terreno")
            li = layers.get([ROOT_LAYER, cname], color(t.get("color"), t["class"]))
            brep = terrain_brep(t)
            if brep is None:
                bad += 1
                continue
            brep.Transform(xf)
            sc.doc.Objects.AddBrep(brep, attrs(li, t.get("color"), {"class": cname}))
            ok += 1

        res = data.get("residual")
        if IMPORT_RESIDUAL_POINTS and res:
            xyz, rgb = res["xyz"], res.get("rgb")
            cloud = rg.PointCloud()
            for k in range(0, len(xyz), 3):
                pt = rg.Point3d(xyz[k], xyz[k + 1], xyz[k + 2])
                if rgb:
                    cloud.Add(pt, sd.Color.FromArgb(rgb[k], rgb[k + 1], rgb[k + 2]))
                else:
                    cloud.Add(pt)
            cloud.Transform(xf)
            li = layers.get([ROOT_LAYER, "Referencia (puntos)"], sd.Color.Gray)
            sc.doc.Objects.AddPointCloud(cloud, attrs(li, None, {}))
    finally:
        sc.doc.Views.RedrawEnabled = True
    sc.doc.Views.Redraw()
    print("las2brep: %d objetos creados, %d omitidos. Origen LAS: %s" % (ok, bad, data["origin"]))


if __name__ == "__main__":
    run()
