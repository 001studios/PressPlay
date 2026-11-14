"""
prompt_builder.py - Cálculo matemático estricto de duración e inyección del Modo Granular.
"""
import re
import json
from pathlib import Path
from typing import Dict, Any
import pyperclip

PROMPT_BASE_PATH = Path("prompt_base.txt")
CONFIG_PATH = Path("config.json")

def calculate_ideal_duration_from_text(text: str) -> dict:
    """Calcula matemáticamente la duración real basada en densidad de palabras (3.2 palabras/seg)."""
    clean = re.sub(r"\[Nota:.*?\]", "", text or "")
    words = [w for w in re.findall(r"\b[a-zA-Z0-9áéíóúÁÉÍÓÚñÑüÜ]+\b", clean) if len(w) > 1]
    count = max(30, len(words))

    # Cálculo matemático exacto: palabras divididas a 3.2 palabras por segundo de locución
    target_sec = max(45, int(round(count / 3.2)))

    mins = target_sec // 60
    secs = target_sec % 60
    return {
        "word_count": count,
        "ideal_seconds": target_sec,
        "formatted_duration": f"{mins}:{secs:02d}"
    }

def parse_duration_to_seconds(duration_input: str) -> int:
    raw = str(duration_input).strip().lower()
    if ":" in raw:
        parts = [p.strip() for p in raw.split(":")]
        if len(parts) == 2: return (int(parts[0]) * 60) + int(parts[1])
        if len(parts) == 3: return (int(parts[0]) * 3600) + (int(parts[1]) * 60) + int(parts[2])
    m_match = re.search(r"(\d+)\s*m", raw)
    s_match = re.search(r"(\d+)\s*s", raw)
    if m_match or s_match:
        m = int(m_match.group(1)) if m_match else 0
        s = int(s_match.group(1)) if s_match else 0
        return (m * 60) + s
    if raw.isdigit(): return int(raw)
    return 135

def build_prompt_and_copy(article: Dict[str, Any], extracted_text: str, duration_input: str) -> Dict[str, Any]:
    if not PROMPT_BASE_PATH.exists():
        raise FileNotFoundError("No se encuentra prompt_base.txt en la raíz.")

    cfg = {}
    if CONFIG_PATH.exists():
        try: cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception: pass

    duration_seconds = parse_duration_to_seconds(duration_input)
    clip_count = int(cfg.get("loop_clips_count", 15))
    video_mode = cfg.get("video_mode", "loop")

    template = PROMPT_BASE_PATH.read_text(encoding="utf-8")

    titulo = article.get("titulo", "Sin título")
    fuente = article.get("source_nombre", article.get("fuente", "Noticias"))
    fecha = article.get("fecha_publicacion", "Fecha no especificada")
    texto = extracted_text if extracted_text.strip() else article.get("resumen", "Texto no disponible.")

    res = template.replace("{{DURACION_SEGUNDOS}}", str(duration_seconds))
    res = res.replace("{{CANTIDAD_CLIPS}}", str(clip_count))
    res = res.replace("{{NOTICIA_TITULO}}", titulo)
    res = res.replace("{{NOTICIA_FUENTE}}", fuente)
    res = res.replace("{{NOTICIA_FECHA}}", str(fecha))
    res = res.replace("{{NOTICIA_TEXTO}}", texto)

    # TRANSFORMACIÓN ESTRUCTURAL DIRECTA PARA MODO GRANULAR
    if video_mode == "granular_srt":
        marker_srt = "FORMATO SRT Y MOMENTOS CLAVE OBLIGATORIOS"
        marker_second = "SEGUNDO MENSAJE"

        pos_srt = res.find(marker_srt)
        pos_second = res.find(marker_second)

        granular_srt_rules = (
            "FORMATO SRT Y ASIGNACIÓN VISUAL GRANULAR (MODO OBLIGATORIO - CADA LÍNEA ETIQUETADA):\n"
            "- Numeración correlativa empezando en 1.\n"
            "- Timestamps continuos en formato HH:MM:SS,mmm --> HH:MM:SS,mmm.\n"
            "- El primer bloque empieza en 00:00:00,000 y el último termina en " + str(duration_seconds) + " segundos.\n"
            "- REGLA DE ORO DE ESPECIFICIDAD VISUAL (ESTRICTAMENTE PROHIBIDO SER GENÉRICO):\n"
            "  * En CADA UNO de los bloques del SRT debes colocar al inicio una etiqueta {video: término exacto} o {imagen: término exacto}.\n"
            "  * PROHIBIDO TERMINANTEMENTE colocar etiquetas genéricas como 'noticias en la actualidad', 'tecnologia', 'persona hablando', 'reportaje'. Si pones eso, el video se arruina.\n"
            "  * DEBES poner entidades, modelos, interfaces y hardware reales: {video: rtx 5090 cooling benchmark}, {video: steam client refund screen}, {video: sam altman devday speech}, {imagen: satya nadella microsoft portrait}.\n"
            "  * PREFERENCIA: usa casi siempre {video: ...} para que haya movimiento continuo en pantalla. Usa {imagen: ...} solo para logos o fotos clave.\n"
            "- LÍMITE DE REPETICIÓN: Ningún término puede repetirse más de 2 veces en todo el guion.\n"
            "- Máximo 2 líneas de texto hablado por bloque con 0 ms de pausa entre bloques encadenados.\n\n"
            "EJEMPLO EXACTO DE SALIDA GRANULAR:\n"
            "1\n00:00:00,000 --> 00:00:02,800\n{video: jensen huang leather jacket keynote} ¡Lo que acaba de anunciar el CEO de Nvidia dejó en ridículo a toda su competencia!\n\n"
            "2\n00:00:02,800 --> 00:00:05,600\n{video: rtx 5090 founders edition benchmark} ¡Duplicaron la memoria gráfica en el mismo socket sin disparar el consumo térmico!\n\n"
            "3\n00:00:05,600 --> 00:00:08,200\n{imagen: tsmc 3nm wafer microscopic photo} Y la clave estuvo en un nodo de silicio que nadie creía viable a escala masiva.\n\n"
        )

        granular_second_rules = (
            "SEGUNDO MENSAJE — NO REQUERIDO (MODO GRANULAR ACTIVO):\n"
            "Dado que operás en Modo Granular y cada bloque de subtítulo ya contiene su propia etiqueta visual {video:...} o {imagen:...}, NO debes entregar lista de palabras clave. Si el usuario te envía cualquier mensaje posterior, responde únicamente: 'GUION GRANULAR COMPLETO'."
        )

        if pos_srt != -1 and pos_second != -1:
            header_part = res[:pos_srt]
            res = header_part + granular_srt_rules + granular_second_rules
        else:
            res += "\n\n" + granular_srt_rules + "\n" + granular_second_rules

    try:
        pyperclip.copy(res)
    except Exception:
        pass

    return {
        "status": "ok",
        "duration_seconds": duration_seconds,
        "clip_count": clip_count,
        "video_mode": video_mode,
        "prompt": res
    }