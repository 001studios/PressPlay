"""
main.py - Endpoints REST con soporte para Subtítulos Dinámicos estilo TikTok y configuración persistente.
"""
from pathlib import Path
import json
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app.db import (
    init_db, get_sources, add_source, toggle_source as db_toggle_source, delete_source,
    insert_articles_batch, get_article_by_id, update_article_text, record_interaction,
    get_all_candidate_articles_global, get_decayed_user_interests, get_favorite_sources_scores,
    save_generate_draft, get_generate_draft, toggle_save_article_db, get_saved_articles_db,
    upsert_user_interest, add_muted_topic_db, remove_muted_topic_db, get_muted_topics_db
)
from app.feed import detect_rss, sync_all_active_sources, get_or_cache_image
from app.extractor import extract_article_content
from app.prompt_builder import build_prompt_and_copy, calculate_ideal_duration_from_text
from app.tts import list_available_voices, preview_voice_sample, QWEN_STYLES
from app.renderer import current_render_job, execute_video_generation_pipeline
from app.recommender import (
    calculate_article_score, group_articles_into_clusters, analyze_clickbait,
    compute_trend_score, compute_viral_potential_score, is_underreported_story,
    apply_controlled_exploration, extract_keywords_from_article, strip_accents,
    are_articles_same_event
)

CONFIG_FILE = Path("config.json")
app = FastAPI(title="NewsToVideo API", version="1.3.0")
STATIC_DIR = Path(__file__).parent / "static"

@app.on_event("startup")
def on_startup():
    init_db()

class ToggleSourceRequest(BaseModel): activa: bool
class AddSourceRequest(BaseModel): url: str
class CopyPromptRequest(BaseModel): duration: str
class DraftRequest(BaseModel): srt: str = ""; keywords: str = ""
class MutedTopicRequest(BaseModel): topic: str

class SettingsPayload(BaseModel):
    output_dir: str = "output"
    music_dir: str = "music"
    default_tts_voice: str = "Alex"
    tts_engine: str = "qwen"
    tts_speed: str = "+8%"
    video_mode: str = "loop"
    video_quality: str = "720p"
    loop_clips_count: int = 15
    enable_tiktok_subtitles: bool = True
    subtitle_highlight_color: str = "#FFE600"
    exploration_personal: int = 70
    exploration_related: int = 20
    exploration_discovery: int = 10
    qwen_gradio_url: str = "FunAudioLLM/CosyVoice"
    qwen_style: str = "youtube_energetic"
    qwen_custom_prompt: str = ""
    hf_token: str = ""
    pexels_api_key: str = ""
    pixabay_api_key: str = ""
    enable_youtube: bool = True
    enable_wikimedia: bool = True
    enable_pexels: bool = False
    enable_pixabay: bool = False
    custom_clip_sources: List[Dict[str, Any]] = []

class GenerateVideoRequest(BaseModel):
    srt: str
    keywords: List[str]
    voice: Optional[str] = None
    tts_engine: Optional[str] = None
    article_id: Optional[int] = None

def load_config() -> dict:
    cfg = {
        "output_dir": "output", "music_dir": "music", "default_tts_voice": "Alex",
        "tts_engine": "qwen", "tts_speed": "+8%", "video_mode": "loop", "video_quality": "720p",
        "loop_clips_count": 15, "enable_tiktok_subtitles": True, "subtitle_highlight_color": "#FFE600",
        "exploration_personal": 70, "exploration_related": 20, "exploration_discovery": 10,
        "qwen_gradio_url": "FunAudioLLM/CosyVoice", "qwen_style": "youtube_energetic",
        "qwen_custom_prompt": "", "hf_token": "", "pexels_api_key": "", "pixabay_api_key": "",
        "enable_youtube": True, "enable_wikimedia": True, "enable_pexels": False,
        "enable_pixabay": False, "custom_clip_sources": []
    }
    if CONFIG_FILE.exists():
        try: cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception: pass
    return cfg

def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

# --- FEED CON EXCLUSIÓN DE GUARDADOS Y TEMAS OCULTOS ---

@app.get("/api/feed")
def get_feed(
    page: int = Query(1, ge=1),
    limit: int = Query(15, ge=5, le=50),
    source_id: Optional[int] = Query(None),
    q: Optional[str] = Query(None),
    order: str = Query("score")
):
    raw_articles = get_all_candidate_articles_global(source_id=source_id, search_query=q)
    if not raw_articles: return {"page": page, "count": 0, "articles": []}

    saved_articles = get_saved_articles_db()
    saved_ids = {s["id"] for s in saved_articles}
    muted_terms = [strip_accents(m["topic"].strip()) for m in get_muted_topics_db() if m["topic"].strip()]

    filtered = []
    for art in raw_articles:
        if art["id"] in saved_ids:
            continue
        if any(are_articles_same_event(art, saved) for saved in saved_articles):
            continue
        art_text = strip_accents(f"{art.get('titulo','')} {art.get('resumen','')} {art.get('source_nombre','')}")
        if any(mt in art_text for mt in muted_terms):
            continue
        filtered.append(art)

    clustered = group_articles_into_clusters(filtered)
    for cl in clustered:
        cl["clickbait"] = analyze_clickbait(cl["titulo"])

    cfg = load_config()

    if order == "trend":
        for cl in clustered: cl["trend_score"] = compute_trend_score(cl)
        clustered.sort(key=lambda x: x["trend_score"], reverse=True)
    elif order == "viral":
        for cl in clustered: cl["viral_score"] = compute_viral_potential_score(cl["titulo"], cl.get("resumen",""))
        clustered.sort(key=lambda x: x["viral_score"], reverse=True)
    elif order == "underreported":
        clustered = [cl for cl in clustered if is_underreported_story(cl)]
    elif order == "recent":
        clustered.sort(key=lambda x: x.get("fecha_publicacion") or "", reverse=True)
    else:
        interests = get_decayed_user_interests()
        fav_sources = get_favorite_sources_scores()
        for cl in clustered:
            cl["score"] = calculate_article_score(cl, interests, fav_sources)
        clustered = apply_controlled_exploration(
            clustered,
            pct_personal=cfg.get("exploration_personal", 70),
            pct_related=cfg.get("exploration_related", 20),
            pct_discovery=cfg.get("exploration_discovery", 10)
        )

    offset = (page - 1) * limit
    batch = clustered[offset:offset + limit]
    return {"page": page, "count": len(batch), "articles": batch}

@app.post("/api/articles/{article_id}/dislike")
def api_dislike_article(article_id: int):
    art = get_article_by_id(article_id)
    if not art: raise HTTPException(status_code=404, detail="No encontrado")
    record_interaction(article_id, "no_interesa")
    extracted = extract_keywords_from_article(art.get("titulo", ""), art.get("resumen", ""))
    for term, weight in extracted.items():
        upsert_user_interest(term, weight_increment=round(-3.5 * weight, 2))
    return {"status": "ok", "article_id": article_id}

# --- TEMAS OCULTOS ---

@app.get("/api/muted-topics")
def api_get_muted_topics():
    return {"topics": get_muted_topics_db()}

@app.post("/api/muted-topics")
def api_add_muted_topic(payload: MutedTopicRequest):
    topic = payload.topic.strip()
    if not topic: raise HTTPException(status_code=400, detail="Tema vacío")
    add_muted_topic_db(topic)
    return {"status": "ok", "topic": topic}

@app.delete("/api/muted-topics/{topic_id}")
def api_remove_muted_topic(topic_id: int):
    remove_muted_topic_db(topic_id)
    return {"status": "ok", "deleted_id": topic_id}

# --- GUARDADOS (VER MÁS TARDE) ---

@app.post("/api/articles/{article_id}/toggle-save")
def api_toggle_save(article_id: int):
    is_saved = toggle_save_article_db(article_id)
    if is_saved:
        record_interaction(article_id, "guardada")
        art = get_article_by_id(article_id)
        if art:
            extracted = extract_keywords_from_article(art.get("titulo", ""), art.get("resumen", ""))
            for term, weight in extracted.items():
                upsert_user_interest(term, weight_increment=round(4.0 * weight, 2))
    return {"status": "ok", "article_id": article_id, "guardado": is_saved}

@app.get("/api/articles/saved")
def api_get_saved():
    saved = get_saved_articles_db()
    for s in saved: s["clickbait"] = analyze_clickbait(s["titulo"])
    return {"count": len(saved), "articles": saved}

@app.post("/api/feed/refresh")
def refresh_feed():
    active = get_sources(only_active=True)
    if not active: return {"status": "ok", "inserted": 0}
    scraped = sync_all_active_sources(active)
    inserted = insert_articles_batch(scraped)
    return {"status": "ok", "inserted": inserted}

@app.get("/api/image-proxy")
def image_proxy(url: str):
    cached_path = get_or_cache_image(url)
    if cached_path and cached_path.exists(): return FileResponse(cached_path)
    svg = """<svg xmlns="http://www.w3.org/2000/svg" width="600" height="340"><rect width="600" height="340" fill="#18181b"/><text x="50%" y="50%" fill="#52525b" font-family="sans-serif" font-size="20" text-anchor="middle" dy=".3em">Sin imagen</text></svg>"""
    return Response(content=svg, media_type="image/svg+xml")

# --- TTS Y PROMPTS ---

@app.get("/api/tts/voices")
async def get_voices(engine: str = Query("qwen")):
    voices = await list_available_voices(engine=engine.lower().strip())
    return Response(content=json.dumps({"voices": voices}), media_type="application/json")

@app.get("/api/tts/preview")
async def api_preview_voice(
    voice: str = Query(...), engine: str = Query("qwen"), style: str = Query("youtube_energetic"),
    custom_prompt: str = Query(""), qwen_url: str = Query("FunAudioLLM/CosyVoice"),
    hf_token: str = Query(""), speed_rate: str = Query("+8%")
):
    try:
        sample_path = await preview_voice_sample(
            voice=voice.strip(), engine=engine.strip(), style=style.strip(),
            custom_prompt=custom_prompt.strip(), qwen_url=qwen_url.strip(),
            hf_token=hf_token.strip(), speed_rate=speed_rate.strip()
        )
        return FileResponse(sample_path, media_type="audio/wav" if sample_path.suffix == ".wav" else "audio/mpeg")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/articles/{article_id}/ideal-duration")
def api_get_ideal_duration(article_id: int):
    article = get_article_by_id(article_id)
    if not article: raise HTTPException(status_code=404, detail="No encontrado")
    text = article.get("texto_extraido")
    if not text:
        res = extract_article_content(url=article["url"], fallback_summary=article.get("resumen", ""))
        text = res["text"]
        update_article_text(article_id, text)
    calc = calculate_ideal_duration_from_text(text)
    return {"article_id": article_id, "title": article["titulo"], "ideal_duration": calc["formatted_duration"], "word_count": calc["word_count"]}

@app.post("/api/articles/{article_id}/copy-prompt")
def api_copy_prompt(article_id: int, payload: CopyPromptRequest):
    article = get_article_by_id(article_id)
    if not article: raise HTTPException(status_code=404, detail="No encontrado")
    text = article.get("texto_extraido")
    if not text:
        res = extract_article_content(url=article["url"], fallback_summary=article.get("resumen", ""))
        text = res["text"]
        update_article_text(article_id, text)
    built = build_prompt_and_copy(article=article, extracted_text=text, duration_input=payload.duration)
    record_interaction(article_id, "copiada")
    return {"status": "ok", "duration_seconds": built["duration_seconds"], "prompt": built["prompt"]}

@app.post("/api/generate")
async def start_generation(payload: GenerateVideoRequest, background_tasks: BackgroundTasks):
    if current_render_job["status"] == "processing": raise HTTPException(status_code=400, detail="Render en proceso")
    cfg = load_config()
    chosen_voice = payload.voice or cfg.get("default_tts_voice", "Alex")
    if payload.tts_engine: cfg["tts_engine"] = payload.tts_engine
    background_tasks.add_task(execute_video_generation_pipeline, srt_text=payload.srt, keywords=payload.keywords, voice=chosen_voice, cfg=cfg, article_id=payload.article_id)
    return {"status": "ok", "message": "Generación iniciada"}

@app.get("/api/generation/progress")
def get_progress(): return current_render_job

@app.get("/api/video/{filename}")
def serve_generated_video(filename: str):
    p = Path(load_config().get("output_dir", "output")).resolve() / filename
    if not p.exists(): raise HTTPException(status_code=404, detail="No encontrado")
    return FileResponse(p, media_type="video/mp4")

@app.get("/api/settings")
def get_settings(): return load_config()

@app.post("/api/settings")
def update_settings(payload: SettingsPayload):
    cfg = payload.dict()
    save_config(cfg)
    return {"status": "ok", "settings": cfg}

@app.get("/api/generate/draft")
def api_get_draft(): return get_generate_draft()

@app.post("/api/generate/draft")
def api_save_draft(payload: DraftRequest):
    save_generate_draft(payload.srt, payload.keywords)
    return {"status": "ok"}

@app.get("/api/sources")
def list_sources(): return {"sources": get_sources()}

@app.post("/api/sources/detect")
def detect_and_add(payload: AddSourceRequest):
    det = detect_rss(payload.url)
    return {"status": "ok", "source": add_source(det["url"], det["nombre"], det["tipo"])}

@app.patch("/api/sources/{source_id}/toggle")
def toggle_src(source_id: int, payload: ToggleSourceRequest):
    db_toggle_source(source_id, payload.activa)
    return {"status": "ok"}

@app.delete("/api/sources/{source_id}")
def remove_src(source_id: int):
    delete_source(source_id)
    return {"status": "ok"}

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
def serve_index(): return FileResponse(STATIC_DIR / "index.html")