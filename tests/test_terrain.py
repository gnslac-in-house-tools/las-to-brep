"""Modo terreno: nube sin clasificar -> superficie (suelo o tal cual) + textura."""
import json
import struct
import zlib

import laspy
import numpy as np
import pytest

from las2brep.__main__ import main
from las2brep.terrain import heightfield

X0, Y0 = 773_820.0, 1_609_822.0
GROUND_RGB = (60, 140, 60)
BUILDING_RGB = (200, 40, 40)
BX, BY, BW, BH = 150.0, 150.0, 30.0, 12.0  # edificio: centro, ancho, alto


def ground_z(x, y):
    """Terreno real en coords locales del sintético (x, y en m desde X0, Y0)."""
    return 1885.0 + 0.1 * x + 3.0 * np.sin(y / 40.0)


def _site(rng):
    """Terreno de 300 x 300 m sin clasificar, un edificio de 30 x 30 x 12 m y ruido bajo."""
    n = 360_000
    x, y = rng.uniform(0, 300, n), rng.uniform(0, 300, n)
    inside = (np.abs(x - BX) < BW / 2) & (np.abs(y - BY) < BW / 2)
    x, y = x[~inside], y[~inside]  # bajo el edificio no se ve el suelo
    z = ground_z(x, y) + rng.normal(0, 0.03, len(x))
    rgb = np.tile(GROUND_RGB, (len(x), 1))

    u, v = np.meshgrid(np.arange(-BW / 2, BW / 2, 0.5), np.arange(-BW / 2, BW / 2, 0.5))
    rx, ry = BX + u.ravel(), BY + v.ravel()
    rz = ground_z(BX, BY) + BH + np.zeros(len(rx))
    roof = np.column_stack([rx, ry, rz])

    noise = np.column_stack([rng.uniform(0, 300, 20), rng.uniform(0, 300, 20), np.full(20, 1585.0)])

    xyz = np.vstack([np.column_stack([x, y, z]), roof, noise])
    rgb = np.vstack([rgb, np.tile(BUILDING_RGB, (len(roof), 1)), np.tile(GROUND_RGB, (20, 1))])
    return xyz, rgb


@pytest.fixture(scope="module")
def site_las(tmp_path_factory):
    path = tmp_path_factory.mktemp("sitio") / "points.las"
    xyz, rgb = _site(np.random.default_rng(3))
    h = laspy.LasHeader(point_format=3, version="1.2")
    h.scales = [0.001] * 3
    h.offsets = [X0, Y0, 1500]
    las = laspy.LasData(h)
    las.x, las.y, las.z = xyz[:, 0] + X0, xyz[:, 1] + Y0, xyz[:, 2]
    las.classification = np.zeros(len(xyz), np.uint8)  # sin clasificar
    las.red, las.green, las.blue = (rgb[:, k].astype(np.uint16) * 257 for k in range(3))
    las.write(str(path))
    return path


def _convert(las, out, *extra):
    main(["convert", str(las), "-o", str(out), "--chunk", "100000", *extra])
    return json.loads(out.read_text(encoding="utf-8"))


def _nodes(data):
    """Posición (x, y en coords del sintético) y z de cada punto de control del terreno."""
    (t,) = data["terrains"]
    o = np.array(data["origin"])
    z = np.array(t["z"]).reshape(t["nx"], t["ny"]) + o[2]
    i, j = np.meshgrid(np.arange(t["nx"]), np.arange(t["ny"]), indexing="ij")
    x = t["origin"][0] + i * t["cell"] + o[0] - X0
    y = t["origin"][1] + j * t["cell"] + o[1] - Y0
    return x, y, z, t


def _read_png(path):
    """Decodificador mínimo de PNG RGB de 8 bits (filtros 0-4), solo para pruebas."""
    b = path.read_bytes()
    assert b[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, w, h = 8, b"", None, None
    while pos < len(b):
        n, kind = struct.unpack(">I4s", b[pos:pos + 8])
        body = b[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", body[:10])
            assert (depth, ctype) == (8, 2)
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    raw = np.frombuffer(zlib.decompress(idat), np.uint8).reshape(h, 1 + w * 3)
    out = np.zeros((h, w * 3), np.int32)
    for r in range(h):
        f, line = raw[r, 0], raw[r, 1:].astype(np.int32)
        prev = out[r - 1] if r else np.zeros(w * 3, np.int32)
        if f == 0:
            out[r] = line
        elif f == 2:
            out[r] = (line + prev) % 256
        else:
            raise AssertionError("filtro PNG no soportado en la prueba: %d" % f)
    return out.reshape(h, w, 3).astype(np.uint8)


# ------------------------------------------------------------------ pruebas


def test_heightfield_nodes_sit_at_cell_centers():
    """La altura de cada celda se publica en el centro de la celda, no en su esquina."""
    u, v = np.meshgrid(np.arange(0, 40, 0.02), np.arange(0, 20, 0.5))
    xyz = np.column_stack([u.ravel(), v.ravel(), 0.5 * u.ravel()])
    t = heightfield(xyz, None, cell=2.0, max_cells=1000)
    z = np.array(t["z"]).reshape(t["nx"], t["ny"])
    x_node = t["origin"][0] + np.arange(t["nx"] - 1) * t["cell"]  # la última celda queda incompleta
    assert np.allclose(z[:-1, 0], 0.5 * x_node, atol=0.02)


def test_terrain_mode_ground_removes_buildings_and_noise(site_las, tmp_path):
    data = _convert(site_las, tmp_path / "suelo.json", "--terrain", "--ground", "--terrain-cell", "5",
                    "--texture", "0")
    assert data["planes"] == []
    x, y, z, t = _nodes(data)
    err = np.abs(z - ground_z(x, y))
    on_building = (np.abs(x - BX) < 8) & (np.abs(y - BY) < 8)
    assert on_building.any()
    assert err[on_building].max() < 0.5, err[on_building].max()  # el edificio desaparece
    assert np.median(err) < 0.1 and err.max() < 1.0, (np.median(err), err.max())  # sin pozos de ruido
    assert t["base"] + data["origin"][2] > 1885.0 - 20  # la base no la arrastra el ruido a 1585 m


def test_terrain_mode_surface_keeps_buildings(site_las, tmp_path):
    data = _convert(site_las, tmp_path / "tal_cual.json", "--terrain", "--terrain-cell", "5",
                    "--texture", "0")
    x, y, z, _ = _nodes(data)
    on_building = (np.abs(x - BX) < 8) & (np.abs(y - BY) < 8)
    roof = ground_z(BX, BY) + BH
    assert np.all(np.abs(z[on_building] - roof) < 0.5), z[on_building] - roof


def test_terrain_texture_png_is_top_view_north_up(site_las, tmp_path):
    out = tmp_path / "tex.las2brep.json"
    data = _convert(site_las, out, "--terrain", "--terrain-cell", "5", "--texture", "1")
    tex = data["terrains"][0]["texture"]
    png = tmp_path / tex["file"]
    img = _read_png(png)
    h, w, _ = img.shape
    assert (w, h) == (round(tex["size"][0] / tex["px"]), round(tex["size"][1] / tex["px"]))

    def pixel(x, y):  # x, y en coords del sintético; fila 0 = norte (Y máx)
        o = np.array(data["origin"])
        col = int((x + X0 - o[0] - tex["origin"][0]) / tex["px"])
        row = h - 1 - int((y + Y0 - o[1] - tex["origin"][1]) / tex["px"])
        return img[row, col].astype(int)

    assert np.abs(pixel(BX, BY) - BUILDING_RGB).max() <= 2
    assert np.abs(pixel(BX + 60, BY + 100) - GROUND_RGB).max() <= 2  # norte del edificio
    assert np.abs(pixel(BX - 100, BY - 60) - GROUND_RGB).max() <= 2


def test_warns_when_no_ground_class(site_las, tmp_path, capsys):
    _convert(site_las, tmp_path / "planos.json", "--max-planes", "0")
    assert "--terrain" in capsys.readouterr().out
