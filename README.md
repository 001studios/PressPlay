# ⚡ PressPlay

Este proyecto lo hice para fines personales, el proyecto no provee ningún binario y las
fuentes RSS son de acceso público.

🇦🇷 ¡Este proyecto poco útil pero innovador está hecho en ARGENTINA! VAMOOOOOOO 🇦🇷
<img width="447" height="447" alt="image" src="https://github.com/user-attachments/assets/16262ab7-d7a2-4707-bf89-7b6315da55b7" />

En realidad el proyecto es muy simple, no tiene ninguna función de IA adentro, todo es puro
Python.

Lo que sí tiene IA, además del SRT que genera la IA mediante el prompt que te proporciona el
programa, es el programa en sí. Aunque yo no programé el código a mano línea por línea, sí
se me ocurrió la idea principal y fui quien dio todas las instrucciones para lograr un
programa pulido y con un buen algoritmo.

Así que, como no soy un experto en Python, a continuación voy a dejar el texto que dejó la
IA para cualquiera que quiera forkear el proyecto y modificarlo para adaptarlo a su gusto:

# 📖 Arquitectura interna y documentación técnica

Este documento detalla la arquitectura interna, el flujo de datos, los algoritmos
matemáticos y los contratos de interfaz de PressPlay. Está diseñado para que cualquier
desarrollador pueda auditar, extender o modificar cualquier módulo del sistema sin romper la
sincronización del pipeline.

## 1. Visión General y Filosofía del Sistema

PressPlay es una estación de trabajo automatizada que convierte noticias web en videoensayos
y piezas audiovisuales cortas de alta retención para YouTube, TikTok y Reels.

El sistema resuelve tres problemas críticos en la automatización de video:

## 1. La brecha temporal de la locución: Un guion SRT plano tiene marcas de tiempo
teóricas; la voz sintetizada por una IA con entonación humana real nunca dura lo mismo que
el texto plano. PressPlay mide los milisegundos reales de la voz y recalcula la línea de
tiempo antes de montar el video y los subtítulos.

## 2. La degradación visual por rate-limiting: La descarga masiva de fragmentos de
video suele provocar bloqueos de IP (HTTP 429) o descargas de imágenes estáticas disfrazadas
de video. PressPlay implementa rotación de proveedores, cliente Android en yt-dlp y
validación de fotogramas negros en FFmpeg.

## 3. El sesgo y la duplicación informativa: En lugar de mostrar 20 artículos
idénticos sobre el mismo tema, el motor agrupa las noticias por solapamiento de entidades
clave (Clustering) y filtra el sensacionalismo mediante un analizador de clickbait
multicapa.

## 2. Mapa de Archivos y Responsabilidades

```text
pressplay/
├── app/
│   ├── static/          # Frontend Web (Single Page Application nativa)
│   │   ├── app.js       # Orquestador reactivo de la UI, polling y eventos
│   │   ├── index.html   # Estructura semántica de paneles y modales
│   │   └── style.css    # Sistema de diseño oscuro de alto contraste
│   ├── clips.py         # Adquisición de video, Query Crafting y anti-pantalla negra
│   ├── db.py            # Capa de persistencia SQLite (WAL mode) y estado global
│   ├── extractor.py     # Web scraping y extracción de cuerpo sin muros de pago
│   ├── feed.py          # Lector concurrente de feeds RSS y caché de imágenes
│   ├── main.py          # API REST con FastAPI y orquestación de tareas en segundo plano
│   ├── prompt_builder.py# Conteo estricto de palabras y formateador de prompts
│   ├── recommender.py   # Algoritmo BM25, detector de clickbait y clustering
│   ├── renderer.py      # Ensamblador FFmpeg con aceleración GPU y subtítulos ASS
│   └── tts.py           # Locución IA con inyección obligatoria de rules.txt
├── rules.txt            # Directivas obligatorias de estilo y dicción para la voz
├── config.json          # Archivo de configuración activo local
├── config.example.json  # Plantilla de configuración para distribución
├── requirements.txt     # Dependencias de Python
└── newstovideo.db       # Base de datos SQLite (se genera automáticamente)
```

## 3. Flujo de Datos End-to-End

El ciclo de vida de un video sigue 7 fases secuenciales:

```text
[1. RSS Sync] ──> [2. DB & Clustering] ──> [3. Feed UI & Clickbait]
                                                    │
                                                    ▼
[4. Extracción Web & Prompt] <──────────────────────┘
        │
        ▼ (El usuario genera el guion SRT con la IA)
[5. Síntesis TTS & Synced Blocks] (Mide duración real en milisegundos)
        │
        ▼
[6. Adquisición B-Roll & Validación Anti-Negro]
        │
        ▼
[7. Generador ASS TikTok & Render GPU FFmpeg] ──> [MP4 Final]
```

### Fase 1: Ingesta y Sincronización de Fuentes (feed.py)

- Se consultan concurrentemente todas las fuentes activas en la tabla sources utilizando
  concurrent.futures.ThreadPoolExecutor(max_workers=6).

- Se normalizan fechas en formato ISO y se descargan los thumbnails en disco bajo
  cache/images/{hash}.jpg para evitar enlaces rotos o bloqueos por hotlinking.

### Fase 2: Normalización, Filtrado y Clustering (recommender.py)

- Filtro de Temas Ocultos: Se eliminan acentos y diacríticos con unicodedata tanto en
  los términos bloqueados como en los textos de los artículos. Si hay coincidencia, el
  artículo se descarta antes de llegar a memoria.

- Exclusión de Guardados: Artículos almacenados en saved_articles o que hablen del mismo
  evento quedan excluidos del feed general.

- Clustering: Se extraen conjuntos de entidades (extract_core_entities). Si dos noticias
  comparten 2 o más entidades específicas o superan un 45% de solapamiento (Overlap
  Coefficient), se unifican bajo una sola tarjeta representativa con una lista de
  fuentes agregadas.

### Fase 3: Evaluación de Clickbait (recommender.py)

- Cada titular se analiza en tiempo real evaluando 7 vectores sintácticos: omisión de
  sujeto, frases de intriga, comillas descontextualizadas, superlativos amarillistas,
  listas numéricas y mayúsculas. Devuelve un porcentaje (0% a 100%) y un nivel de
  severidad.

### Fase 4: Extracción y Cálculo de Duración (extractor.py y prompt_builder.py)

- Al hacer clic en "Copiar para Video", el backend descarga el contenido completo del
  artículo con trafilatura. Si hay muro de pago, recurre a selectores BeautifulSoup de
  respaldo o al resumen provisto por el RSS.

- Se cuenta el número real de palabras útiles y se calcula la duración exacta:

$$
\text{Duración (segundos)} = \max\left(45, \text{round}\left(\frac{\text{Palabras}}{3.2}\right)\right)
$$

- Si en Ajustes está activo el Modo Granular, prompt_builder.py reescribe el bloque de
  salida para obligar a la IA a incluir {video: ...} o {imagen: ...} en cada línea del
  SRT con un máximo de 2 usos por recurso.

### Fase 5: Locución y Medición de Tiempo Real (tts.py)

- Se divide el SRT en bloques. Cada bloque de texto se procesa con Qwen-TTS o Edge TTS
  inyectando las directivas de rules.txt.

- Cada fragmento de audio se exporta a WAV mono a 44.1 kHz.

- Punto crítico de sincronización: El sistema lee la cabecera WAV de cada archivo
  (wave.open), obtiene el número exacto de frames y calcula la duración real en
  milisegundos (actual_dur = frames / 44100.0).

- Se genera una estructura de datos llamada synced_blocks, donde cada bloque tiene un
  start y un end basados en el audio real pronunciado, no en los timestamps del texto
  original.

### Fase 6: Adquisición de B-Roll Resiliente (clips.py)

- Query Crafting: Para evitar videos con webcams o podcasts, la búsqueda en YouTube se
  transforma agregando modificadores visuales (footage, showcase, b-roll) y negando
  ruido (-podcast -reaction -streamer).

- Evasión de Bloqueos (Rate Limiting): yt-dlp invoca el cliente Android/TV
  (--extractor-args "youtube:player_client=android"), aplica jitter aleatorio y, en Modo
  Granular, rota entre proveedores (YouTube, Pexels, Pixabay, Wikimedia).

- Validación Anti-Pantalla Negra: Se muestrean 4 fotogramas del clip descargado en
  escala de grises con FFmpeg (scale=1:1,format=gray). Si el valor medio de luminancia
  es \le 26, el video se descarta automáticamente y se descarga un sustituto.

### Fase 7: Subtítulos Dinámicos y Render GPU (renderer.py)

- Se generan subtítulos en formato Advanced SubStation Alpha (.ass):

- Cada bloque de audio se fragmenta en ráfagas cortas de 2 a 3 palabras.
- Se aplica tipografía de impacto (48px, negrita, borde negro de 4px).
- La palabra hablada en cada micro-fracción de tiempo se agranda un 18%
  (\fscx118\fscy118) y adopta el color de resaltado configurado.

- Se llama a FFmpeg con el códec acelerado por hardware NVIDIA (-c:v h264_nvenc -preset
  p4 -cq 23), integrando música de fondo opcional con atenuación (ducking) al 10%.

## 4. Desglose Técnico de Módulos

### app/recommender.py

## 1. Algoritmo de Detección de Clickbait (analyze_clickbait)

Evalúa el titular mediante una suma ponderada multivariable:

- Omisión del sujeto (+28 pts): Expresiones indefinidas como "un famoso", "una conocida
  empresa", "un histórico jugador" seguidas de verbos de acción.
- Citas descontextualizadas (+22 pts): Texto entre comillas que sugiere declaraciones
  explosivas.
- Preguntas en el titular (+24 pts): Titulares con signos de interrogación para generar
  incertidumbre artificial.
- Signos y puntos suspensivos (+18 a +20 pts): Presencia de !, ¡, ... o combinaciones.
- Fórmulas de suspenso (+32 pts): Inicios con "por qué", "la razón de", "este es el",
  "lo que nadie sabe".
- Léxico de sobrecarga emocional (+17 pts por coincidencia, máx 34): Búsqueda en el
  conjunto SENSATIONAL_LEXICON (bomba, furor, brutal, estalló, lapidario, etc.).
- Listas anzuelo (+25 pts): Patrones de números asociados a curiosidad ("5 claves", "3
  motivos").
- Mayúsculas sostenidas (+25 pts): Ratio de mayúsculas superior al 28% del texto.

| Rango de Puntaje | Clasificación         | Clase CSS   |
| :--------------: | :-------------------: | :---------: |
| $\ge 58\%$       | Clickbait Alto        | `cb-red`    |
| $32\% - 57\%$    | Sensacionalista       | `cb-yellow` |
| $< 32\%$         | Informativo / Fáctico | `cb-green`  |

## 2. Clustering por Solapamiento de Entidades (are_articles_same_event y group_articles_into_clusters)

En lugar de depender de similitudes coseno o Jaccard globales (que fallan con títulos de
longitudes dispares), se utiliza el Coeficiente de Solapamiento (Overlap) sobre entidades
centrales:

- Se normalizan las palabras con strip_accents.
- Se extraen términos con longitud \ge 4 o que contengan números identificadores de
  producto (ej: 5090, ps5, gta6).
- Si dos noticias comparten \ge 2 entidades clave o el ratio:

$$
\text{Overlap}(A, B) = \frac{|A \cap B|}{\min(|A|, |B|)} \ge 0.45
$$

se consideran el mismo acontecimiento y se agrupan en una única estructura de clúster.

### app/tts.py

#### Medición de Audio y Generación de synced_blocks

La función synthesize_srt_to_audio coordina la síntesis asíncrona mediante un semáforo
(asyncio.Semaphore(2)). Para cada bloque de subtítulo, calcula la duración física real del
archivo .wav generado:

```python
with wave.open(str(norm_wav), "rb") as w:
    actual_dur = w.getnframes() / float(sample_rate)
```

Construye la lista synced_blocks:

```python
synced_blocks.append({
    "index": block["index"],
    "start": current_timeline,
    "end": current_timeline + actual_dur,
    "duration": actual_dur,
    "text": block["text"],
    "key_visual": block.get("key_visual")
})
current_timeline += actual_dur + pause_sec
```

Esto garantiza que la duración de los clips de video en el Modo Granular y la aparición de
los subtítulos dependan de la velocidad real de la voz sintetizada.

### app/clips.py

## 1. Query Crafting para Búsqueda en YouTube (build_precision_youtube_query)

Evita la descarga de contenido irrelevante estructurando la consulta de la siguiente forma:

- Si la keyword contiene términos de videojuegos: añade "gameplay 4k 60fps" o
  "walkthrough gameplay".
- Si contiene hardware: añade "showcase b-roll footage" o "trailer hardware review".
- En otros casos: añade "footage b-roll" o "news report clip".
- Filtro de exclusión negativo: Añade -podcast -reaction -reaccionando -stream
-webcam -vlog a la cadena de búsqueda enviada a yt-dlp.

## 2. Evasión de Bloqueos de YouTube

Para sortear el código de error HTTP 429 Too Many Requests:

- Se invoca yt-dlp con --extractor-args "youtube:player_client=android,web". Los
  endpoints móviles no aplican las restricciones de bots del cliente web de escritorio.
- Se aplica un jitter aleatorio de espera entre 0.6 y 1.4 segundos.
- Si se detecta un 429 en stderr, se activa un cooldown temporal en
  PLATFORM_COOLDOWNS["youtube"] de 90 segundos, provocando que las siguientes descargas
  salten inmediatamente a Pexels, Pixabay o Wikimedia sin reintentar inútilmente.

## 3. Validación de Fotogramas Negros (is_clip_black_or_corrupt)

FFmpeg toma muestras en los instantes equivalentes al 20%, 40%, 60% y 80% de la duración
total del video:

```bash
ffmpeg -v error -ss {ts} -i {clip} -vframes 1 -vf "scale=1:1,format=gray" -f rawvideo -
```

Esto retorna 1 byte por fotograma con el valor de luminancia (0 a 255). Si en ninguno de los
puntos evaluados el valor supera 26, el clip se clasifica como pantalla negra y se descarta.

### app/renderer.py

#### Generación de Subtítulos Dinámicos ASS (generate_tiktok_ass_subtitles)

Transforma los synced_blocks en eventos ASS divididos en ráfagas de 2 a 3 palabras. Para
cada palabra activa dentro del chunk, se inyectan etiquetas de estilo avanzadas:

- {\c&H00BBGGRR&}: Cambia el color del texto al color de resaltado configurado.
- {\fscx118\fscy118}: Agranda la palabra activa un 18% en los ejes X e Y.
- {\rTikTokMain}: Restablece el estilo base (blanco con borde negro) para las palabras
  inactivas.

El archivo .ass se compila directamente en la línea de comandos de FFmpeg a través del
filtro de video:

```bash
-filter_complex "[0:v]fps=30,format=yuv420p,subtitles='ruta_escapada.ass'[vout]"
```

## 5. Guía para Modificar y Extender el Proyecto

¿Cómo agregar un nuevo motor de voz (ej: ElevenLabs o Piper local)?

## 1. En app/tts.py:

- Agrega el identificador del motor en list_available_voices(engine).
- Crea la función de síntesis correspondiente (ej: synthesize_with_elevenlabs(text,
  voice_id, out_wav)).
- En synthesize_single_block(), agrega una condición elif eng == "tu_motor": que invoque
  tu función y genere el archivo WAV normalizado.

## 2. En app/main.py:

- En SettingsPayload, asegúrate de registrar cualquier clave de API necesaria.

## 3. En app/static/index.html:

- Agrega la opción dentro del selector <select id="cfg-tts-engine">.

¿Cómo agregar un nuevo proveedor de video de stock?

## 1. En app/clips.py:
- Implementa la función de descarga (ej: try_download_from_shutterstock(keyword,
  api_key, output_path)).
- En acquire_validated_clip_multi_platform(), agrega el proveedor a la lista available y
  mapea su llamada dentro del bucle de rotación.
- Todo video nuevo debe pasar obligatoriamente por normalize_video() para garantizar
  resolución unificada y códec YUV420p.

¿Cómo cambiar la fuente o posición de los subtítulos dinámicos?

## 1. Abre app/renderer.py en la función generate_tiktok_ass_subtitles().

## 2. Modifica la línea Style: TikTokMain,...:

- Cambia Arial Black por la fuente instalada en el sistema que prefieras (ej: Impact,
  Montserrat ExtraBold).
- Ajusta margin_v para cambiar la altura vertical en pantalla (un valor mayor sube los
  subtítulos; un valor menor los baja).
- Cambia \fscx118\fscy118 para aumentar o reducir el porcentaje de aumento en el pop-up
  zoom.

¿Cómo calibrar la sensibilidad del detector de Clickbait?

## 1. Abre app/recommender.py en la función analyze_clickbait().

## 2. Puedes ajustar los puntajes asignados a cada regla en las variables score +=
X o incorporar nuevas expresiones regulares a la lista CLICKBAIT_PATTERNS.

## 3. Para modificar los rangos de clasificación, cambia los umbrales de decisión
(final_score >= 58 para rojo y final_score >= 32 para amarillo).

## 6. Requisitos del Sistema y Dependencias

- Python: 3.10 o superior (probado en 3.10, 3.11 y 3.13).
- FFmpeg: Se requiere una compilación moderna que incluya soporte para libass
  (subtítulos) y, preferentemente, h264_nvenc para codificación asistida por hardware
  NVIDIA. Debe estar registrado en las variables de entorno del sistema (PATH).
- Base de datos: SQLite3 nativo con modo transaccional. No requiere configuración de
  servidores externos.
- Acceso a red: Conexión saliente para peticiones HTTP/HTTPS hacia feeds RSS, APIs de
  stock y YouTube. Para uso sin conexión, se pueden colocar videos .mp4 locales dentro
  de la carpeta clips_custom/.

# 🛠️ Cómo usar el programa

En fin, esa fue la larga explicación de la IA. Si quieren publicar el proyecto, modificarlo
o lo que sea, pueden hacer lo que quieran ya que este proyecto está publicado bajo la
Licencia MIT, así que solo pido créditos.

Para los que quieran saber cómo usar el programa:

## 1. Python instalado: Primero necesitan tener Python instalado, es súper fácil y
no voy a enseñar cómo hacerlo acá.

## 2. Instalar librerías: Instalar todas las librerías necesarias mediante:

pip install -r requirements.txt

## 3. Ejecutar el programa: Una vez instalado, ejecutas el programa con:

python -m uvicorn app.main:app --reload --port 8000

## 4. Usar la app: Solo tendrás que abrir http://localhost:8000 en tu navegador de
preferencia y listo.

A medida que vayas scrolleando y añadiendo tus portales favoritos, el algoritmo se ajustará
a tus intereses automáticamente y es una experiencia bastante satisfactoria.

Eso sí: si eliges el Modo Granular (que descarga un clip por cada frase y no genera un bucle
de videos), la generación del video podría demorarse entre 10 a 30 minutos dependiendo de la
duración de este.

# 📄 Licencia

MIT License

Copyright (c) 2025 PressPlay Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy of this
software and associated documentation files (the "Software"), to deal in the Software
without restriction, including without limitation the rights to use, copy, modify, merge,
publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons
to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or
substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR
PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE
FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
DEALINGS IN THE SOFTWARE.
