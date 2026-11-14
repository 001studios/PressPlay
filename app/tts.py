"""
tts.py - Motor de síntesis con medición temporal exacta y reglas estrictas de rules.txt.
Retorna el audio final y la estructura synced_blocks con las duraciones reales de la voz.
"""
import re
import wave
import hashlib
import asyncio
import subprocess
import shutil
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import edge_tts

TTS_CACHE_DIR = Path("cache/tts")
TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
RULES_FILE = Path("rules.txt")

TAG_REGEX = re.compile(r"\{([^}]+)\}")

FALLBACK_VOICES = [
    "es-ES-AlvaroNeural",
    "es-MX-JorgeNeural",
    "es-ES-ElviraNeural",
    "es-US-AlonsoNeural"
]

QWEN_FALLBACK_MAP = {
    "Alex": "es-ES-AlvaroNeural",
    "Dora": "es-ES-ElviraNeural",
    "Benjamin": "es-MX-JorgeNeural",
    "Bella": "es-MX-DaliaNeural",
    "David": "es-ES-AlvaroNeural",
    "Sarah": "es-ES-ElviraNeural"
}

QWEN_SPEAKERS_CATALOG = [
    {"short_name": "Alex", "gender": "Hombre", "locale": "es", "friendly_name": "[Qwen] Alex (Hombre - Tono Dinámico)"},
    {"short_name": "Dora", "gender": "Mujer", "locale": "es", "friendly_name": "[Qwen] Dora (Mujer - Expresiva y Clara)"},
    {"short_name": "Benjamin", "gender": "Hombre", "locale": "es", "friendly_name": "[Qwen] Benjamin (Hombre - Narrativa)"},
    {"short_name": "Bella", "gender": "Mujer", "locale": "es", "friendly_name": "[Qwen] Bella (Mujer - Enérgica)"},
    {"short_name": "David", "gender": "Hombre", "locale": "es", "friendly_name": "[Qwen] David (Hombre - Seguro)"},
    {"short_name": "Sarah", "gender": "Mujer", "locale": "es", "friendly_name": "[Qwen] Sarah (Mujer - Fresca)"}
]

QWEN_STYLES = {
    "youtube_energetic": "habla con dinamismo, energía y entonación entretenida para un video de YouTube",
    "sarcastic_tech": "habla con ironía sutil, humor seco y comentarios directos",
    "cinematic_mystery": "narración con misterio, tensión y cambios de ritmo intrigantes",
    "casual_friend": "habla de forma relajada y cercana, como si le contaras una historia a un amigo",
    "custom": ""
}

def load_strict_rules() -> str:
    """Lee el archivo rules.txt de la raíz y devuelve las directivas obligatorias."""
    if RULES_FILE.exists():
        try:
            content = RULES_FILE.read_text(encoding="utf-8").strip()
            clean_lines = [l for l in content.splitlines() if l.strip() and not l.strip().startswith("#")]
            return " ".join(clean_lines)
        except Exception:
            pass
    return ""

def sanitize_text_for_speech(text: str) -> str:
    if not text: return ""
    clean = TAG_REGEX.sub("", text).strip()
    clean = clean.replace("&", " y ").replace("<", " ").replace(">", " ")
    clean = clean.replace('"', " ").replace("'", " ").replace("/", " de ").replace("%", " por ciento ")
    return re.sub(r"\s{2,}", " ", clean).strip()

def parse_srt_time_to_seconds(time_str: str) -> float:
    time_str = time_str.replace(",", ".").strip()
    parts = time_str.split(":")
    hours, minutes, seconds = float(parts[0]), float(parts[1]), float(parts[2])
    return (hours * 3600) + (minutes * 60) + seconds

def parse_srt(srt_content: str) -> List[Dict[str, Any]]:
    pattern = re.compile(
        r"(\d+)\s*\r?\n"
        r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*\r?\n"
        r"([\s\S]*?)(?=(?:\r?\n\s*\d+\s*\r?\n|\Z))"
    )
    blocks = []
    for match in pattern.finditer(srt_content.strip()):
        idx = int(match.group(1))
        start_sec = parse_srt_time_to_seconds(match.group(2))
        end_sec = parse_srt_time_to_seconds(match.group(3))
        raw_text = " ".join([line.strip() for line in match.group(4).splitlines() if line.strip()])

        key_visual = None
        tag_match = TAG_REGEX.search(raw_text)
        if tag_match: key_visual = tag_match.group(1).strip()

        spoken_text = sanitize_text_for_speech(raw_text)
        blocks.append({
            "index": idx, "start": start_sec, "end": end_sec,
            "duration": max(0.1, end_sec - start_sec),
            "text": spoken_text, "key_visual": key_visual
        })
    if not blocks: raise ValueError("No se encontraron subtítulos válidos en el formato SRT.")
    return blocks

async def list_available_voices(engine: str = "qwen") -> List[Dict[str, Any]]:
    eng = str(engine).lower().strip()
    if eng == "qwen":
        return QWEN_SPEAKERS_CATALOG
    voices = await edge_tts.list_voices()
    formatted = []
    for v in voices:
        formatted.append({
            "name": v["Name"], "short_name": v["ShortName"], "gender": v.get("Gender", "Desconocido"),
            "locale": v.get("Locale", "Global"),
            "friendly_name": f"{v.get('Locale', '')} - {v.get('ShortName', '')} ({v.get('Gender', '')})"
        })
    return sorted(formatted, key=lambda x: (x["locale"], x["short_name"]))

def synthesize_with_qwen(
    text: str,
    speaker: str,
    style_prompt: str,
    out_wav: Path,
    gradio_url: str = "FunAudioLLM/CosyVoice",
    hf_token: str = ""
) -> tuple[bool, str]:
    try:
        from gradio_client import Client
        token = hf_token.strip() if hf_token and hf_token.strip() else None
        client = Client(gradio_url, hf_token=token)

        strict_rules = load_strict_rules()
        combined_instruct = f"{style_prompt}. Reglas obligatorias: {strict_rules}" if strict_rules else style_prompt

        res = client.predict(
            tts_text=text,
            mode_checkbox_group="Instrucción de Estilo",
            instruct_text=combined_instruct,
            spk_id=speaker,
            api_name="/generate_audio"
        )
        if res and Path(res).exists():
            shutil.copy2(res, out_wav)
            return True, "OK"
        return False, "Sin audio de salida."
    except Exception as e:
        return False, str(e)

async def preview_voice_sample(
    voice: str,
    engine: str = "qwen",
    style: str = "youtube_energetic",
    custom_prompt: str = "",
    qwen_url: str = "FunAudioLLM/CosyVoice",
    hf_token: str = "",
    speed_rate: str = "+8%"
) -> Path:
    out_file = TTS_CACHE_DIR / "sample_preview.mp3"
    out_wav = TTS_CACHE_DIR / "sample_preview.wav"
    sample_text = "¡Hola! Esta es una prueba de voz con ritmo de alta retención para YouTube."

    eng = engine.lower().strip()
    if eng == "qwen":
        style_prompt = custom_prompt if style == "custom" and custom_prompt else QWEN_STYLES.get(style, QWEN_STYLES["youtube_energetic"])
        success, _ = await asyncio.to_thread(synthesize_with_qwen, sample_text, voice, style_prompt, out_wav, qwen_url, hf_token)
        if success and out_wav.exists():
            return out_wav

    real_edge_voice = QWEN_FALLBACK_MAP.get(voice, "es-ES-AlvaroNeural")
    comm = edge_tts.Communicate(sample_text, real_edge_voice, rate=speed_rate)
    await comm.save(str(out_file))
    return out_file

async def synthesize_single_block(
    sem: asyncio.Semaphore,
    block: dict,
    idx: int,
    engine: str,
    voice: str,
    job_dir: Path,
    style_prompt: str = "",
    qwen_url: str = "FunAudioLLM/CosyVoice",
    hf_token: str = "",
    speed_rate: str = "+8%"
) -> tuple[int, Path, float]:
    sample_rate = 44100
    channels = 1
    text = block["text"]
    norm_wav = job_dir / f"norm_{idx:04d}.wav"

    if norm_wav.exists() and norm_wav.stat().st_size > 1000:
        dur = block["duration"]
        try:
            with wave.open(str(norm_wav), "rb") as w:
                dur = w.getnframes() / float(sample_rate)
        except Exception: pass
        return idx, norm_wav, dur

    if not any(c.isalnum() for c in text):
        dur = min(0.8, block["duration"])
        cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl=mono", "-t", f"{dur:.3f}", "-acodec", "pcm_s16le", str(norm_wav)]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return idx, norm_wav, dur

    async with sem:
        raw_mp3 = job_dir / f"raw_{idx:04d}.mp3"
        success = False
        eng = engine.lower().strip()

        if eng == "qwen":
            success, _ = await asyncio.to_thread(synthesize_with_qwen, text, voice, style_prompt, norm_wav, qwen_url, hf_token)

        if not success:
            real_v = QWEN_FALLBACK_MAP.get(voice, "es-ES-AlvaroNeural")
            for v_name in [real_v] + [v for v in FALLBACK_VOICES if v != real_v]:
                try:
                    comm = edge_tts.Communicate(text, v_name, rate=speed_rate)
                    await comm.save(str(raw_mp3))
                    if raw_mp3.exists() and raw_mp3.stat().st_size > 500:
                        success = True
                        break
                except Exception:
                    await asyncio.sleep(0.15)

            if success and raw_mp3.exists():
                cmd = ["ffmpeg", "-y", "-i", str(raw_mp3), "-ar", str(sample_rate), "-ac", str(channels), "-acodec", "pcm_s16le", str(norm_wav)]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                raw_mp3.unlink(missing_ok=True)

        dur = block["duration"]
        if norm_wav.exists():
            try:
                with wave.open(str(norm_wav), "rb") as w:
                    dur = w.getnframes() / float(sample_rate)
            except Exception: pass
        else:
            cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl=mono", "-t", f"{dur:.3f}", "-acodec", "pcm_s16le", str(norm_wav)]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        return idx, norm_wav, dur

async def synthesize_srt_to_audio(
    srt_blocks: List[Dict[str, Any]], 
    voice: str, 
    job_id: str,
    raw_srt_text: str = "",
    engine: str = "qwen",
    style: str = "youtube_energetic",
    custom_prompt: str = "",
    qwen_url: str = "FunAudioLLM/CosyVoice",
    hf_token: str = "",
    speed_rate: str = "+8%",
    progress_callback = None
) -> Tuple[Path, List[Dict[str, Any]]]:
    """
    Sintetiza la voz y devuelve:
    1. La ruta del audio concatenado final.
    2. synced_blocks: la línea temporal exacta calculada según los milisegundos reales de la voz.
    """
    clean_speech_all = "".join([b["text"] for b in srt_blocks if b["text"]])
    style_prompt = custom_prompt if style == "custom" and custom_prompt else QWEN_STYLES.get(style, QWEN_STYLES["youtube_energetic"])
    
    strict_rules = load_strict_rules()
    cache_key = hashlib.md5(f"{engine}_{voice}_{style}_{clean_speech_all}_{speed_rate}_{strict_rules}".encode("utf-8")).hexdigest()[:14]
    cached_job_dir = TTS_CACHE_DIR / f"cached_{cache_key}"
    final_voice_wav = cached_job_dir / "voice_track.wav"

    cached_job_dir.mkdir(parents=True, exist_ok=True)
    sample_rate = 44100
    sample_width = 2

    sem = asyncio.Semaphore(2)
    tasks = [
        synthesize_single_block(sem, b, i, engine, voice, cached_job_dir, style_prompt, qwen_url, hf_token, speed_rate)
        for i, b in enumerate(srt_blocks)
    ]
    results = await asyncio.gather(*tasks)
    results_map = {res[0]: (res[1], res[2]) for res in results}

    if progress_callback: progress_callback(len(srt_blocks), len(srt_blocks))

    # Construir audio final y línea de tiempo real 100% sincronizada
    synced_blocks = []
    current_timeline = 0.0

    with wave.open(str(final_voice_wav), "wb") as out_wav:
        out_wav.setnchannels(1)
        out_wav.setsampwidth(sample_width)
        out_wav.setframerate(sample_rate)

        for i, block in enumerate(srt_blocks):
            norm_wav, actual_dur = results_map[i]
            block_start = current_timeline
            block_end = current_timeline + actual_dur

            synced_blocks.append({
                "index": block["index"],
                "start": block_start,
                "end": block_end,
                "duration": actual_dur,
                "text": block["text"],
                "key_visual": block.get("key_visual")
            })

            if norm_wav.exists():
                try:
                    with wave.open(str(norm_wav), "rb") as in_wav:
                        out_wav.writeframes(in_wav.readframes(in_wav.getnframes()))
                except Exception: pass

            current_timeline += actual_dur

            # Pequeña pausa natural casi imperceptible (0.06s) entre frases para dinamismo
            pause_sec = 0.06
            out_wav.writeframes(b"\x00" * int(pause_sec * sample_rate * sample_width))
            current_timeline += pause_sec

    return final_voice_wav, synced_blocks