"""Prueba de punta a punta con un LAS sintético: un cuarto sobre terreno."""
import json

import laspy
import numpy as np

from las2brep.__main__ import main
from las2brep.reader import inspect, voxelize

X0, Y0 = 500_000.0, 2_100_000.0  # coordenadas tipo UTM, para probar el desplazamiento


def _room(rng, step=0.02, noise=0.004):
    """Cuarto de 6 x 4 x 3 m (piso, techo, 4 muros) + terreno ondulado."""
    pts, cls = [], []

    def grid(a, b):
        u, v = np.meshgrid(np.arange(0, a, step), np.arange(0, b, step))
        return u.ravel(), v.ravel()

    u, v = grid(6, 4)
    for z in (0.0, 3.0):  # piso y techo
        pts.append(np.column_stack([u, v, np.full_like(u, z)]))
    u, v = grid(6, 3)
    for y in (0.0, 4.0):
        pts.append(np.column_stack([u, np.full_like(u, y), v]))
    u, v = grid(4, 3)
    for x in (0.0, 6.0):
        pts.append(np.column_stack([np.full_like(u, x), u, v]))
    cls += [6] * sum(len(p) for p in pts)

    u, v = np.meshgrid(np.arange(-5, 11, 0.1), np.arange(-5, 9, 0.1))
    ground = np.column_stack([u.ravel(), v.ravel(), -0.3 + 0.2 * np.sin(u.ravel() / 3)])
    pts.append(ground)
    cls += [2] * len(ground)

    xyz = np.vstack(pts) + rng.normal(0, noise, (sum(len(p) for p in pts), 3))
    return xyz, np.array(cls, dtype=np.uint8)


def _write_las(path, rng):
    xyz, cls = _room(rng)
    h = laspy.LasHeader(point_format=3, version="1.2")
    h.scales = [0.001] * 3
    h.offsets = [X0, Y0, 0]
    las = laspy.LasData(h)
    las.x, las.y, las.z = xyz[:, 0] + X0, xyz[:, 1] + Y0, xyz[:, 2]
    las.classification = cls
    las.red = np.where(cls == 6, 200, 90).astype(np.uint16) * 257
    las.green = np.where(cls == 6, 190, 140).astype(np.uint16) * 257
    las.blue = np.where(cls == 6, 180, 60).astype(np.uint16) * 257
    las.write(str(path))
    return len(xyz)


def test_end_to_end(tmp_path):
    las = tmp_path / "cuarto.las"
    n = _write_las(las, np.random.default_rng(1))

    info = inspect(str(las), scan=True)
    assert info["point_count"] == n and info["has_rgb"]
    assert set(info["classes"]) == {2, 6}

    vc = voxelize(str(las), voxel=0.05, chunk_size=200_000, progress=lambda s: None)
    assert vc.rgb is not None and vc.rgb.max() <= 255
    assert len(vc.xyz) < n

    out = tmp_path / "cuarto.json"
    main(["convert", str(las), "-o", str(out), "--chunk", "200000"])
    data = json.loads(out.read_text(encoding="utf-8"))

    assert data["origin"][:2] == [X0 - 6, Y0 - 6]  # floor(mins); el ruido baja el mínimo de -5
    planes = data["planes"]
    kinds = sorted(p["kind"] for p in planes)
    assert kinds == ["losa"] * 2 + ["muro"] * 4, kinds

    sizes = sorted(tuple(sorted(p["size"])) for p in planes)
    expected = sorted([(4, 6), (4, 6), (3, 6), (3, 6), (3, 4), (3, 4)])
    for got, exp in zip(sizes, expected):
        assert np.allclose(got, exp, atol=0.1), (got, exp)
    assert all(p["color"] == [200, 190, 180] for p in planes)

    (t,) = data["terrains"]
    assert t["class"] == 2 and t["nx"] * t["ny"] == len(t["z"])
