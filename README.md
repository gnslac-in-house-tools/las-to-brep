# las-to-brep

Convierte nubes de puntos **LAS/LAZ** (escaneos láser, de varios GB) en **Breps ligeras de Rhino**: muros y losas como sólidos tipo caja y terreno como superficie NURBS. **No genera mallas.** El resultado es fácil de cortar, perforar y usar en booleanas.

```
escaneo.las (1+ GB)  ──►  python -m las2brep convert  ──►  escaneo.las2brep.json (KB–MB)  ──►  Rhino: las2brep_importer.py  ──►  Breps
```

## Por qué este enfoque

Una nube de puntos no tiene superficies: son millones de puntos sueltos. Hay tres formas de llegar a geometría:

| Enfoque | Resultado | Problema |
|---|---|---|
| Triangular (Poisson, Ball pivoting) | Malla | Pesada, llena de ruido, difícil de editar. Justo lo que no queremos. |
| Ajustar NURBS libres a todo | Superficies complejas | Lento, frágil y no da sólidos cerrados. |
| **Detectar primitivas (este repo)** | **Planos → cajas, terreno → 1 superficie NURBS** | Simplifica: los detalles finos se pierden (a propósito). |

En arquitectura casi todo es plano (muros, losas, techos, fachadas). Así que el tool:

1. **Lee el LAS por bloques** (`laspy`), sin cargar el archivo completo en RAM.
2. **Reduce a voxeles** (5 cm por defecto): de decenas de millones de puntos a 1–3 millones, separando por clase y promediando color.
3. **Terreno** (clase ASPRS 2): rejilla de alturas → una superficie NURBS de ≤150×150 puntos de control. Si el archivo no está clasificado, usa `--terrain` (ver abajo).
4. **Planos** con RANSAC + mínimos cuadrados. Endereza muros a vertical y losas a horizontal (`--snap`). Cada plano se separa en regiones conectadas y cada región se vuelve un rectángulo de área mínima.
5. **Exporta un JSON pequeño** solo con primitivas. Un archivo de 600 MB de prueba produjo 119 KB.
6. **En Rhino**, un script sin dependencias crea las Breps con `Box.ToBrep()`. Cada región queda como una caja cerrada de 6 caras con el espesor que elijas.

### ¿Y los "materiales"?

Un LAS no guarda materiales como Rhino. Guarda por punto la **clasificación** (terreno, edificio, vegetación...), el **color RGB** (si el escáner lo capturó) y la **intensidad**. El tool los traduce así:

- **Clase → capa** `LAS2Brep::<Clase>::<Muros|Losas|Inclinados>`, con un material de render por capa.
- **RGB → color de objeto**: el color promedio de los puntos de esa región. Pesa casi nada; es un color por objeto, no por punto.
- Cada Brep lleva *user text* (`las2brep_class`, `las2brep_kind`, `las2brep_points`, `las2brep_rms`) para filtrar o hacer schedules.

Texturizar con el color punto por punto obligaría a usar mallas o imágenes enormes, y eso contradice el objetivo.

## Instalación (compu de la empresa)

1. Clona este repo con **GitHub Desktop**.
2. Necesitas Python ≥ 3.9 con `numpy`, `scipy` y `laspy`. Usa la opción que te permita tu equipo:

   **A. Python del sistema**
   ```powershell
   cd "ruta\al\repo\las-to-brep"
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

   **B. El Python que trae Rhino 8** (si no puedes instalar Python):
   ```powershell
   & "$env:USERPROFILE\.rhinocode\py39-rh8\python.exe" -m pip install -r requirements.txt
   & "$env:USERPROFILE\.rhinocode\py39-rh8\python.exe" -m las2brep convert archivo.las
   ```
   (Abre una vez el `ScriptEditor` de Rhino 8 para que se cree esa carpeta. El nombre `py39-rh8` puede variar según la versión.)

   Si el proxy bloquea `pip`, descarga los *wheels* en otra compu con `pip download -r requirements.txt -d wheels` y luego instálalos con `pip install --no-index --find-links wheels -r requirements.txt`.

3. El script de Rhino (`rhino/las2brep_importer.py`) **no necesita nada extra**.

## Uso

```powershell
# 1. Ver qué trae el archivo (clases, RGB, extensión). --scan recorre todo el archivo para contar clases.
python -m las2brep inspect escaneo.las --scan

# 2. Convertir
python -m las2brep convert escaneo.las -o escaneo.las2brep.json

# Ejemplos de ajuste
python -m las2brep convert escaneo.las --voxel 0.03 --manhattan --wall-thickness 0.2
python -m las2brep convert escaneo.las --classes 6 --bbox 500100,2100050,500180,2100120
```

3. En Rhino: `_ScriptEditor` (Rhino 8) → abrir `rhino/las2brep_importer.py` → ▶. Elige el `.json`.
   - Las Breps se crean cerca del origen. El desplazamiento real del LAS queda guardado en `Document User Text → las2brep_origin`. Para ubicarlas en coordenadas reales, cambia `PLACE_AT_SOURCE_COORDS = True` en el script.
   - Si tu documento no está en metros, el script escala automáticamente.

### Terrenos y sitios (`--terrain`)

Si el archivo es un terreno, sobre todo si **no está clasificado** (todo en clase 0 o 1, típico de dron o fotogrametría), usa el modo terreno. Toda la nube se vuelve **una sola masa**, sin muros ni losas:

```powershell
# Solo el suelo: quita edificios y árboles
python -m las2brep convert points.las --terrain --ground -o terreno.las2brep.json

# La superficie tal cual, con lo que haya encima
python -m las2brep convert points.las --terrain -o superficie.las2brep.json

# Más detalle: celdas de ~5 m en un sitio de 1.5 km
python -m las2brep convert points.las --terrain --ground --terrain-max 300 -o terreno.las2brep.json
```

- No usa voxeles y lee el archivo por bloques. Un sitio de 70 M de puntos tarda del orden de un minuto.
- Ignora el **ruido** que queda muy por debajo de sus vecinos.
- Junto al JSON deja una **textura** `*.las2brep.png`: el color del escaneo visto desde arriba, tipo ortofoto.
- En Rhino el terreno queda como **sólido cerrado**: la superficie NURBS, los costados verticales y una base plana `--base` m abajo del punto más bajo. Sirve para `_ClippingPlane`, secciones y booleanas. Con `TERRAIN_SOLID = False` en el script queda solo la superficie.
- La textura se ve en modo **Rendered**. El PNG tiene que estar en la misma carpeta que el JSON.

| Parámetro | Default | Qué hace |
|---|---|---|
| `--terrain` | off | Activa el modo terreno. |
| `--ground` | off | Solo el suelo. Sin esta opción, la superficie promedia todo lo que hay en cada celda. |
| `--terrain-max` | 150 | Puntos de control por lado. La celda es el lado mayor entre este número. Hasta unos 500 sigue siendo manejable. |
| `--terrain-cell` | 1.0 | Celda mínima en m. |
| `--ground-window` | 40 | Ancho en m del objeto más grande que se quita. Súbelo si quedan naves o bodegas grandes. |
| `--ground-slope` | 0.2 | Pendiente máxima del suelo (0.2 = 20%). Súbelo en terreno muy inclinado si se "rasuran" las crestas. |
| `--texture` | 0.5 | m por pixel de la textura. 0 = sin textura. |
| `--base` | 5 | m bajo el punto más bajo para la base del sólido. |

Bajo un bosque muy cerrado no hay puntos de suelo, sobre todo con fotogrametría. Ahí el suelo se interpola desde los bordes.

### Parámetros principales

| Parámetro | Default | Qué hace |
|---|---|---|
| `--voxel` | 0.05 | Resolución en m. Más grande es más rápido y con menos detalle. |
| `--distance` | 0.03 | Tolerancia punto-plano. Súbelo si el escaneo es ruidoso. |
| `--min-points` | 300 | Tamaño mínimo de región, en voxeles. Súbelo para ignorar objetos chicos. |
| `--gap` | 0.15 | Un hueco mayor que este valor separa dos regiones del mismo plano. |
| `--snap` | 3 | Grados para enderezar muros y losas. Con 0 se desactiva. |
| `--manhattan` | off | Alinea todos los muros a una retícula ortogonal. |
| `--outline` | rect | `rect` = cajas limpias; `hull` = polígono convexo extruido. |
| `--wall-thickness` / `--slab-thickness` | 0.15 / 0.20 | Espesor del sólido. Con 0 se crean superficies planas. |
| `--classes` / `--ignore` | – / 7,18 | Filtra clases ASPRS. Por defecto descarta el ruido. |
| `--bbox` | – | Recorta una zona (en coordenadas del LAS). |
| `--residual N` | 0 | Agrega N puntos no asignados como nube ligera de referencia. |

## Rendimiento medido

| Archivo | Puntos | Tiempo | JSON |
|---|---|---|---|
| Sintético 600 MB (16 cuartos + terreno) | 17.6 M | 51 s | 119 KB, 96 cajas |

La memoria depende del número de voxeles, no del tamaño del archivo. Si te quedas sin RAM, sube `--voxel` o baja `--chunk`.

## Limitaciones conocidas (v0.1)

- **Cada cara escaneada es un sólido.** Un muro escaneado por ambos lados produce dos cajas paralelas. *Siguiente versión:* emparejar caras paralelas y usar la distancia entre ellas como espesor real.
- **Los vanos (ventanas y puertas) no se recortan.** El contorno `rect` los cubre. *Siguiente versión:* contorno por ráster con huecos.
- **Superficies curvas** (columnas redondas, bóvedas): quedan sin asignar. Puedes verlas con `--residual`. *Siguiente versión:* detectar cilindros.
- **Vegetación y mobiliario** no se convierten (son ruido para este objetivo).

## Estructura

```
las2brep/
  reader.py    lectura por bloques + voxeles (LAS/LAZ)
  segment.py   RANSAC, regiones conectadas, rectángulos
  terrain.py   rejilla de alturas, modo --terrain, filtro de suelo, textura
  export.py    formato JSON intermedio
  __main__.py  CLI
rhino/
  las2brep_importer.py   JSON -> Breps (RhinoCommon, Rhino 7/8)
tests/
  test_pipeline.py       prueba con LAS sintético
  test_terrain.py        modo terreno: suelo, ruido, textura
```

Pruebas: `pip install pytest && python -m pytest`
