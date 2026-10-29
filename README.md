# las-to-brep

Convierte nubes de puntos **LAS/LAZ** (escaneos láser o fotogrametría, de varios GB) en **Breps ligeras de Rhino**. **No genera mallas.** El resultado es fácil de cortar, seccionar, perforar y usar en booleanas.

Tiene dos modos:

| Modo | Para qué | Resultado en Rhino |
|---|---|---|
| **Edificios** (default) | Interiores y fachadas escaneados | Muros y losas como cajas sólidas, terreno de clase 2 como superficie |
| **Terreno** (`--terrain`) | Sitios, topografía, levantamientos con dron | Una sola masa del sitio: sólido cerrado con textura tipo ortofoto |

```
points.las (1+ GB)  ──►  py -m las2brep convert  ──►  points.las2brep.json (KB–MB)  ──►  Rhino: las2brep_importer.py  ──►  Breps
                                                     + points.las2brep.png (solo modo terreno)
```

## ¿Qué modo uso?

Corre primero `inspect` para ver qué clases trae el archivo:

```powershell
py -m las2brep inspect points.las --scan
```

- Si **todo está en clase 0 o 1** (sin clasificar, típico de dron o fotogrametría) y el archivo es un **terreno**, usa `--terrain`.
- Si es un **edificio** escaneado por dentro o por fuera, usa el modo normal.
- Si corres el modo normal sobre un terreno sin clasificar, salen **cientos de cajas planas esparcidas a distintas alturas**. No es un error del archivo: el modo edificios busca planos y parte las pendientes en franjas horizontales. El propio comando te avisa cuando no hay puntos de terreno (clase 2).

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

Los ejemplos usan `py -m las2brep`. Si activaste el `.venv`, también funciona `python -m las2brep`. Corre los comandos desde la carpeta del repo.

---

## Modo terreno (`--terrain`)

### Paso a paso

**1. Convertir.** La ruta del LAS puede estar en la red, entre comillas:

```powershell
# Alta calidad + ortofoto (recomendado para sitios de ~1-2 km)
py -m las2brep convert "\\servidor\carpeta\points.las" --terrain --ground --terrain-max 1000 --texture 0.25 -o terreno.las2brep.json
```

Al terminar tienes dos archivos en la carpeta donde corriste el comando:
- `terreno.las2brep.json`: la geometría del terreno (pocos MB).
- `terreno.las2brep.png`: la textura tipo ortofoto. **Debe quedarse junto al JSON.**

`-o` solo indica el nombre del resultado. Si ya existe un archivo con ese nombre, se sobrescribe sin avisar. Sin `-o`, el resultado toma el nombre del LAS.

**2. Importar en Rhino.** `_ScriptEditor` (Rhino 8) → abre `rhino/las2brep_importer.py` → ▶ → elige el `.json`.
- El terreno queda en la capa `LAS2Brep::Terreno` como **sólido cerrado**. Puedes comprobarlo con `_What`: debe decir *closed polysurface*.
- Pon la vista en modo **Rendered** para ver la textura.

**3. Cortes y secciones.**
- `_ClippingPlane` para cortes en vivo.
- `_Section` / `_Contour` para curvas de corte o curvas de nivel.
- Como es un sólido cerrado, también funcionan las booleanas: excavar plataformas, restar edificios, etc.

### Recetas

| Uso | Comando (agregar a `py -m las2brep convert points.las`) |
|---|---|
| Solo el suelo, alta calidad + ortofoto | `--terrain --ground --terrain-max 1000 --texture 0.25` |
| Solo el suelo, ligero | `--terrain --ground --terrain-max 300 --texture 0.5` |
| Superficie tal cual (con árboles y edificios) | `--terrain --terrain-max 1000 --texture 0.25` |
| Sin textura | agrega `--texture 0` |
| Solo una zona | agrega `--bbox xmin,ymin,xmax,ymax` (coordenadas del LAS) |

### Cómo funciona

1. **Lectura por bloques.** Lee el LAS de 2 M en 2 M puntos (`--chunk`), sin cargarlo completo en RAM y sin voxeles. Respeta `--classes`, `--ignore` (por defecto descarta las clases de ruido 7 y 18) y `--bbox`.
2. **Rejilla fina.** Cada punto cae en una celda de ~1 m. Por celda se guarda la altura **mínima** (para el suelo), la **suma de alturas** y el **número de puntos** (para el promedio).
3. **Ruido.** Las celdas que quedan más de 2 m por debajo de la mediana de sus vecinas se descartan. Así desaparecen los puntos falsos muy abajo, típicos de la fotogrametría.
4. **Suelo (`--ground`).** Filtro morfológico progresivo (SMRF, Pingel et al. 2013). Aplica "aperturas" con ventanas cada vez más grandes, hasta `--ground-window`. Lo que sobresale más de lo que permite la pendiente `--ground-slope` se marca como objeto (edificio, árbol, vehículo) y se descarta. Sin `--ground`, cada celda usa el **promedio** de todo lo que tiene encima.
5. **Puntos de control.** Las celdas finas se promedian en la rejilla final de la superficie. Su tamaño es el lado mayor del sitio entre `--terrain-max`. Cada altura se coloca en el **centro** de su celda.
6. **Huecos.** Las celdas sin datos se rellenan por interpolación lineal desde las vecinas, por ejemplo bajo un edificio quitado o donde no hubo escaneo.
7. **Textura.** En paralelo se promedia el color RGB en pixeles de `--texture` m, visto desde arriba con el norte arriba. Se guarda como PNG sin dependencias extra.
8. **JSON.** Guarda la rejilla, la cota de la base (`--base` m bajo el punto más bajo) y los datos de la textura.
9. **Rhino.**
   - Crea una superficie NURBS con los puntos de control (grado 3).
   - Levanta costados verticales (superficies regladas) desde cada borde hasta la base, tapa abajo y une todo en un **sólido cerrado**.
   - Asigna un material con el PNG y lo proyecta desde arriba (mapeo planar), de modo que la imagen cae exactamente sobre sus coordenadas X/Y.

### Parámetros del modo terreno

| Parámetro | Default | Qué hace |
|---|---|---|
| `--terrain` | off | Activa el modo terreno. Ignora la clasificación y no detecta muros ni losas. |
| `--ground` | off | Solo el suelo. Sin esta opción, la superficie promedia todo lo que hay en cada celda. |
| `--terrain-max` | 150 | Puntos de control por lado. Más alto = más detalle. 1000 en un sitio de 1.5 km da celdas de ~1.5 m. |
| `--terrain-cell` | 1.0 | Celda mínima en m. Evita celdas más finas que esto en sitios chicos. |
| `--ground-window` | 40 | Ancho en m del objeto más grande que se quita. Súbelo (80, 120) si quedan naves o bodegas grandes. |
| `--ground-slope` | 0.2 | Pendiente máxima del suelo (0.2 = 20%). Súbelo (0.4, 0.6) en terreno muy inclinado si se "rasuran" crestas o taludes. |
| `--texture` | 0.5 | m por pixel de la textura. 0.25 = más nítida y más pesada. 0 = sin textura. |
| `--base` | 5 | m bajo el punto más bajo del terreno para la base del sólido. |
| `--bbox` | – | Recorta una zona (coordenadas del LAS). Con los mismos puntos de control, más detalle. |

Opciones dentro del script de Rhino (`rhino/las2brep_importer.py`, arriba del archivo):

| Opción | Default | Qué hace |
|---|---|---|
| `TERRAIN_SOLID` | `True` | `False` = solo la superficie, sin costados ni base. |
| `TERRAIN_TEXTURE` | `True` | `False` = no aplica la textura aunque exista el PNG. |
| `PLACE_AT_SOURCE_COORDS` | `False` | `True` = ubica todo en las coordenadas reales del LAS. |

### Problemas comunes

| Síntoma | Causa | Solución |
|---|---|---|
| Cientos de cajas planas esparcidas | Terreno sin clasificar en modo edificios | Usa `--terrain` |
| Quedan bodegas o naves como bultos | Son más anchas que `--ground-window` | Sube `--ground-window` |
| Crestas, bordos o taludes aplanados | Son más empinados que `--ground-slope` | Sube `--ground-slope` |
| No se ve la textura | Vista en Shaded, o el PNG no está junto al JSON | Modo **Rendered**; copia el PNG a la carpeta del JSON |
| Rhino dice que no pudo cerrar el terreno | No se unieron los costados | Se importa solo la superficie. Avísanos con el JSON |
| Se queda sin memoria | La textura fina es la que más RAM usa | `--texture 0.5` o recorta con `--bbox` |
| Bajo un bosque el suelo se ve "plano" | No hay puntos de suelo bajo un dosel cerrado (sobre todo con fotogrametría) | Es interpolado desde los bordes del bosque; no hay dato real ahí |

---

## Modo edificios (default)

En arquitectura casi todo es plano (muros, losas, techos, fachadas). Hay tres formas de llegar a geometría:

| Enfoque | Resultado | Problema |
|---|---|---|
| Triangular (Poisson, Ball pivoting) | Malla | Pesada, llena de ruido, difícil de editar. Justo lo que no queremos. |
| Ajustar NURBS libres a todo | Superficies complejas | Lento, frágil y no da sólidos cerrados. |
| **Detectar primitivas (este repo)** | **Planos → cajas, terreno → 1 superficie NURBS** | Simplifica: los detalles finos se pierden (a propósito). |

### Cómo funciona

1. **Lee el LAS por bloques** (`laspy`), sin cargar el archivo completo en RAM.
2. **Reduce a voxeles** (5 cm por defecto): de decenas de millones de puntos a 1–3 millones, separando por clase y promediando color.
3. **Terreno** (clase ASPRS 2): rejilla de alturas → una superficie NURBS de ≤150×150 puntos de control. Si el archivo no está clasificado, usa `--terrain`.
4. **Planos** con RANSAC + mínimos cuadrados. Endereza muros a vertical y losas a horizontal (`--snap`). Cada plano se separa en regiones conectadas y cada región se vuelve un rectángulo de área mínima.
5. **Exporta un JSON pequeño** solo con primitivas. Un archivo de 600 MB de prueba produjo 119 KB.
6. **En Rhino**, el script crea las Breps con `Box.ToBrep()`. Cada región queda como una caja cerrada de 6 caras con el espesor que elijas.

### Uso

```powershell
py -m las2brep convert escaneo.las -o escaneo.las2brep.json

# Ejemplos de ajuste
py -m las2brep convert escaneo.las --voxel 0.03 --manhattan --wall-thickness 0.2
py -m las2brep convert escaneo.las --classes 6 --bbox 500100,2100050,500180,2100120
```

En Rhino: `_ScriptEditor` (Rhino 8) → abrir `rhino/las2brep_importer.py` → ▶. Elige el `.json`.
- Las Breps se crean cerca del origen. El desplazamiento real del LAS queda guardado en `Document User Text → las2brep_origin`. Para ubicarlas en coordenadas reales, cambia `PLACE_AT_SOURCE_COORDS = True` en el script.
- Si tu documento no está en metros, el script escala automáticamente.

### Parámetros

| Parámetro | Default | Qué hace |
|---|---|---|
| `--voxel` | 0.05 | Resolución en m. Más grande es más rápido y con menos detalle. |
| `--distance` | 0.03 | Tolerancia punto-plano. Súbelo si el escaneo es ruidoso. |
| `--min-points` | 300 | Tamaño mínimo de región, en voxeles. Súbelo para ignorar objetos chicos. |
| `--max-planes` | 400 | Máximo de planos a detectar. Con 0 no detecta planos. |
| `--gap` | 0.15 | Un hueco mayor que este valor separa dos regiones del mismo plano. |
| `--snap` | 3 | Grados para enderezar muros y losas. Con 0 se desactiva. |
| `--manhattan` | off | Alinea todos los muros a una retícula ortogonal. |
| `--outline` | rect | `rect` = cajas limpias; `hull` = polígono convexo extruido. |
| `--wall-thickness` / `--slab-thickness` | 0.15 / 0.20 | Espesor del sólido. Con 0 se crean superficies planas. |
| `--terrain-classes` | 2 | Clases que se tratan como terreno en este modo. |
| `--classes` / `--ignore` | – / 7,18 | Filtra clases ASPRS. Por defecto descarta el ruido. |
| `--bbox` | – | Recorta una zona (en coordenadas del LAS). |
| `--residual N` | 0 | Agrega N puntos no asignados como nube ligera de referencia. |

---

## ¿Y los "materiales"?

Un LAS no guarda materiales como Rhino. Guarda por punto la **clasificación** (terreno, edificio, vegetación...), el **color RGB** (si el escáner o la cámara lo capturó) y la **intensidad**. El tool los traduce así:

- **Clase → capa** `LAS2Brep::<Clase>::<Muros|Losas|Inclinados>`, con un material de render por capa.
- **RGB → color de objeto**: el color promedio de los puntos de cada pieza. Es un color por objeto, no por punto.
- **Terreno (`--terrain`) → textura tipo ortofoto**: una imagen PNG con el color visto desde arriba, proyectada sobre el sólido. Los costados verticales muestran la imagen estirada; es normal.
- Cada Brep lleva *user text* (`las2brep_class`, `las2brep_kind`, `las2brep_points`, `las2brep_rms`, `las2brep_mode`) para filtrar o hacer schedules.

No hay manera de sacar materiales "reales" (concreto, pasto, asfalto) de un LAS: solo hay color por punto.

## Rendimiento medido

| Archivo | Puntos | Comando | Tiempo | Memoria pico | Salida |
|---|---|---|---|---|---|
| Sintético 600 MB (16 cuartos + terreno) | 17.6 M | modo edificios | 51 s | – | JSON 119 KB, 96 cajas |
| Sintético 1.2 × 1.5 km con vegetación | 8 M | `--terrain --ground --terrain-max 300` | 9 s | 1.2 GB | JSON 0.5 MB + PNG |
| Mismo sitio | 8 M | `--terrain --ground --terrain-max 1000 --texture 0.5` | 37 s | 1.2 GB | JSON 5.4 MB + PNG |
| Mismo sitio | 8 M | `--terrain --ground --terrain-max 1000 --texture 0.25` | 42 s | 4 GB | JSON 5.4 MB + PNG |

En el modo edificios la memoria depende del número de voxeles: si te quedas sin RAM, sube `--voxel` o baja `--chunk`. En el modo terreno depende del tamaño de las rejillas, no del archivo; lo que más pesa es la textura fina. La lectura crece con el número de puntos: un archivo real de 70 M de puntos tarda del orden de 1–2 minutos, más si se lee desde la red. En el sitio sintético, el suelo quedó a menos de 5 cm del real en el 95% de los nodos, con 15% de puntos de vegetación.

El tamaño de PNG de la tabla es de datos sintéticos (color muy uniforme). Con una ortofoto real, el PNG pesa varias veces más.

## Limitaciones conocidas

**Modo terreno**
- Bajo un bosque cerrado o un edificio grande no hay suelo real: se interpola.
- La textura es una vista cenital. En taludes casi verticales y en los costados del sólido se ve estirada.
- La superficie NURBS suaviza un poco los quiebres bruscos (bordes de banquetas, muros de contención).
- La importación en Rhino (sólido y textura) no tiene pruebas automáticas: se prueba a mano en Rhino.

**Modo edificios**
- **Cada cara escaneada es un sólido.** Un muro escaneado por ambos lados produce dos cajas paralelas. *Siguiente versión:* emparejar caras paralelas y usar la distancia entre ellas como espesor real.
- **Los vanos (ventanas y puertas) no se recortan.** El contorno `rect` los cubre. *Siguiente versión:* contorno por ráster con huecos.
- **Superficies curvas** (columnas redondas, bóvedas): quedan sin asignar. Puedes verlas con `--residual`. *Siguiente versión:* detectar cilindros.
- **Vegetación y mobiliario** no se convierten (son ruido para este objetivo).

## Estructura

```
las2brep/
  reader.py    lectura por bloques + voxeles (LAS/LAZ)
  segment.py   RANSAC, regiones conectadas, rectángulos (modo edificios)
  terrain.py   rejilla de alturas, modo --terrain, filtro de suelo, ruido, textura
  export.py    formato JSON intermedio + escritor de PNG
  __main__.py  CLI
rhino/
  las2brep_importer.py   JSON -> Breps (RhinoCommon, Rhino 7/8): cajas, sólido de terreno, textura
tests/
  test_pipeline.py       modo edificios con LAS sintético
  test_terrain.py        modo terreno: suelo, ruido, textura, media celda
```

Pruebas: `pip install pytest && python -m pytest`
