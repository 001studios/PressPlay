"""
renderer.py - Ensamblador de video con sincronización estricta al audio real:
- Subtítulos estilo TikTok/Reels: ráfagas cortas de 2 a 3 palabras, tamaño grande (48px) y pop-up zoom (+18%).
- Los cambios de clip y subtítulos se basan en los milisegundos reales del audio sintetizado.
- Renderizado acelerado por GPU NVENC.
"""
from pathlib import Path
import os
import random
import re
import datetime
import subprocess
import asyncio
import traceback
from typing import Dict, Any, Optional, List

from app.tts import parse_srt, synthesize_srt_to_audio
from app.clips import (
    get_cached_or_new_clips,
    build_anti_repetition_clip_sequence,
    prepare_key_visual_clip,
    purge_generation_clips,
    get_media_duration,
    CLIPS_CACHE_DIR
)
from app.db import (
    record_interaction, mark_article_as_used, 
    upsert_user_interest, get_article_by_id
)
from app.recommender import extract_keywords_from_article

current_render_job: Dict[str, Any] = {
    "status": "idle",
    "stage": "",
    "progress": 0,
    "detail": "Esperando orden de inicio",
    "video_path": None,
    "error_message": None
}

def update_job_progress(stage: str, progress: int, detail: str):
    current_render_job["status"] = "processing"
    current_render_job["stage"] = stage
    current_render_job["progress"] = min(100, max(0, progress))
    current_render_job["detail"] = detail

def get_random_background_music(music_dir: Path) -> Optional[Path]:
    if not music_dir.exists(): return None
    valid_exts = [".mp3", ".wav", ".m4a", ".ogg", ".aac", ".flac"]
    tracks = [f for f in music_dir.glob("*") if f.suffix.lower() in valid_exts]
    return random.choice(tracks) if tracks else None

def sanitize_filename(name: str) -> str:
    clean = re.sub(r'[\\/*?:"<>|]', "", name).strip().replace(" ", "_")
    return clean[:60] if clean else "video_generado"

def hex_to_ass_color(hex_color: str) -> str:
    clean = hex_color.lstrip("#").strip()
    if len(clean) == 6:
        r, g, b = clean[0:2], clean[2:4], clean[4:6]
        return f"&H00{b}{g}{r}".upper()
    return "&H0000E6FF"

def format_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs >= 100: cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def generate_tiktok_ass_subtitles(synced_blocks: List[Dict[str, Any]], output_ass_path: Path, highlight_hex: str = "#FFE600", quality: str = "720p") -> Path:
    """
    Genera subtítulos estilo TikTok/Reels auténticos:
    - Ráfagas cortas de 2 a 3 palabras en pantalla.
    - Fuente grande de impacto (48px en 720p / 56px en 1080p).
    - Pop-up zoom (+18%) y cambio al color de resaltado en la palabra que se está pronunciando.
    """
    ass_highlight_color = hex_to_ass_color(highlight_hex)
    font_size = 48 if quality in ["720p", "1080p"] else 32
    margin_v = 85 if quality in ["720p", "1080p"] else 55

    ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: TikTokMain,Arial Black,{font_size},&H00FFFFFF,{ass_highlight_color},&H00000000,&H80000000,-1,0,0,0,100,100,1,0,1,4.0,2.0,2,30,30,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []

    for block in synced_blocks:
        raw_text = block.get("text", "").strip().upper()
        if not raw_text: continue

        words = raw_text.split()
        if not words: continue

        block_start = block["start"]
        block_end = block["end"]
        block_duration = max(0.2, block_end - block_start)

        # Dividir la frase en chunks rítmicos de 2 a 3 palabras
        chunk_size = 2 if len(words) <= 4 else 3
        chunks = [words[i:i + chunk_size] for i in range(0, len(words), chunk_size)]
        chunk_duration = block_duration / len(chunks)

        for c_idx, chunk in enumerate(chunks):
            c_start = block_start + (c_idx * chunk_duration)
            c_end = block_start + ((c_idx + 1) * chunk_duration)
            if c_idx == len(chunks) - 1:
                c_end = block_end

            word_sub_dur = (c_end - c_start) / len(chunk)

            # Micro-eventos sincronizados dentro del chunk de 2-3 palabras
            for w_idx, active_word in enumerate(chunk):
                sub_start = c_start + (w_idx * word_sub_dur)
                sub_end = c_start + ((w_idx + 1) * word_sub_dur)
                if w_idx == len(chunk) - 1:
                    sub_end = c_end

                ass_start_str = format_ass_time(sub_start)
                ass_end_str = format_ass_time(sub_end)

                # Construir la línea: la palabra hablada se agranda y cambia al color seleccionado
                line_words = []
                for j, w in enumerate(chunk):
                    if j == w_idx:
                        line_words.append(f"{{\\c{ass_highlight_color}\\fscx118\\fscy118}}{w}{{\\rTikTokMain}}")
                    else:
                        line_words.append(f"{{\\c&H00FFFFFF&\\fscx100\\fscy100}}{w}")

                dialogue_text = " ".join(line_words)
                events.append(f"Dialogue: 0,{ass_start_str},{ass_end_str},TikTokMain,,0,0,0,,{dialogue_text}")

    full_ass_content = ass_header + "\n".join(events) + "\n"
    output_ass_path.write_text(full_ass_content, encoding="utf-8")
    return output_ass_path

def get_nvidia_or_fast_encoder() -> list[str]:
    try:
        test_cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=1280x720:d=0.04", "-c:v", "h264_nvenc", "-f", "null", "-"]
        if subprocess.run(test_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23", "-pix_fmt", "yuv420p"]
    except Exception: pass
    return ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-threads", "0", "-pix_fmt", "yuv420p"]

def run_ffmpeg_sync(cmd: list, cwd_dir: Path) -> tuple[int, str]:
    res = subprocess.run(cmd, cwd=str(cwd_dir.resolve()), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return res.returncode, res.stderr

async def execute_video_generation_pipeline(
    srt_text: str,
    keywords: list[str],
    voice: str,
    cfg: dict,
    article_id: Optional[int] = None
):
    global current_render_job
    try:
        purge_generation_clips()

        out_raw = cfg.get("output_dir", "output")
        output_dir = Path(out_raw).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        music_dir = Path(cfg.get("music_dir", "music")).resolve()
        quality = cfg.get("video_quality", "720p")
        target_clips = int(cfg.get("loop_clips_count", 15))
        tts_engine = cfg.get("tts_engine", "qwen")
        video_mode = cfg.get("video_mode", "loop")
        enable_subtitles = cfg.get("enable_tiktok_subtitles", True)
        subtitle_color = cfg.get("subtitle_highlight_color", "#FFE600")
        
        qwen_url = cfg.get("qwen_gradio_url", "FunAudioLLM/CosyVoice")
        qwen_style = cfg.get("qwen_style", "youtube_energetic")
        qwen_custom = cfg.get("qwen_custom_prompt", "")
        hf_token = cfg.get("hf_token", "")
        speed_rate = cfg.get("tts_speed", "+8%")
        
        job_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

        # 1. Parsear guion SRT
        update_job_progress("tts", 5, "Parseando guion base...")
        raw_srt_blocks = parse_srt(srt_text)

        # 2. Sintetizar locución y obtener la línea temporal REAL del audio
        def tts_prog(c, t):
            update_job_progress("tts", 5 + int((c / t) * 35), f"Sintetizando locución con {tts_engine.upper()} ({speed_rate})...")

        voice_audio_path, synced_blocks = await synthesize_srt_to_audio(
            srt_blocks=raw_srt_blocks, voice=voice, job_id=job_id,
            raw_srt_text=srt_text, engine=tts_engine,
            style=qwen_style, custom_prompt=qwen_custom,
            qwen_url=qwen_url, hf_token=hf_token, speed_rate=speed_rate,
            progress_callback=tts_prog
        )
        total_audio_duration = get_media_duration(voice_audio_path)

        loop = asyncio.get_running_loop()
        final_sequence = []

        # 3. Adquisición de Clips: Sincronizada con la duración REAL de cada frase
        if video_mode == "granular_srt":
            update_job_progress("clips", 45, "Modo Granular: Adquiriendo clip para cada frase hablada...")
            clip_usage_tracker = {}
            for idx, block in enumerate(synced_blocks):
                tag = block.get("key_visual")
                tag_query = tag if tag else (keywords[idx % len(keywords)] if keywords else "actualidad")
                current_usage = clip_usage_tracker.get(tag_query, 0)
                variant = current_usage + 1
                
                # Cada clip dura exactamente lo que duró la frase en el audio
                clip = await loop.run_in_executor(None, prepare_key_visual_clip, tag_query, block["duration"], cfg, quality, variant, idx)
                if clip and clip.exists():
                    final_sequence.append(clip)
                    clip_usage_tracker[tag_query] = current_usage + 1
                else:
                    b_clips = await loop.run_in_executor(None, get_cached_or_new_clips, keywords, cfg, quality, 3)
                    final_sequence.append(random.choice(b_clips))
        else:
            update_job_progress("clips", 45, "Adquiriendo videos de B-Roll en alta definición...")
            background_clips = await loop.run_in_executor(None, get_cached_or_new_clips, keywords, cfg, quality, target_clips)
            update_job_progress("clips", 70, "Ensamblando B-Roll y momentos clave...")
            base_sequence = build_anti_repetition_clip_sequence(background_clips, total_audio_duration + 30.0, max_clip_usage=2)

            key_moments = []
            for idx, block in enumerate(synced_blocks):
                if block.get("key_visual"):
                    kclip = await loop.run_in_executor(None, prepare_key_visual_clip, block["key_visual"], block["duration"], cfg, quality, 1, idx)
                    if kclip and kclip.exists(): key_moments.append(kclip)

            base_idx = 0
            for km in key_moments:
                if base_idx < len(base_sequence):
                    final_sequence.append(base_sequence[base_idx])
                    base_idx += 1
                final_sequence.append(km)
            final_sequence.extend(base_sequence[base_idx:])

        concat_txt = CLIPS_CACHE_DIR / f"concat_{job_id}.txt"
        with open(concat_txt, "w", encoding="utf-8") as f:
            for clip in final_sequence:
                f.write(f"file '{clip.name}'\n")

        # 4. Generación de Subtítulos Dinámicos TikTok SINCRONIZADOS AL AUDIO REAL
        subtitle_filter = ""
        ass_file_path = None
        if enable_subtitles:
            update_job_progress("render", 80, "Generando subtítulos dinámicos estilo TikTok sincronizados...")
            ass_file_path = CLIPS_CACHE_DIR / f"subtitles_{job_id}.ass"
            generate_tiktok_ass_subtitles(synced_blocks, ass_file_path, highlight_hex=subtitle_color, quality=quality)
            escaped_ass = str(ass_file_path.resolve()).replace("\\", "/").replace(":", "\\:")
            subtitle_filter = f",subtitles='{escaped_ass}'"

        # 5. Nombre y Renderizado
        article_title = "video"
        if article_id:
            art = get_article_by_id(article_id)
            if art and art.get("titulo"): article_title = art["titulo"]
        clean_title = sanitize_filename(article_title)
        output_filename = f"{clean_title}_{job_id}.mp4"
        final_video_path = output_dir / output_filename

        encoder_flags = get_nvidia_or_fast_encoder()
        update_job_progress("render", 85, f"Renderizando a {quality} con GPU...")

        bg_music = get_random_background_music(music_dir)
        perfect_sync_filter = f"fps=30,format=yuv420p{subtitle_filter}"

        if bg_music and bg_music.exists():
            update_job_progress("render", 88, f"Mezclando música de fondo al 10%...")
            cmd = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_txt.name,
                "-i", str(voice_audio_path.resolve()), "-stream_loop", "-1", "-i", str(bg_music.resolve()),
                "-filter_complex",
                f"[0:v]{perfect_sync_filter}[vout];[1:a]volume=1.0[v_speech];[2:a]volume=0.10[v_bg];[v_speech][v_bg]amix=inputs=2:duration=first:dropout_transition=2[aout]",
                "-map", "[vout]", "-map", "[aout]", *encoder_flags, "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
                "-t", f"{total_audio_duration:.3f}", str(final_video_path)
            ]
        else:
            update_job_progress("render", 88, "Renderizando video con subtítulos dinámicos...")
            cmd = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_txt.name,
                "-i", str(voice_audio_path.resolve()),
                "-filter_complex", f"[0:v]{perfect_sync_filter}[vout]",
                "-map", "[vout]", "-map", "1:a", *encoder_flags, "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
                "-t", f"{total_audio_duration:.3f}", str(final_video_path)
            ]

        ret_code, err_log = await loop.run_in_executor(None, run_ffmpeg_sync, cmd, CLIPS_CACHE_DIR)
        concat_txt.unlink(missing_ok=True)
        if ass_file_path and ass_file_path.exists():
            ass_file_path.unlink(missing_ok=True)

        if ret_code != 0: raise RuntimeError(f"FFmpeg falló: {err_log[-350:]}")

        if article_id:
            mark_article_as_used(article_id)
            record_interaction(article_id, "generada")
            art = get_article_by_id(article_id)
            if art:
                extracted_kw = extract_keywords_from_article(art.get("titulo", ""), art.get("texto_extraido") or art.get("resumen", ""))
                for term, weight in extracted_kw.items():
                    upsert_user_interest(term, weight_increment=round(weight * 6.0, 2))

        update_job_progress("done", 100, "¡Video finalizado exitosamente!")
        current_render_job["status"] = "finished"
        current_render_job["video_path"] = f"/api/video/{output_filename}"

    except Exception as e:
        traceback.print_exc()
        err_msg = str(e) or type(e).__name__
        current_render_job["status"] = "error"
        current_render_job["error_message"] = err_msg
        current_render_job["detail"] = f"Fallo: {err_msg}"