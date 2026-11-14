"""
recommender.py - Motores algorítmicos avanzados:
- Detector multicapa de Clickbait con análisis sintáctico y de omisión de sujeto.
- Clustering reforzado de noticias repetidas por solapamiento de entidades clave.
- Funciones de categorización: Tendencias, Potencial Viral e Historias Ocultas.
- Filtrado normalizado estricto para Temas Ocultos y exclusión de guardados.
"""
import math
import re
import unicodedata
import random
from datetime import datetime, timezone
from collections import Counter
from typing import List, Dict, Any, Set

DECAY_RATE_PER_DAY = 0.95
RECENCY_HALF_LIFE_HOURS = 48.0

BM25_K1 = 1.5
BM25_B = 0.75
AVG_DOC_LEN = 120.0

SPANISH_STOPWORDS: Set[str] = {
    "de", "la", "que", "el", "en", "y", "a", "los", "del", "se", "las", "por", "un", "para",
    "con", "no", "una", "su", "al", "lo", "como", "mas", "pero", "sus", "le", "ya", "o",
    "este", "si", "porque", "esta", "entre", "cuando", "muy", "sin", "sobre", "tambien",
    "me", "hasta", "hay", "donde", "quien", "desde", "todo", "nos", "durante", "todos",
    "uno", "les", "ni", "contra", "otros", "ese", "eso", "ante", "ellos", "e", "esto",
    "antes", "algunos", "unos", "yo", "otro", "otras", "otra", "tanto", "esa", "tras", "hace"
}

# Léxico de alta carga emocional / sensacionalismo
SENSATIONAL_LEXICON = {
    "bomba", "estallo", "estallaron", "furor", "brutal", "impactante", "insolito", "polemica",
    "polemico", "escandalo", "lapidario", "lapidaria", "feroz", "durisimo", "durisima",
    "demoledor", "demoledora", "insolita", "alerta", "urgente", "inesperado", "inesperada",
    "quiebre", "terremoto", "terrible", "desesperacion", "secreto", "oculto", "revoluciona",
    "tajante", "sin piedad", "fulminante", "drama", "tragico", "conmocion", "peor", "devastador",
    "rebelion", "traicion", "pesadilla", "panico", "caos", "misterioso", "misteriosa"
}

VIRAL_KEYWORDS = {
    "controversia": ["polemica", "escandalo", "renuncia", "demanda", "juicio", "estafa", "denuncia", "furia"],
    "impacto": ["crisis", "millones", "dolares", "despidos", "aumento", "quiebra", "inflacion", "record", "caida"],
    "sorpresa": ["inesperado", "revoluciona", "historico", "descubren", "oculto", "secreto", "insolito"],
    "debate": ["prohiben", "ley", "debate", "gobierno", "sancion", "rechazo", "decision"]
}

def strip_accents(text: str) -> str:
    """Elimina acentos y diacríticos para comparaciones normalizadas infalibles."""
    if not text: return ""
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn").lower()

def clean_and_tokenize(text: str) -> List[str]:
    if not text: return []
    norm = strip_accents(text)
    words = re.findall(r"[a-z0-9]{3,}", norm)
    return [w for w in words if w not in SPANISH_STOPWORDS and not w.isdigit()]

def extract_entities_and_bigrams(text: str) -> List[str]:
    if not text: return []
    proper = re.findall(r"\b[A-ZÁÉÍÓÚÑ0-9][a-záéíóúñA-Z0-9_-]{2,}\b", text)
    tokens = clean_and_tokenize(text)
    bigrams = [f"{tokens[i]} {tokens[i+1]}" for i in range(len(tokens) - 1)]
    return [strip_accents(p) for p in proper if strip_accents(p) not in SPANISH_STOPWORDS] + bigrams

def extract_keywords_from_article(title: str, body: str, max_keywords: int = 15) -> Dict[str, float]:
    counts = Counter()
    for t in clean_and_tokenize(title): counts[t] += 4.5
    for b in clean_and_tokenize(body)[:300]: counts[b] += 1.0
    for ent in extract_entities_and_bigrams(f"{title}. {body}"): counts[ent] += 4.0
    total = sum(counts.values()) or 1.0
    return {k: round(v / total, 4) for k, v in counts.most_common(max_keywords)}

def calculate_decayed_weight(initial_weight: float, last_updated_iso: str) -> float:
    try:
        dt = datetime.fromisoformat(last_updated_iso.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        days_passed = max(0.0, (now - dt).total_seconds() / 86400.0)
        return initial_weight * math.pow(DECAY_RATE_PER_DAY, days_passed)
    except Exception:
        return initial_weight

# --- DETECTOR MULTICAPA DE CLICKBAIT (0-100%) ---

def analyze_clickbait(title: str) -> Dict[str, Any]:
    if not title:
        return {"score": 10, "level": "low", "label": "10% Informativo", "css_class": "cb-green", "tooltip": "Sin título"}

    raw = title.strip()
    norm = strip_accents(raw)
    score = 12  # Base mínima natural
    reasons = []

    # 1. Omisión voluntaria del sujeto (Entidades fantasma para forzar el clic)
    # Ej: "Un famoso actor confesó...", "Una conocida empresa cerró..."
    if re.search(r"\b(un|una)\s+(famoso|famosa|conocido|conocida|historico|historica|importante|gigante|estrella|figura)\b", norm):
        score += 28
        reasons.append("Ocultamiento deliberado del sujeto")

    # 2. Citas sensacionalistas entre comillas ("Fue terrible", "No puedo más")
    quotes = re.findall(r'["“«\']([^"”»\']+)["”»\']', raw)
    if quotes:
        score += 22
        reasons.append("Declaración descontextualizada entre comillas")

    # 3. Preguntas retóricas en el titular (¿Habrá crisis?, ¿Es el fin?)
    if "?" in raw or "¿" in raw:
        score += 24
        reasons.append("Pregunta retórica en el titular")

    # 4. Signos de exclamación o puntos suspensivos de intriga
    if "!" in raw or "¡" in raw:
        score += 20
        reasons.append("Exclamaciones enfáticas")
    if "..." in raw or "…" in raw:
        score += 18
        reasons.append("Puntos suspensivos de suspenso")

    # 5. Fórmulas de cebo de curiosidad y deixis ("Por qué", "La razón por la que", "Lo que pasó")
    curiosity_triggers = [
        "por que", "la razon de", "el motivo de", "este es el", "esta es la",
        "asi fue", "lo que paso", "lo que nadie", "lo que no sabias", "no vas a creer"
    ]
    if any(norm.startswith(ct) or f": {ct}" in norm for ct in curiosity_triggers):
        score += 32
        reasons.append("Cebo de curiosidad al inicio")

    # 6. Léxico de impacto / sobrecarga emocional
    tokens = set(re.findall(r"[a-z0-9]{3,}", norm))
    matches = tokens.intersection(SENSATIONAL_LEXICON)
    if matches:
        score += min(34, len(matches) * 17)
        reasons.append(f"Términos de impacto: {', '.join(list(matches)[:2])}")

    # 7. Listas anzuelo ("Las 5 claves...", "Los 3 motivos...")
    if re.search(r"\b\d+\s+(claves|motivos|razones|secretos|cosas|pasos)\b", norm):
        score += 25
        reasons.append("Estructura de lista anzuelo")

    # 8. Mayúsculas excesivas
    caps = sum(1 for c in raw if c.isupper())
    if caps / max(len(raw), 1) > 0.28:
        score += 25
        reasons.append("Mayúsculas sostenidas")

    final_score = min(96, score)

    # Clasificación en 3 bandas de color bien diferenciadas
    if final_score >= 58:
        return {
            "score": final_score, "level": "high",
            "label": f"{final_score}% Clickbait",
            "css_class": "cb-red",
            "tooltip": " · ".join(reasons) or "Sensacionalismo alto"
        }
    elif final_score >= 32:
        return {
            "score": final_score, "level": "medium",
            "label": f"{final_score}% Sensacional",
            "css_class": "cb-yellow",
            "tooltip": " · ".join(reasons) or "Ligeramente inflado"
        }
    return {
        "score": final_score, "level": "low",
        "label": f"{final_score}% Informativo",
        "css_class": "cb-green",
        "tooltip": " · ".join(reasons) if reasons else "Titular fáctico y directo"
    }

# --- CLUSTERING REFORZADO POR ENTIDADES CLAVE ---

def extract_core_entities(text: str) -> Set[str]:
    tokens = set(clean_and_tokenize(text))
    return {t for t in tokens if len(t) >= 4 or any(c.isdigit() for c in t)}

def are_articles_same_event(art1: Dict[str, Any], art2: Dict[str, Any]) -> bool:
    core1 = extract_core_entities(art1.get("titulo", ""))
    core2 = extract_core_entities(art2.get("titulo", ""))
    if not core1 or not core2: return False

    intersection = core1.intersection(core2)
    if len(intersection) >= 2:
        return True

    min_len = min(len(core1), len(core2))
    overlap = len(intersection) / min_len if min_len > 0 else 0.0
    return overlap >= 0.45

def group_articles_into_clusters(articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    clusters = []

    for art in articles:
        matched = None
        for cl in clusters:
            if are_articles_same_event(art, cl["representative"]):
                matched = cl
                break

        if matched:
            matched["items"].append(art)
            matched["fuentes_adicionales"].append(art.get("source_nombre", "Fuente"))
        else:
            clusters.append({
                "representative": art,
                "items": [art],
                "fuentes_adicionales": [art.get("source_nombre", "Fuente")]
            })

    result = []
    for cl in clusters:
        rep = cl["representative"].copy()
        unique_sources = list(dict.fromkeys(cl["fuentes_adicionales"]))
        rep["fuentes_count"] = len(cl["items"])
        rep["fuentes_nombres"] = unique_sources
        rep["cluster_ids"] = [it["id"] for it in cl["items"]]

        combined_text = rep.get("texto_extraido") or rep.get("resumen") or ""
        for other in cl["items"][1:4]:
            o_text = other.get("texto_extraido") or other.get("resumen") or ""
            if len(o_text) > len(combined_text):
                combined_text = o_text
        rep["texto_combinado"] = combined_text
        result.append(rep)

    return result

# --- FUNCIONES DE CATEGORIZACIÓN ---

def compute_trend_score(cluster: Dict[str, Any]) -> float:
    fuentes = cluster.get("fuentes_count", 1)
    pub_date_str = cluster.get("fecha_publicacion") or cluster.get("fecha_obtencion")
    hours_old = 12.0
    if pub_date_str:
        try:
            pub_dt = datetime.strptime(str(pub_date_str).replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
            hours_old = max(0.5, (datetime.utcnow() - pub_dt).total_seconds() / 3600.0)
        except Exception: pass
    return round((fuentes ** 1.8) / (hours_old + 1.0), 3)

def compute_viral_potential_score(title: str, summary: str) -> int:
    text = strip_accents(f"{title} {summary}")
    score = 15
    for cat, kws in VIRAL_KEYWORDS.items():
        matches = sum(1 for kw in kws if kw in text)
        score += min(22, matches * 11)
    return min(100, score)

def is_underreported_story(cluster: Dict[str, Any]) -> bool:
    fuentes = cluster.get("fuentes_count", 1)
    words = len((cluster.get("resumen") or "").split())
    return fuentes <= 2 and words > 35

# --- BM25 Y EXPLORACIÓN ---

def calculate_bm25_score(query_terms: Dict[str, float], doc_tokens: List[str]) -> float:
    if not query_terms or not doc_tokens: return 0.0
    doc_len = len(doc_tokens)
    term_counts = Counter(doc_tokens)
    score = 0.0
    for term, term_weight in query_terms.items():
        if term in term_counts:
            tf = term_counts[term]
            num = tf * (BM25_K1 + 1.0)
            den = tf + BM25_K1 * (1.0 - BM25_B + BM25_B * (doc_len / AVG_DOC_LEN))
            score += term_weight * (num / den)
    return min(1.0, score / 4.0)

def calculate_article_score(article: Dict[str, Any], user_interests: Dict[str, float], favorite_sources: Dict[int, float]) -> float:
    title = article.get("titulo", "")
    summary = article.get("resumen", "")
    tokens = clean_and_tokenize(f"{title}. {summary}")

    bm25_sim = calculate_bm25_score(user_interests, tokens)

    pub_date_str = article.get("fecha_publicacion") or article.get("fecha_obtencion")
    hours_old = 72.0
    if pub_date_str:
        try:
            pub_dt = datetime.strptime(str(pub_date_str).replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
            hours_old = max(0.1, (datetime.utcnow() - pub_dt).total_seconds() / 3600.0)
        except Exception: pass
    recency = math.exp(-math.log(2) * (hours_old / RECENCY_HALF_LIFE_HOURS))

    views = article.get("veces_mostrada", 0)
    fatigue = min(0.60, (views - 12) * 0.05) if views > 12 else 0.0
    used_penalty = 0.95 if article.get("usada", 0) else 0.0

    if user_interests:
        base = (0.50 + 0.40 * bm25_sim + 0.10 * recency) if bm25_sim > 0.05 else (0.02 + 0.05 * recency)
    else:
        base = 0.20 + 0.30 * recency

    return max(0.01, round(base - used_penalty - fatigue, 3))

def apply_controlled_exploration(articles: List[Dict[str, Any]], pct_personal: int = 70, pct_related: int = 20, pct_discovery: int = 10) -> List[Dict[str, Any]]:
    if len(articles) <= 5: return articles
    sorted_arts = sorted(articles, key=lambda x: x.get("score", 0), reverse=True)
    n_total = len(sorted_arts)
    n_pers = max(1, int(n_total * (pct_personal / 100.0)))
    n_rel = max(1, int(n_total * (pct_related / 100.0)))

    pool_pers = sorted_arts[:n_pers]
    pool_rel = sorted_arts[n_pers:n_pers + n_rel]
    pool_disc = sorted_arts[n_pers + n_rel:]
    random.shuffle(pool_disc)

    feed = []
    i, j, k = 0, 0, 0
    while i < len(pool_pers) or j < len(pool_rel) or k < len(pool_disc):
        for _ in range(7):
            if i < len(pool_pers): feed.append(pool_pers[i]); i += 1
        for _ in range(2):
            if j < len(pool_rel): feed.append(pool_rel[j]); j += 1
        if k < len(pool_disc): feed.append(pool_disc[k]); k += 1

    return feed