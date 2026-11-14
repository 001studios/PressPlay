"""
feed.py - Detección RSS, sincronización concurrente y caché de imágenes en disco.
"""
from urllib.parse import urljoin, urlparse
from pathlib import Path
from datetime import datetime
import hashlib
import time
import concurrent.futures
import requests
from bs4 import BeautifulSoup
import feedparser

CACHE_IMG_DIR = Path("cache/images")
CACHE_IMG_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

COMMON_PATHS = [
    "/feed",
    "/rss",
    "/rss.xml",
    "/feed.xml",
    "/arc/outboundfeeds/rss/",
    "/feeds/posts/default",
    "/atom.xml"
]

def verify_feed_url(url: str) -> dict | None:
    try:
        parsed = feedparser.parse(url)
        if len(parsed.entries) > 0 or (hasattr(parsed, "feed") and hasattr(parsed.feed, "title")):
            title = parsed.feed.get("title", urlparse(url).netloc)
            return {"feed_url": url, "title": title}
    except Exception:
        pass
    return None

def detect_rss(input_url: str) -> dict:
    clean_url = input_url.strip()
    if not clean_url.startswith(("http://", "https://")):
        clean_url = "https://" + clean_url

    direct_check = verify_feed_url(clean_url)
    if direct_check:
        return {
            "url": direct_check["feed_url"],
            "nombre": direct_check["title"] or urlparse(clean_url).netloc,
            "tipo": "rss"
        }

    try:
        response = requests.get(clean_url, headers=HEADERS, timeout=8)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        link_tags = soup.find_all(
            "link",
            rel=lambda r: r and "alternate" in r.lower(),
            type=lambda t: t and any(x in t.lower() for x in ["rss", "atom", "xml"])
        )
        for tag in link_tags:
            href = tag.get("href")
            if href:
                candidate_url = urljoin(clean_url, href)
                check = verify_feed_url(candidate_url)
                if check:
                    tag_title = tag.get("title") or check["title"] or urlparse(clean_url).netloc
                    return {"url": check["feed_url"], "nombre": tag_title, "tipo": "rss"}
    except Exception:
        pass

    base_domain = f"{urlparse(clean_url).scheme}://{urlparse(clean_url).netloc}"
    for path in COMMON_PATHS:
        candidate_url = urljoin(base_domain, path)
        check = verify_feed_url(candidate_url)
        if check:
            return {"url": check["feed_url"], "nombre": check["title"] or urlparse(clean_url).netloc, "tipo": "rss"}

    raise ValueError(f"No se pudo encontrar ningún feed RSS válido para: {input_url}")

# --- EXTRACCIÓN DE IMÁGENES Y METADATOS DE ARTÍCULOS ---

def extract_image_from_entry(entry: dict) -> str | None:
    """Extrae la URL de imagen más relevante de una entrada RSS."""
    # 1. Enclosures (archivos multimedia adjuntos)
    if "enclosures" in entry and entry.enclosures:
        for enc in entry.enclosures:
            if "image" in enc.get("type", "") or enc.get("href", "").lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                return enc.get("href")

    # 2. Media Content o Thumbnail de Yahoo Media RSS
    if "media_content" in entry and entry.media_content:
        first_media = entry.media_content[0]
        if "url" in first_media:
            return first_media["url"]

    if "media_thumbnail" in entry and entry.media_thumbnail:
        first_thumb = entry.media_thumbnail[0]
        if "url" in first_thumb:
            return first_thumb["url"]

    # 3. Buscar tag <img> en el summary o content HTML
    html_snippets = []
    if "summary" in entry:
        html_snippets.append(entry.summary)
    if "content" in entry:
        for c in entry.content:
            html_snippets.append(c.get("value", ""))

    for snippet in html_snippets:
        if "<img" in snippet:
            try:
                soup = BeautifulSoup(snippet, "html.parser")
                img = soup.find("img")
                if img and img.get("src"):
                    return img["src"]
            except Exception:
                pass

    return None

def parse_entry_datetime(entry: dict) -> str:
    """Convierte la fecha de la noticia a formato SQL ISO YYYY-MM-DD HH:MM:SS."""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return time.strftime("%Y-%m-%d %H:%M:%S", entry.published_parsed)
    if hasattr(entry, "updated_parsed") and entry.updated_parsed:
        return time.strftime("%Y-%m-%d %H:%M:%S", entry.updated_parsed)
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def clean_summary_text(raw_html: str) -> str:
    """Elimina etiquetas HTML del resumen para dejar texto puro."""
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    text = soup.get_text(separator=" ", strip=True)
    return text[:280] + ("..." if len(text) > 280 else "")

def fetch_single_feed(source: dict) -> list[dict]:
    """Descarga y parsea una fuente individual."""
    articles = []
    try:
        parsed = feedparser.parse(source["url"])
        for entry in parsed.entries[:35]:  # Tomar las últimas 35 de cada feed
            link = entry.get("link", "").strip()
            title = entry.get("title", "").strip()
            if not link or not title:
                continue

            summary = clean_summary_text(entry.get("summary", entry.get("description", "")))
            img_url = extract_image_from_entry(entry)
            pub_date = parse_entry_datetime(entry)

            articles.append({
                "source_id": source["id"],
                "url": link,
                "titulo": title,
                "resumen": summary,
                "imagen": img_url,
                "fecha_publicacion": pub_date
            })
    except Exception as e:
        print(f"[ERROR] Error al sincronizar fuente {source.get('nombre')}: {e}")
    return articles

def sync_all_active_sources(sources: list[dict]) -> list[dict]:
    """Sincroniza todas las fuentes en paralelo con ThreadPoolExecutor."""
    all_articles = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        results = executor.map(fetch_single_feed, sources)
        for res in results:
            all_articles.extend(res)
    return all_articles

# --- CACHÉ DE IMÁGENES EN DISCO ---

def get_or_cache_image(img_url: str) -> Path | None:
    """Descarga la imagen a disco con nombre hash si no existe aún. Retorna la ruta."""
    if not img_url:
        return None
    try:
        url_hash = hashlib.sha256(img_url.encode("utf-8")).hexdigest()
        ext = Path(urlparse(img_url).path).suffix.lower()
        if ext not in [".jpg", ".jpeg", ".png", ".webp", ".gif"]:
            ext = ".jpg"
        
        file_path = CACHE_IMG_DIR / f"{url_hash}{ext}"
        if file_path.exists() and file_path.stat().st_size > 0:
            return file_path

        # Descargar si no está en disco
        res = requests.get(img_url, headers=HEADERS, timeout=6)
        if res.status_code == 200:
            file_path.write_bytes(res.content)
            return file_path
    except Exception:
        pass
    return None