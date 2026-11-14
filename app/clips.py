"""
clips.py - Motor de adquisición con evasión de bloqueos en YouTube y rotación de plataformas:
- Usa el cliente Android/TV de yt-dlp (evade el 99% de bloqueos HTTP 429 y bot checks).
- Rotación automática de proveedores en Modo Granular para no saturar YouTube.
- Jitter aleatorio entre descargas y purga previa de clips en cada sesión.
"""
import sys
import os
import shutil
import re
import time
import random
import hashlib
import collections
import subprocess
import concurrent.futures
import urllib.parse
from pathlib import Path
from typing import List, Dict, Any, Optional
import requests

CLIPS_CACHE_DIR = Path("cache/clips")
CLIPS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
CUSTOM_CLIPS_DIR = Path("clips_custom")
CUSTOM_CLIPS_DIR.mkdir(parents=True, exist_ok=True)

RESOLUTIONS = {
    "480p": (854, 480),
    "720p": (1280, 720),
    "1080p": (1920, 1080)
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
}

# Estado de cooldown por plataforma para no insistir si una fue bloqueada temporalmente
PLATFORM_COOLDOWNS = {
    "youtube": 0.0,
    "pixabay": 0.0,
    "pexels": 0.0,
    "wikimedia": 0.0
}

def purge_generation_clips():
    """Limpia la caché de clips de sesiones previas para no mezclar material."""
    for f in CLIPS_CACHE_DIR.glob("kw_*.mp4"):
        try: f.unlink(missing_ok=True)
        except Exception: pass
    for f in CLIPS_CACHE_DIR.glob("candidate_*.mp4"):
        try: f.unlink(missing_ok=True)
        except Exception: pass

def get_media_duration(file_path: Path) -> float:
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(file_path)]
        return max(1.0, float(subprocess.check_output(cmd, text=True).strip()))
    except Exception:
        return 5.0

def is_clip_black_or_corrupt(file_path: Path) -> bool:
    if not file_path.exists() or file_path.stat().st_size < 35000:
        return True
    try:
        duration = get_media_duration(file_path)
        if duration < 0.8: return True
        sample_timestamps = [duration * 0.20, duration * 0.40, duration * 0.60, duration * 0.80]
        visible_frames = 0
        for ts in sample_timestamps:
            cmd = [
                "ffmpeg", "-v", "error", "-ss", f"{ts:.2f}",
                "-i", str(file_path), "-vframes", "1",
                "-vf", "scale=1:1,format=gray", "-f", "rawvideo", "-"
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5)
            if proc.returncode == 0 and len(proc.stdout) >= 1:
                if proc.stdout[0] > 26: visible_frames += 1
        return visible_frames == 0
    except Exception:
        return True

def get_nvenc_flags() -> list[str]:
    try:
        t = subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=1280x720:d=0.04", "-c:v", "h264_nvenc", "-f", "null", "-"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        if t.returncode == 0:
            return ["-c:v", "h264_nvenc", "-preset", "p1", "-cq", "24"]
    except Exception: pass
    return ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "24", "-threads", "2"]

def normalize_video(input_file: Path, output_file: Path, quality: str = "720p") -> bool:
    w, h = RESOLUTIONS.get(quality, (1280, 720))
    enc = get_nvenc_flags()
    cmd = [
        "ffmpeg", "-y", "-i", str(input_file),
        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=0x09090b,setsar=1,fps=30,format=yuv420p",
        *enc, "-pix_fmt", "yuv420p", "-an", str(output_file)
    ]
    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return res.returncode == 0 and output_file.exists() and output_file.stat().st_size > 40000

def convert_image_to_clip(image_file: Path, output_file: Path, duration: float, quality: str = "720p") -> bool:
    w, h = RESOLUTIONS.get(quality, (1280, 720))
    enc = get_nvenc_flags()
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", str(image_file),
        "-t", f"{duration:.3f}",
        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=0x09090b,setsar=1,fps=30,format=yuv420p",
        *enc, "-pix_fmt", "yuv420p", "-an", str(output_file)
    ]
    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return res.returncode == 0 and output_file.exists() and output_file.stat().st_size > 20000

# --- FOTOS OFICIALES HD ---

def fetch_wikipedia_real_image(query: str, dest_path: Path) -> bool:
    try:
        clean_q = re.sub(r"^(imagen|video|image):\s*", "", query, flags=re.I).strip()
        encoded = urllib.parse.quote_plus(clean_q)
        url = f"https://en.wikipedia.org/w/api.php?action=query&format=json&generator=search&gsrsearch={encoded}&gsrlimit=2&prop=pageimages&pithumbsize=1280"
        res = requests.get(url, headers=HEADERS, timeout=5)
        if res.status_code == 200:
            pages = res.json().get("query", {}).get("pages", {})
            for p in pages.values():
                thumb = p.get("thumbnail", {}).get("source")
                if thumb:
                    dl = requests.get(thumb, headers=HEADERS, timeout=8)
                    if dl.status_code == 200 and len(dl.content) > 10000:
                        dest_path.write_bytes(dl.content)
                        return True
    except Exception: pass
    return False

def fetch_bing_real_image(query: str, dest_path: Path) -> bool:
    try:
        clean_q = re.sub(r"^(imagen|video|image):\s*", "", query, flags=re.I).strip()
        encoded = urllib.parse.quote_plus(clean_q)
        url = f"https://images.search.yahoo.com/search/images?p={encoded}"
        res = requests.get(url, headers=HEADERS, timeout=6)
        if res.status_code == 200:
            matches = re.findall(r'imgurl=(https?%3A%2F%2F[^&]+)', res.text)
            for m in matches[:3]:
                raw_url = urllib.parse.unquote(m)
                dl = requests.get(raw_url, headers=HEADERS, timeout=8)
                if dl.status_code == 200 and len(dl.content) > 15000:
                    dest_path.write_bytes(dl.content)
                    return True
    except Exception: pass
    return False

def get_high_precision_photo(query: str, dest_path: Path) -> bool:
    return fetch_wikipedia_real_image(query, dest_path) or fetch_bing_real_image(query, dest_path)

def collect_local_custom_clips(cfg: dict, quality: str = "720p") -> List[Path]:
    valid_exts = {".mp4", ".mkv", ".mov", ".avi", ".webm"}
    directories = [CUSTOM_CLIPS_DIR]
    for source in cfg.get("custom_clip_sources", []):
        if isinstance(source, dict) and source.get("path"):
            p = Path(source["path"])
            if p.exists() and p.is_dir(): directories.append(p)

    out = []
    for d in directories:
        for file in d.glob("*"):
            if file.suffix.lower() in valid_exts and file.is_file() and file.stat().st_size > 150000:
                h = hashlib.md5(str(file.resolve()).encode("utf-8")).hexdigest()[:10]
                norm_dest = CLIPS_CACHE_DIR / f"local_{h}_{quality}.mp4"
                if not norm_dest.exists() or norm_dest.stat().st_size < 40000:
                    if normalize_video(file, norm_dest, quality) and not is_clip_black_or_corrupt(norm_dest):
                        out.append(norm_dest)
                else:
                    if not is_clip_black_or_corrupt(norm_dest): out.append(norm_dest)
    return out

# --- DESCARGA DE YOUTUBE CON EVASIÓN DE RATE LIMIT (CLIENTE ANDROID) ---

def build_precision_youtube_query(keyword: str, variant: int) -> str:
    clean = re.sub(r'[^\w\s-]', '', keyword).strip()
    lower = clean.lower()

    if any(w in lower for w in ["gameplay", "game", "combate", "partida"]):
        visual_tag = "gameplay 4k 60fps" if variant % 2 == 1 else "walkthrough gameplay"
    elif any(w in lower for w in ["nvidia", "gpu", "rtx", "hardware", "cpu", "chip", "intel", "amd"]):
        visual_tag = "showcase b-roll footage" if variant % 2 == 1 else "trailer hardware review"
    elif any(w in lower for w in ["trailer", "teaser"]):
        visual_tag = "official trailer"
    else:
        visual_tag = "footage b-roll" if variant % 2 == 1 else "news report clip"

    anti_garbage = "-podcast -reaction -reaccionando -stream -webcam -vlog"
    return f"{clean} {visual_tag} {anti_garbage}".strip()

def try_download_from_youtube(keyword: str, output_path: Path, quality: str = "720p", search_index: int = 1) -> bool:
    # Verificar si YouTube está en cooldown temporal por 429
    if time.time() < PLATFORM_COOLDOWNS["youtube"]:
        return False

    try:
        search_query = build_precision_youtube_query(keyword, search_index)
        offsets = ["*00:00:25-00:00:40", "*00:00:50-00:01:05", "*00:01:20-00:01:35"]
        time_section = offsets[(search_index - 1) % len(offsets)]
        
        temp_slug = hashlib.md5(f"yt_{search_query}_{search_index}_{os.getpid()}".encode("utf-8")).hexdigest()[:10]
        temp_template = str((CLIPS_CACHE_DIR / f"ytdl_raw_{temp_slug}.%(ext)s").resolve())

        # Jitter aleatorio para no parecer un scraper robótico
        time.sleep(random.uniform(0.6, 1.4))

        cmd = [
            sys.executable, "-m", "yt_dlp", f"ytsearch{search_index}:{search_query}",
            "--playlist-items", str(search_index),
            "-f", "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
            "--download-sections", time_section,
            "--extractor-args", "youtube:player_client=android,web",  # Evita bloqueos web usando cliente Android
            "--match-filter", "duration > 15 & duration < 900",
            "-o", temp_template,
            "--no-playlist", "--socket-timeout", "15", "--retries", "1",
            "--quiet", "--no-warnings"
        ]
        
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=40)
        
        # Detección de bloqueo HTTP 429 / Sign in
        err_out = proc.stderr.decode("utf-8", errors="ignore").lower()
        if "429" in err_out or "too many requests" in err_out or "sign in to confirm" in err_out:
            print("[AVISO YouTube]: Rate limit detectado. Entrando en cooldown de 90 segundos y conmutando a otras fuentes...")
            PLATFORM_COOLDOWNS["youtube"] = time.time() + 90.0
            return False

        found = list(CLIPS_CACHE_DIR.glob(f"ytdl_raw_{temp_slug}.*"))
        if found and found[0].stat().st_size > 50000:
            if normalize_video(found[0], output_path, quality) and not is_clip_black_or_corrupt(output_path):
                found[0].unlink(missing_ok=True)
                return True
            found[0].unlink(missing_ok=True)
        return False
    except Exception:
        return False

def try_download_from_pixabay(keyword: str, api_key: str, output_path: Path, quality: str = "720p", search_index: int = 1) -> bool:
    if not api_key: return False
    try:
        clean = re.sub(r'[^\w\s]', '', keyword).strip()
        url = f"https://pixabay.com/api/videos/?key={api_key}&q={urllib.parse.quote(clean)}&per_page=5"
        res = requests.get(url, headers=HEADERS, timeout=8)
        if res.status_code == 200:
            hits = res.json().get("hits", [])
            idx = (search_index - 1) % len(hits) if hits else 0
            if hits and idx < len(hits):
                vid_data = hits[idx].get("videos", {}).get("medium") or hits[idx].get("videos", {}).get("small")
                if vid_data and vid_data.get("url"):
                    temp_vid = CLIPS_CACHE_DIR / f"temp_pxb_{os.getpid()}_{search_index}.mp4"
                    r = requests.get(vid_data["url"], headers=HEADERS, timeout=15)
                    if r.status_code == 200 and len(r.content) > 60000:
                        temp_vid.write_bytes(r.content)
                        success = normalize_video(temp_vid, output_path, quality)
                        temp_vid.unlink(missing_ok=True)
                        if success and not is_clip_black_or_corrupt(output_path): return True
        return False
    except Exception: return False

def try_download_from_pexels(keyword: str, api_key: str, output_path: Path, quality: str = "720p", search_index: int = 1) -> bool:
    if not api_key: return False
    try:
        clean = re.sub(r'[^\w\s]', '', keyword).strip()
        url = f"https://api.pexels.com/videos/search?query={urllib.parse.quote(clean)}&per_page=5&orientation=landscape"
        res = requests.get(url, headers={**HEADERS, "Authorization": api_key}, timeout=8)
        if res.status_code == 200:
            videos = res.json().get("videos", [])
            idx = (search_index - 1) % len(videos) if videos else 0
            if videos and idx < len(videos):
                files = videos[idx].get("video_files", [])
                if files:
                    target = next((f for f in files if f.get("quality") == "sd" or f.get("height", 0) <= 720), files[0])
                    temp_vid = CLIPS_CACHE_DIR / f"temp_pxl_{os.getpid()}_{search_index}.mp4"
                    r = requests.get(target["link"], headers=HEADERS, timeout=15)
                    if r.status_code == 200 and len(r.content) > 60000:
                        temp_vid.write_bytes(r.content)
                        success = normalize_video(temp_vid, output_path, quality)
                        temp_vid.unlink(missing_ok=True)
                        if success and not is_clip_black_or_corrupt(output_path): return True
        return False
    except Exception: return False

def try_download_from_wikimedia(keyword: str, output_path: Path, quality: str = "720p", search_index: int = 1) -> bool:
    try:
        clean = re.sub(r'[^\w\s]', '', keyword).strip()
        search_url = f"https://commons.wikimedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(clean)}+filetype:video&srnamespace=6&format=json&srlimit=5"
        res = requests.get(search_url, headers=HEADERS, timeout=6)
        if res.status_code == 200:
            results = res.json().get("query", {}).get("search", [])
            idx = (search_index - 1) % len(results) if results else 0
            if results and idx < len(results):
                title = results[idx].get("title", "")
                i_res = requests.get(f"https://commons.wikimedia.org/w/api.php?action=query&titles={urllib.parse.quote(title)}&prop=imageinfo&iiprop=url&format=json", headers=HEADERS, timeout=6)
                if i_res.status_code == 200:
                    pages = i_res.json().get("query", {}).get("pages", {})
                    for p in pages.values():
                        info = p.get("imageinfo", [])
                        if info and info[0].get("url") and info[0]["url"].lower().endswith((".webm", ".mp4", ".ogv")):
                            temp_vid = CLIPS_CACHE_DIR / f"temp_wm_{os.getpid()}_{search_index}.webm"
                            r = requests.get(info[0]["url"], headers=HEADERS, timeout=20)
                            if r.status_code == 200 and len(r.content) > 100000:
                                temp_vid.write_bytes(r.content)
                                success = normalize_video(temp_vid, output_path, quality)
                                temp_vid.unlink(missing_ok=True)
                                if success and not is_clip_black_or_corrupt(output_path): return True
        return False
    except Exception: return False

def acquire_validated_clip_multi_platform(keyword: str, cfg: dict, final_output: Path, quality: str = "720p", variant: int = 1, rotation_seed: int = 0) -> bool:
    clean_keyword = keyword.strip()
    
    # Construcción de plataformas disponibles
    available = []
    if cfg.get("enable_pexels", False) and cfg.get("pexels_api_key", "").strip():
        available.append("pexels")
    if cfg.get("enable_youtube", True) and time.time() >= PLATFORM_COOLDOWNS["youtube"]:
        available.append("youtube")
    if cfg.get("enable_pixabay", False) and cfg.get("pixabay_api_key", "").strip():
        available.append("pixabay")
    if cfg.get("enable_wikimedia", True):
        available.append("wikimedia")
    if not available:
        available = ["youtube"]

    # Rotar el orden de búsqueda según el índice de la frase para no bombardear la misma plataforma
    shift = rotation_seed % len(available)
    platforms = available[shift:] + available[:shift]

    temp = CLIPS_CACHE_DIR / f"candidate_{hashlib.md5(f'{clean_keyword}_{variant}_{os.getpid()}'.encode()).hexdigest()[:10]}.mp4"

    for platform in platforms:
        def fetch(p, idx):
            if p == "youtube": return try_download_from_youtube(clean_keyword, temp, quality, idx)
            elif p == "pixabay": return try_download_from_pixabay(clean_keyword, cfg.get("pixabay_api_key", ""), temp, quality, idx)
            elif p == "pexels": return try_download_from_pexels(clean_keyword, cfg.get("pexels_api_key", ""), temp, quality, idx)
            elif p == "wikimedia": return try_download_from_wikimedia(clean_keyword, temp, quality, idx)
            return False

        if fetch(platform, variant) and not is_clip_black_or_corrupt(temp):
            shutil.move(str(temp), str(final_output))
            return True
        temp.unlink(missing_ok=True)

    return False

# --- MOMENTOS CLAVE Y B-ROLL ---

def prepare_key_visual_clip(tag_content: str, duration: float, cfg: dict, quality: str = "720p", variant: int = 1, rotation_seed: int = 0) -> Optional[Path]:
    raw_tag = tag_content.strip()
    is_video_tag = "video:" in raw_tag.lower()
    clean_query = re.sub(r"^(imagen|video|image):\s*", "", raw_tag, flags=re.I).strip()
    slug = "".join(c for c in clean_query.lower() if c.isalnum() or c in " _-").replace(" ", "_")
    key_clip_path = CLIPS_CACHE_DIR / f"key_{slug}_v{variant}_{quality}.mp4"

    if key_clip_path.exists() and key_clip_path.stat().st_size > 30000 and not is_clip_black_or_corrupt(key_clip_path):
        return key_clip_path

    if is_video_tag:
        if acquire_validated_clip_multi_platform(clean_query, cfg, key_clip_path, quality, variant=variant, rotation_seed=rotation_seed):
            return key_clip_path

    temp_img = CLIPS_CACHE_DIR / f"img_key_{slug}_{variant}.jpg"
    if get_high_precision_photo(clean_query, temp_img):
        convert_image_to_clip(temp_img, key_clip_path, duration, quality)
        temp_img.unlink(missing_ok=True)
        if key_clip_path.exists() and not is_clip_black_or_corrupt(key_clip_path):
            return key_clip_path

    return None

def get_or_download_video_clip(keyword: str, cfg: dict, quality: str = "720p", variant: int = 1, rotation_seed: int = 0) -> Optional[Path]:
    clean_keyword = keyword.strip()
    if not clean_keyword: return None
    slug = "".join(c for c in clean_keyword.lower() if c.isalnum() or c in " _-").replace(" ", "_")
    final_clip = CLIPS_CACHE_DIR / f"kw_{slug}_v{variant}_{quality}.mp4"

    if final_clip.exists() and final_clip.stat().st_size > 50000 and not is_clip_black_or_corrupt(final_clip):
        return final_clip

    if acquire_validated_clip_multi_platform(clean_keyword, cfg, final_clip, quality, variant=variant, rotation_seed=rotation_seed):
        return final_clip
    return None

def get_cached_or_new_clips(keywords: List[str], cfg: dict, quality: str = "720p", target_clip_count: int = 15) -> List[Path]:
    matched = collect_local_custom_clips(cfg, quality)
    clean_kws = [k.strip() for k in keywords if k.strip()] or ["actualidad tecnologia", "gaming noticias"]

    needed = max(0, target_clip_count - len(matched))
    if needed > 0:
        fetch_plan = []
        kw_idx, var = 0, 1
        while len(fetch_plan) < needed:
            fetch_plan.append((clean_kws[kw_idx % len(clean_kws)], var, kw_idx))
            kw_idx += 1
            if kw_idx % len(clean_kws) == 0: var += 1

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
            futs = [ex.submit(get_or_download_video_clip, kw, cfg, quality, v, r_seed) for kw, v, r_seed in fetch_plan]
            for f in concurrent.futures.as_completed(futs):
                res = f.result()
                if res and res.exists() and not is_clip_black_or_corrupt(res):
                    matched.append(res)

    return [c for c in set(matched) if c.exists() and not is_clip_black_or_corrupt(c)]

def build_anti_repetition_clip_sequence(available_clips: List[Path], target_duration: float, max_clip_usage: int = 2) -> List[Path]:
    valid_clips = [c for c in available_clips if c.exists() and c.stat().st_size > 40000 and not is_clip_black_or_corrupt(c)]
    if not valid_clips:
        raise ValueError("No se encontraron clips de video válidos.")

    sequence = []
    current_time = 0.0
    usage_counter = collections.Counter()
    last_clip = None

    while current_time < target_duration:
        candidates = [c for c in valid_clips if usage_counter[c] < max_clip_usage and c != last_clip]
        if not candidates:
            candidates = [c for c in valid_clips if c != last_clip] or valid_clips

        chosen = random.choice(candidates)
        usage_counter[chosen] += 1
        last_clip = chosen
        sequence.append(chosen)
        current_time += get_media_duration(chosen)

    return sequence