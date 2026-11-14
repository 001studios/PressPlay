"""
db.py - SQLite con scoring BM25, clustering de noticias, temas ocultos y fatiga temática.
"""
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

DB_PATH = Path("newstovideo.db")

DEFAULT_SOURCES = [
    {"url": "https://www.infobae.com/arc/outboundfeeds/rss/", "nombre": "Infobae", "tipo": "rss"},
    {"url": "https://www.clarin.com/rss/lo-ultimo/", "nombre": "Clarín", "tipo": "rss"},
    {"url": "https://feeds.bbci.co.uk/mundo/rss.xml", "nombre": "BBC Mundo", "tipo": "rss"},
    {"url": "https://www.xataka.com/feedburner.xml", "nombre": "Xataka", "tipo": "rss"},
    {"url": "https://www.genbeta.com/feedburner.xml", "nombre": "Genbeta", "tipo": "rss"},
    {"url": "https://www.levelup.com/rss", "nombre": "LevelUp", "tipo": "rss"}
]

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE NOT NULL,
                nombre TEXT NOT NULL,
                tipo TEXT NOT NULL DEFAULT 'rss',
                activa INTEGER NOT NULL DEFAULT 1,
                fecha_agregada TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                url TEXT UNIQUE NOT NULL,
                titulo TEXT NOT NULL,
                resumen TEXT,
                imagen TEXT,
                fecha_publicacion TIMESTAMP,
                fecha_obtencion TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                texto_extraido TEXT,
                usada INTEGER DEFAULT 0,
                veces_mostrada INTEGER DEFAULT 0,
                FOREIGN KEY (source_id) REFERENCES sources (id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_articles_url ON articles(url)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_articles_pubdate ON articles(fecha_publicacion DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source_id)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS saved_articles (
                article_id INTEGER PRIMARY KEY,
                fecha_guardado TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (article_id) REFERENCES articles (id) ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS muted_topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT UNIQUE NOT NULL,
                fecha_agregado TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS interests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT UNIQUE NOT NULL,
                peso REAL NOT NULL DEFAULT 1.0,
                ultima_actualizacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id INTEGER NOT NULL,
                accion TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (article_id) REFERENCES articles (id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                clave TEXT PRIMARY KEY,
                valor TEXT NOT NULL
            )
        """)
        cursor.execute("SELECT COUNT(*) FROM sources")
        if cursor.fetchone()[0] == 0:
            for src in DEFAULT_SOURCES:
                cursor.execute("INSERT INTO sources (url, nombre, tipo, activa) VALUES (?, ?, ?, 1)", (src["url"], src["nombre"], src["tipo"]))
        conn.commit()

# --- BORRADOR ---

def save_generate_draft(srt: str, keywords: str):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO settings (clave, valor) VALUES ('draft_srt', ?) ON CONFLICT(clave) DO UPDATE SET valor = ?", (srt, srt))
        cursor.execute("INSERT INTO settings (clave, valor) VALUES ('draft_keywords', ?) ON CONFLICT(clave) DO UPDATE SET valor = ?", (keywords, keywords))
        conn.commit()

def get_generate_draft() -> dict:
    draft = {"srt": "", "keywords": ""}
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT clave, valor FROM settings WHERE clave IN ('draft_srt', 'draft_keywords')")
        for row in cursor.fetchall():
            if row["clave"] == "draft_srt": draft["srt"] = row["valor"]
            if row["clave"] == "draft_keywords": draft["keywords"] = row["valor"]
    return draft

# --- FUENTES Y ARTÍCULOS ---

def get_sources(only_active: bool = False) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        query = "SELECT id, url, nombre, tipo, activa, fecha_agregada FROM sources"
        if only_active: query += " WHERE activa = 1"
        query += " ORDER BY id ASC"
        cursor.execute(query)
        return [dict(row) for row in cursor.fetchall()]

def add_source(url: str, nombre: str, tipo: str = "rss") -> Dict[str, Any]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO sources (url, nombre, tipo, activa) VALUES (?, ?, ?, 1)", (url.strip(), nombre.strip(), tipo))
        conn.commit()
        return {"id": cursor.lastrowid, "url": url, "nombre": nombre, "tipo": tipo, "activa": 1}

def toggle_source(source_id: int, activa: bool):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE sources SET activa = ? WHERE id = ?", (1 if activa else 0, source_id))
        conn.commit()

def delete_source(source_id: int):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        conn.commit()

def insert_articles_batch(articles_data: List[Dict[str, Any]]) -> int:
    if not articles_data: return 0
    with get_connection() as conn:
        cursor = conn.cursor()
        query = "INSERT OR IGNORE INTO articles (source_id, url, titulo, resumen, imagen, fecha_publicacion) VALUES (:source_id, :url, :titulo, :resumen, :imagen, :fecha_publicacion)"
        cursor.executemany(query, articles_data)
        conn.commit()
        return cursor.rowcount

def get_article_by_id(article_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT a.*, s.nombre AS source_nombre,
                   CASE WHEN sa.article_id IS NOT NULL THEN 1 ELSE 0 END AS guardado
            FROM articles a
            JOIN sources s ON a.source_id = s.id
            LEFT JOIN saved_articles sa ON a.id = sa.article_id
            WHERE a.id = ?
        """, (article_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def update_article_text(article_id: int, text: str):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE articles SET texto_extraido = ? WHERE id = ?", (text, article_id))
        conn.commit()

def mark_article_as_used(article_id: int):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE articles SET usada = 1 WHERE id = ?", (article_id,))
        conn.commit()

# --- TEMAS OCULTOS ---

def add_muted_topic_db(topic: str):
    clean = topic.strip().lower()
    if not clean: return
    with get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO muted_topics (topic) VALUES (?)", (clean,))
        conn.commit()

def remove_muted_topic_db(topic_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM muted_topics WHERE id = ?", (topic_id,))
        conn.commit()

def get_muted_topics_db() -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.execute("SELECT id, topic, fecha_agregado FROM muted_topics ORDER BY topic ASC")
        return [dict(r) for r in cursor.fetchall()]

# --- GUARDADOS (VER MÁS TARDE) ---

def toggle_save_article_db(article_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT article_id FROM saved_articles WHERE article_id = ?", (article_id,))
        exists = cursor.fetchone()
        if exists:
            cursor.execute("DELETE FROM saved_articles WHERE article_id = ?", (article_id,))
            conn.commit()
            return False
        else:
            cursor.execute("INSERT INTO saved_articles (article_id) VALUES (?)", (article_id,))
            conn.commit()
            return True

def get_saved_articles_db() -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        query = """
            SELECT a.id, a.source_id, a.url, a.titulo, a.resumen, a.imagen, 
                   a.fecha_publicacion, a.texto_extraido, a.usada, s.nombre AS source_nombre,
                   sa.fecha_guardado, 1 AS guardado
            FROM saved_articles sa
            JOIN articles a ON sa.article_id = a.id
            JOIN sources s ON a.source_id = s.id
            ORDER BY sa.fecha_guardado DESC
        """
        cursor.execute(query)
        return [dict(row) for row in cursor.fetchall()]

# --- INTERACCIONES Y ALGORITMO ---

def record_interaction(article_id: int, accion: str):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO interactions (article_id, accion) VALUES (?, ?)", (article_id, accion))
        conn.commit()

def upsert_user_interest(keyword: str, weight_increment: float = 1.0):
    now_iso = datetime.now(timezone.utc).isoformat()
    clean_kw = keyword.strip().lower()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO interests (keyword, peso, ultima_actualizacion)
            VALUES (?, ?, ?)
            ON CONFLICT(keyword) DO UPDATE SET
                peso = MAX(-10.0, MIN(50.0, peso + ?)),
                ultima_actualizacion = ?
        """, (clean_kw, weight_increment, now_iso, weight_increment, now_iso))
        conn.commit()

def get_decayed_user_interests() -> Dict[str, float]:
    from app.recommender import calculate_decayed_weight
    interests_map = {}
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT keyword, peso, ultima_actualizacion FROM interests")
        for row in cursor.fetchall():
            decayed = calculate_decayed_weight(row["peso"], str(row["ultima_actualizacion"]))
            interests_map[row["keyword"]] = round(decayed, 4)
    return interests_map

def get_favorite_sources_scores() -> Dict[int, float]:
    scores = {}
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT a.source_id, COUNT(i.id) as interaction_count
            FROM interactions i
            JOIN articles a ON i.article_id = a.id
            WHERE i.accion IN ('copiada', 'generada', 'guardada')
            GROUP BY a.source_id
        """)
        rows = cursor.fetchall()
        if not rows: return {}
        max_count = max([r["interaction_count"] for r in rows]) or 1
        for r in rows:
            scores[r["source_id"]] = round(r["interaction_count"] / max_count, 3)
    return scores

def get_all_candidate_articles_global(source_id: Optional[int] = None, search_query: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        query = """
            SELECT a.id, a.source_id, a.url, a.titulo, a.resumen, a.imagen, 
                   a.fecha_publicacion, a.fecha_obtencion, a.texto_extraido, a.usada, a.veces_mostrada, 
                   s.nombre AS source_nombre,
                   CASE WHEN sa.article_id IS NOT NULL THEN 1 ELSE 0 END AS guardado
            FROM articles a 
            JOIN sources s ON a.source_id = s.id 
            LEFT JOIN saved_articles sa ON a.id = sa.article_id
            WHERE s.activa = 1
        """
        params: List[Any] = []
        if source_id: query += " AND a.source_id = ?"; params.append(source_id)
        if search_query:
            query += " AND (a.titulo LIKE ? OR a.resumen LIKE ?)"
            term = f"%{search_query.strip()}%"
            params.extend([term, term])

        query += " ORDER BY a.fecha_publicacion DESC LIMIT 250"
        cursor.execute(query, params)
        rows = cursor.fetchall()

        ids = [r["id"] for r in rows[:15]]
        if ids:
            p_holders = ",".join("?" * len(ids))
            conn.execute(f"UPDATE articles SET veces_mostrada = veces_mostrada + 1 WHERE id IN ({p_holders})", ids)
            conn.commit()

        return [dict(r) for r in rows]