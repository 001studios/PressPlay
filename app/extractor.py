"""
extractor.py - Extracción de texto limpio de artículos web.
Usa trafilatura como motor primario, respaldo basado en selectores HTML,
y detector de muros de pago con fallback al resumen del RSS.
"""
import re
from typing import Dict, Any
import requests
from bs4 import BeautifulSoup
import trafilatura

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

PAYWALL_KEYWORDS = [
    "suscríbete", "suscripcion", "suscripción", "artículo exclusivo",
    "exclusivo para suscriptores", "registrate para seguir leyendo",
    "iniciá sesión para continuar", "contenido restringido",
    "paywall", "acceso ilimitado"
]

def clean_extracted_text(text: str) -> str:
    """Elimina saltos de línea excesivos y espacios residuales."""
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n\n".join(lines)

def fallback_html_extraction(html: str) -> str:
    """Extractor secundario basado en etiquetas de artículo y párrafos."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        # Remover elementos indeseados
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
            tag.decompose()

        # Buscar contenedores típicos de noticias
        main_content = (
            soup.find("article") or 
            soup.find(class_=re.compile(r"(article|cuerpo|nota|entry|post)-content", re.I)) or
            soup.find("main") or
            soup.body
        )
        if not main_content:
            return ""

        paragraphs = [p.get_text(strip=True) for p in main_content.find_all("p") if len(p.get_text(strip=True)) > 25]
        return "\n\n".join(paragraphs)
    except Exception:
        return ""

def extract_article_content(url: str, fallback_summary: str = "") -> Dict[str, Any]:
    """
    Descarga y extrae el texto principal de una noticia.
    Retorna un diccionario con:
    - 'text': Texto extraído limpio.
    - 'word_count': Cantidad de palabras.
    - 'is_paywall': Booleano indicando si se detectó muro o contenido truncado.
    - 'method': 'trafilatura', 'html_fallback' o 'rss_summary'.
    """
    clean_url = url.strip()
    downloaded_html = None

    # 1. Descarga con Trafilatura
    try:
        downloaded_html = trafilatura.fetch_url(clean_url)
    except Exception:
        pass

    # Si trafilatura falla al descargar, usar requests directamente
    if not downloaded_html:
        try:
            res = requests.get(clean_url, headers=HEADERS, timeout=10)
            if res.status_code == 200:
                downloaded_html = res.text
        except Exception:
            pass

    extracted_text = ""
    method_used = "trafilatura"

    # 2. Extracción primaria con Trafilatura
    if downloaded_html:
        extracted_text = trafilatura.extract(
            downloaded_html,
            include_comments=False,
            include_tables=False,
            no_fallback=False
        ) or ""

    # 3. Respaldo HTML si Trafilatura devolvió texto vacío o muy pobre (< 80 palabras)
    words = extracted_text.split() if extracted_text else []
    if len(words) < 80 and downloaded_html:
        fallback_text = fallback_html_extraction(downloaded_html)
        if len(fallback_text.split()) > len(words):
            extracted_text = fallback_text
            words = extracted_text.split()
            method_used = "html_fallback"

    extracted_text = clean_extracted_text(extracted_text)
    word_count = len(words)

    # 4. Chequeo de Muro de Pago o Contenido Insuficiente (< 150 palabras)
    text_lower = extracted_text.lower()
    has_paywall_signal = any(pw in text_lower for pw in PAYWALL_KEYWORDS)
    is_short = word_count < 150

    is_paywall = has_paywall_signal or (is_short and word_count < 60)

    # Si la extracción fue bloqueada por paywall o es casi nula, recurrir al resumen del RSS
    if (is_paywall or word_count < 50) and fallback_summary:
        if len(fallback_summary.split()) > word_count:
            extracted_text = (
                f"{fallback_summary}\n\n"
                f"[Nota: No se pudo extraer el cuerpo completo por posible muro de pago o restricciones del sitio. "
                f"Se utilizó el resumen provisto por la fuente.]"
            )
            word_count = len(extracted_text.split())
            method_used = "rss_summary"
            is_paywall = True

    return {
        "text": extracted_text,
        "word_count": word_count,
        "is_paywall": is_paywall,
        "method": method_used
    }