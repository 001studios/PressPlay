/**
 * app.js - Gestión integral: Subtítulos dinámicos TikTok, Fuentes, Cálculo real de duración y Clickbait.
 */

const state = {
  feedPage: 1,
  feedLimit: 15,
  isLoadingFeed: false,
  orderFilter: "score",
  sourceFilter: "",
  searchQuery: "",
  allVoices: [],
  selectedEngine: "qwen",
  activeModalArticleId: null,
  renderedArticleIds: new Set(),
  durationCache: {},
  sampleAudio: null
};

document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initSourcesModule();
  initFeedModule();
  initMutedTopicsModule();
  initSavedModule();
  initDurationModal();
  initSettingsModule();
  initGenerateModule();
  initVoicePreview();
  loadSavedCounter();
  loadMutedTopics();
  loadSources();
});

function showToast(msg, isError = false) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.style.borderColor = isError ? "var(--danger)" : "var(--border-color)";
  t.style.display = "block";
  setTimeout(() => { t.style.display = "none"; }, 3500);
}

function initTabs() {
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.onclick = () => {
      const tab = btn.getAttribute("data-tab");
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));
      document.getElementById(`tab-${tab}`).classList.add("active");

      if (tab === "sources") loadSources();
      if (tab === "saved") loadSavedArticles();
      if (tab === "muted") loadMutedTopics();
    };
  });
}

// --- FUENTES RSS ---

function initSourcesModule() {
  const form = document.getElementById("add-source-form");
  const input = document.getElementById("source-url-input");
  const btn = document.getElementById("add-source-btn");

  if (form) {
    form.onsubmit = async (e) => {
      e.preventDefault();
      const url = input.value.trim();
      if (!url) return;
      btn.disabled = true;
      try {
        const res = await fetch("/api/sources/detect", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url })
        });
        const d = await res.json();
        if (res.ok) {
          showToast(`Fuente agregada: ${d.source.nombre}`);
          input.value = "";
          loadSources();
          loadSourcesDropdown();
          resetAndReloadFeed();
        } else {
          showToast(d.detail || "No se detectó un RSS válido", true);
        }
      } catch(e) {
        showToast("Error de conexión al agregar fuente", true);
      } finally {
        btn.disabled = false;
      }
    };
  }
}

async function loadSources() {
  const container = document.getElementById("sources-container");
  const countSpan = document.getElementById("sources-count");
  if (!container) return;

  container.innerHTML = "<p style='color: var(--accent-muted); font-size: 0.85rem;'>Cargando fuentes...</p>";
  try {
    const res = await fetch("/api/sources");
    const d = await res.json();
    const sources = d.sources || [];
    if (countSpan) countSpan.textContent = sources.length;
    container.innerHTML = "";

    if (sources.length === 0) {
      container.innerHTML = "<p style='color: var(--accent-muted); font-size: 0.85rem;'>No hay fuentes registradas.</p>";
      return;
    }

    sources.forEach(src => {
      const item = document.createElement("div");
      item.className = "source-item";
      item.innerHTML = `
        <div class="source-info">
          <span class="source-name">${escapeHtml(src.nombre)}</span>
          <span class="source-url">${escapeHtml(src.url)}</span>
        </div>
        <div class="source-actions">
          <button class="btn-action-icon btn-toggle-src ${src.activa ? 'active' : ''}" data-id="${src.id}" data-active="${src.activa}">
            <span>${src.activa ? 'Activa' : 'Inactiva'}</span>
          </button>
          <button class="btn-action-icon dislike btn-delete-src" data-id="${src.id}">
            <span>Eliminar</span>
          </button>
        </div>
      `;

      item.querySelector(".btn-toggle-src").onclick = async (e) => {
        const id = src.id;
        const newActiva = !src.activa;
        await fetch(`/api/sources/${id}/toggle`, {
          method: "PATCH", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ activa: newActiva })
        });
        loadSources();
        resetAndReloadFeed();
      };

      item.querySelector(".btn-delete-src").onclick = async () => {
        if (confirm(`¿Eliminar la fuente "${src.nombre}"?`)) {
          await fetch(`/api/sources/${src.id}`, { method: "DELETE" });
          loadSources();
          loadSourcesDropdown();
          resetAndReloadFeed();
          showToast("Fuente eliminada");
        }
      };

      container.appendChild(item);
    });
  } catch(e) {
    container.innerHTML = "<p style='color: var(--danger); font-size: 0.85rem;'>Error al cargar fuentes.</p>";
  }
}

// --- FEED Y TARJETAS ---

function initFeedModule() {
  const orderSel = document.getElementById("feed-order-filter");
  const srcSel = document.getElementById("feed-source-filter");
  const searchInput = document.getElementById("feed-search-input");
  const refreshBtn = document.getElementById("feed-refresh-btn");
  const sentinel = document.getElementById("scroll-sentinel");

  new IntersectionObserver((entries) => {
    if (entries[0].isIntersecting && !state.isLoadingFeed) loadNextFeedBatch();
  }, { rootMargin: "600px" }).observe(sentinel);

  orderSel.onchange = (e) => { state.orderFilter = e.target.value; resetAndReloadFeed(); };
  srcSel.onchange = (e) => { state.sourceFilter = e.target.value; resetAndReloadFeed(); };
  searchInput.oninput = (e) => {
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(() => { state.searchQuery = e.target.value.trim(); resetAndReloadFeed(); }, 300);
  };

  refreshBtn.onclick = async () => {
    refreshBtn.classList.add("spinning");
    try {
      const res = await fetch("/api/feed/refresh", { method: "POST" });
      const d = await res.json();
      showToast(`Sincronizadas +${d.inserted} noticias nuevas`);
      resetAndReloadFeed();
    } catch(e) {}
    finally { refreshBtn.classList.remove("spinning"); }
  };

  loadSourcesDropdown();
  loadNextFeedBatch();
}

function resetAndReloadFeed() {
  state.feedPage = 1;
  state.renderedArticleIds.clear();
  document.getElementById("feed-container").innerHTML = "";
  loadNextFeedBatch();
}

async function loadNextFeedBatch() {
  if (state.isLoadingFeed) return;
  state.isLoadingFeed = true;
  document.getElementById("feed-skeleton").style.display = "flex";

  const params = new URLSearchParams({ page: state.feedPage, limit: state.feedLimit, order: state.orderFilter });
  if (state.sourceFilter) params.append("source_id", state.sourceFilter);
  if (state.searchQuery) params.append("q", state.searchQuery);

  try {
    const res = await fetch(`/api/feed?${params.toString()}`);
    const data = await res.json();
    renderFeedArticles(data.articles || []);
    state.feedPage++;
  } catch(e) {}
  finally {
    state.isLoadingFeed = false;
    document.getElementById("feed-skeleton").style.display = "none";
  }
}

function renderFeedArticles(articles) {
  const container = document.getElementById("feed-container");
  articles.forEach(art => {
    if (state.renderedArticleIds.has(art.id)) return;
    state.renderedArticleIds.add(art.id);
    container.appendChild(createArticleCard(art));
  });
  attachCardEvents(container);
}

function createArticleCard(art) {
  const card = document.createElement("article");
  card.className = "article-card";
  card.id = `art-card-${art.id}`;

  const imgUrl = art.imagen ? `/api/image-proxy?url=${encodeURIComponent(art.imagen)}` : "/api/image-proxy?url=";
  const isSaved = art.guardado === 1;
  const cb = art.clickbait || { score: 10, label: "10% Informativo", css_class: "cb-green", tooltip: "Informativo" };

  let clusterHtml = "";
  if (art.fuentes_count && art.fuentes_count > 1) {
    clusterHtml = `<div class="card-cluster-badge" title="Medios: ${art.fuentes_nombres.join(', ')}">🗂️ ${art.fuentes_count} Medios cubriendo esto</div>`;
  }

  card.innerHTML = `
    <div class="card-media">
      ${clusterHtml}
      <img class="card-img" src="${imgUrl}" alt="${escapeHtml(art.titulo)}" loading="lazy" />
    </div>
    <div class="card-body">
      <div class="card-meta">
        <span class="source-badge">${escapeHtml(art.source_nombre)}</span>
        <span>${formatTimeAgo(art.fecha_publicacion)}</span>
      </div>

      <!-- TÍTULO CON RECUADRO DE CLICKBAIT AL LADO -->
      <div class="card-title-row">
        <h2 class="card-title">
          <a href="${escapeHtml(art.url)}" target="_blank">${escapeHtml(art.titulo)}</a>
        </h2>
        <div class="clickbait-box ${cb.css_class}" title="${escapeHtml(cb.tooltip)}">
          <span>${cb.score}%</span>
          <span class="cb-sub">${cb.level === 'high' ? 'Clickbait' : (cb.level === 'medium' ? 'Sensacional' : 'Limpio')}</span>
        </div>
      </div>

      <p class="card-summary">${escapeHtml(art.resumen || "")}</p>

      <div class="card-footer">
        <div class="footer-actions">
          <button class="btn-action-icon btn-save ${isSaved ? 'active' : ''}" data-id="${art.id}">
            <span>${isSaved ? 'Guardado' : 'Guardar'}</span>
          </button>
          <button class="btn-dislike" data-id="${art.id}" title="Penalizar tema en el algoritmo">
            <span>✕ No me interesa</span>
          </button>
          <button class="btn-mute-topic" data-title="${escapeHtml(art.titulo)}" title="Ocultar término de todos los feeds">
            <span>🚫 Ocultar tema...</span>
          </button>
        </div>
        <button class="btn-primary btn-copy-prompt" data-id="${art.id}" data-title="${escapeHtml(art.titulo)}">
          <span>Copiar para Video</span>
        </button>
      </div>
    </div>
  `;
  return card;
}

function attachCardEvents(parent) {
  parent.querySelectorAll(".btn-save").forEach(btn => {
    btn.onclick = async () => {
      const id = btn.getAttribute("data-id");
      const res = await fetch(`/api/articles/${id}/toggle-save`, { method: "POST" });
      const d = await res.json();
      if (d.guardado) {
        const card = document.getElementById(`art-card-${id}`);
        if (card) {
          card.style.opacity = "0";
          setTimeout(() => card.remove(), 200);
        }
        showToast("Guardada en 'Ver más tarde' (ocultada del feed)");
      } else {
        btn.classList.remove("active");
        btn.querySelector("span").textContent = "Guardar";
        showToast("Quitada de guardados");
      }
      loadSavedCounter();
    };
  });

  parent.querySelectorAll(".btn-dislike").forEach(btn => {
    btn.onclick = async () => {
      const id = btn.getAttribute("data-id");
      await fetch(`/api/articles/${id}/dislike`, { method: "POST" });
      const card = document.getElementById(`art-card-${id}`);
      if (card) {
        card.style.opacity = "0";
        setTimeout(() => card.remove(), 200);
      }
      showToast("Tema penalizado en tu algoritmo (-3.5)");
    };
  });

  parent.querySelectorAll(".btn-mute-topic").forEach(btn => {
    btn.onclick = async () => {
      const rawTitle = btn.getAttribute("data-title") || "";
      const defaultWord = rawTitle.split(" ")[0].replace(/[^a-zA-Z0-9]/g, "");
      const topic = prompt("Ingresa el término exacto a ocultar de todos los feeds (ej: Nvidia, Jensen Huang, Trump, Fútbol):", defaultWord);
      if (topic && topic.trim()) {
        await fetch("/api/muted-topics", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ topic: topic.trim() })
        });
        showToast(`Tema '${topic.trim()}' ocultado de todos los feeds`);
        loadMutedTopics();
        resetAndReloadFeed();
      }
    };
  });

  parent.querySelectorAll(".btn-copy-prompt").forEach(btn => {
    const id = btn.getAttribute("data-id");
    btn.onclick = () => openDurationModal(id, btn.getAttribute("data-title"));
  });
}

// --- MODAL DE DURACIÓN CON CÁLCULO EN VIVO ---

function initDurationModal() {
  const m = document.getElementById("duration-modal");
  document.getElementById("btn-close-modal").onclick = () => m.classList.remove("active");
  document.getElementById("btn-cancel-modal").onclick = () => m.classList.remove("active");
  
  document.getElementById("duration-form").onsubmit = async (e) => {
    e.preventDefault();
    const dur = document.getElementById("duration-input-field").value.trim();
    const btnSubmit = document.getElementById("btn-submit-modal");
    const txtSubmit = document.getElementById("btn-submit-modal-text");

    btnSubmit.disabled = true;
    txtSubmit.textContent = "Extrayendo y copiando...";

    try {
      const res = await fetch(`/api/articles/${state.activeModalArticleId}/copy-prompt`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ duration: dur })
      });
      const d = await res.json();
      if (res.ok) {
        navigator.clipboard?.writeText(d.prompt);
        m.classList.remove("active");
        showToast("¡Prompt copiado al portapapeles con éxito!");
      } else {
        showToast(d.detail || "Error al procesar", true);
      }
    } catch(err) {
      showToast("Error de conexión", true);
    } finally {
      btnSubmit.disabled = false;
      txtSubmit.textContent = "Copiar al portapapeles";
    }
  };
}

async function openDurationModal(id, title) {
  state.activeModalArticleId = id;
  const m = document.getElementById("duration-modal");
  document.getElementById("modal-article-title").textContent = title;
  
  const hint = document.getElementById("modal-duration-hint");
  const input = document.getElementById("duration-input-field");
  const btnSubmit = document.getElementById("btn-submit-modal");

  if (state.durationCache[id]) {
    const cached = state.durationCache[id];
    input.value = cached.ideal_duration;
    hint.textContent = `Texto real extraído: ${cached.word_count} palabras. A 3.2 palabras/seg, duración calculada: ${cached.ideal_duration}.`;
    input.disabled = false;
    btnSubmit.disabled = false;
    m.classList.add("active");
    input.select();
    return;
  }

  input.disabled = true;
  btnSubmit.disabled = true;
  input.value = "Calculando...";
  hint.textContent = "Descargando cuerpo completo de la web y contando palabras reales...";
  m.classList.add("active");

  try {
    const res = await fetch(`/api/articles/${id}/ideal-duration`);
    const data = await res.json();
    if (res.ok && data.ideal_duration) {
      state.durationCache[id] = data;
      input.value = data.ideal_duration;
      hint.textContent = `Texto real extraído: ${data.word_count} palabras. A 3.2 palabras/seg, duración calculada: ${data.ideal_duration}.`;
    } else {
      input.value = "2:00";
      hint.textContent = "No se pudo extraer el cuerpo completo. Puedes ajustar la duración manualmente.";
    }
  } catch(e) {
    input.value = "2:00";
    hint.textContent = "Error al conectar. Ingresa los minutos deseados.";
  } finally {
    input.disabled = false;
    btnSubmit.disabled = false;
    input.select();
  }
}

// --- TEMAS OCULTOS ---

function initMutedTopicsModule() {
  const form = document.getElementById("add-muted-topic-form");
  const input = document.getElementById("muted-topic-input");
  form.onsubmit = async (e) => {
    e.preventDefault();
    const val = input.value.trim();
    if (!val) return;
    await fetch("/api/muted-topics", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic: val })
    });
    input.value = "";
    loadMutedTopics();
    resetAndReloadFeed();
    showToast(`Tema '${val}' ocultado`);
  };
}

async function loadMutedTopics() {
  const container = document.getElementById("muted-topics-list");
  const counter = document.getElementById("muted-counter");
  if (!container) return;

  try {
    const res = await fetch("/api/muted-topics");
    const d = await res.json();
    const topics = d.topics || [];
    if (counter) counter.textContent = topics.length;
    container.innerHTML = "";

    if (topics.length === 0) {
      container.innerHTML = "<p style='color: var(--accent-muted); font-size: 0.85rem;'>No tienes ningún tema oculto activo.</p>";
      return;
    }

    topics.forEach(t => {
      const item = document.createElement("div");
      item.className = "muted-tag-item";
      item.innerHTML = `
        <span style="font-weight: 700; color: #fff;">🚫 ${escapeHtml(t.topic)}</span>
        <button class="btn-action-icon" data-id="${t.id}" style="color: var(--danger); border-color: rgba(239, 68, 68, 0.4) !important;">Desbloquear</button>
      `;
      item.querySelector("button").onclick = async () => {
        await fetch(`/api/muted-topics/${t.id}`, { method: "DELETE" });
        loadMutedTopics();
        resetAndReloadFeed();
        showToast(`Tema '${t.topic}' reactivado`);
      };
      container.appendChild(item);
    });
  } catch(e) {}
}

// --- GUARDADOS ---

async function loadSavedCounter() {
  try {
    const res = await fetch("/api/articles/saved");
    const d = await res.json();
    document.getElementById("saved-counter").textContent = d.count || 0;
  } catch(e) {}
}

async function loadSavedArticles() {
  const container = document.getElementById("saved-container");
  const empty = document.getElementById("saved-empty-msg");
  container.innerHTML = "";
  const res = await fetch("/api/articles/saved");
  const d = await res.json();
  const arts = d.articles || [];
  empty.style.display = arts.length === 0 ? "block" : "none";
  arts.forEach(a => container.appendChild(createArticleCard(a)));
  attachCardEvents(container);
}

function initSavedModule() {}

// --- MUESTRA DE VOZ ---

function initVoicePreview() {
  const btn = document.getElementById("btn-preview-voice");
  const btnText = document.getElementById("btn-preview-voice-text");
  const statusSpan = document.getElementById("preview-audio-status");

  btn.onclick = async () => {
    const voice = document.getElementById("tts-voice-select").value;
    if (!voice) return showToast("Elige una voz primero", true);

    if (state.sampleAudio) { state.sampleAudio.pause(); state.sampleAudio = null; }

    btn.disabled = true;
    btnText.textContent = "Sintetizando con Rules.txt...";
    statusSpan.textContent = "Aplicando directivas...";

    try {
      const params = new URLSearchParams({
        voice: voice,
        engine: state.selectedEngine,
        style: document.getElementById("cfg-qwen-style").value,
        speed_rate: document.getElementById("cfg-tts-speed").value,
        _t: Date.now()
      });

      state.sampleAudio = new Audio(`/api/tts/preview?${params.toString()}`);
      state.sampleAudio.onplay = () => { btnText.textContent = "Reproduciendo..."; statusSpan.textContent = "Sonando"; };
      state.sampleAudio.onended = () => { btn.disabled = false; btnText.textContent = "Escuchar Muestra con Rules.txt"; statusSpan.textContent = ""; };
      state.sampleAudio.onerror = () => { btn.disabled = false; btnText.textContent = "Escuchar Muestra con Rules.txt"; showToast("Error al reproducir audio", true); };
      await state.sampleAudio.play();
    } catch(err) {
      btn.disabled = false;
      btnText.textContent = "Escuchar Muestra con Rules.txt";
      showToast("Fallo al conectar con el motor de voz", true);
    }
  };
}

// --- AJUSTES Y SUBTÍTULOS TIKTOK ---

function initSettingsModule() {
  loadSettings();

  const colorPicker = document.getElementById("cfg-sub-highlight-picker");
  const colorSelect = document.getElementById("cfg-sub-highlight-select");

  if (colorPicker && colorSelect) {
    colorPicker.oninput = (e) => { colorSelect.value = e.target.value.toUpperCase(); };
    colorSelect.onchange = (e) => { colorPicker.value = e.target.value; };
  }

  document.getElementById("cfg-tts-engine").onchange = (e) => {
    state.selectedEngine = e.target.value;
    loadTtsVoices(e.target.value);
  };

  document.getElementById("btn-save-settings").onclick = async () => {
    const payload = {
      output_dir: document.getElementById("cfg-output-dir").value.trim(),
      music_dir: document.getElementById("cfg-music-dir").value.trim(),
      tts_engine: document.getElementById("cfg-tts-engine").value,
      default_tts_voice: document.getElementById("tts-voice-select").value,
      tts_speed: document.getElementById("cfg-tts-speed").value,
      qwen_style: document.getElementById("cfg-qwen-style").value,
      video_mode: document.getElementById("cfg-video-mode").value,
      video_quality: document.getElementById("cfg-video-quality").value,
      enable_tiktok_subtitles: document.getElementById("cfg-enable-tiktok-subs").checked,
      subtitle_highlight_color: document.getElementById("cfg-sub-highlight-picker").value,
      loop_clips_count: parseInt(document.getElementById("cfg-loop-clips-count").value) || 15,
      exploration_personal: parseInt(document.getElementById("cfg-exp-personal").value) || 70,
      exploration_related: parseInt(document.getElementById("cfg-exp-related").value) || 20,
      exploration_discovery: parseInt(document.getElementById("cfg-exp-discovery").value) || 10,
      enable_youtube: document.getElementById("cfg-enable-yt").checked,
      enable_wikimedia: document.getElementById("cfg-enable-wm").checked,
      enable_pixabay: document.getElementById("cfg-enable-pxb").checked,
      enable_pexels: document.getElementById("cfg-enable-pxl").checked,
      pixabay_api_key: document.getElementById("cfg-pixabay-key").value.trim(),
      pexels_api_key: document.getElementById("cfg-pexels-key").value.trim()
    };
    await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    showToast("Ajustes y configuración de subtítulos guardados");
  };
}

async function loadSettings() {
  const res = await fetch("/api/settings");
  const c = await res.json();
  document.getElementById("cfg-output-dir").value = c.output_dir || "output";
  document.getElementById("cfg-music-dir").value = c.music_dir || "music";
  document.getElementById("cfg-tts-engine").value = c.tts_engine || "qwen";
  document.getElementById("cfg-tts-speed").value = c.tts_speed || "+8%";
  document.getElementById("cfg-qwen-style").value = c.qwen_style || "youtube_energetic";
  document.getElementById("cfg-video-mode").value = c.video_mode || "loop";
  document.getElementById("cfg-video-quality").value = c.video_quality || "720p";
  document.getElementById("cfg-enable-tiktok-subs").checked = c.enable_tiktok_subtitles !== false;
  
  const subColor = c.subtitle_highlight_color || "#FFE600";
  document.getElementById("cfg-sub-highlight-picker").value = subColor;
  document.getElementById("cfg-sub-highlight-select").value = subColor.toUpperCase();
  document.getElementById("cfg-loop-clips-count").value = c.loop_clips_count || 15;

  document.getElementById("cfg-exp-personal").value = c.exploration_personal || 70;
  document.getElementById("cfg-exp-related").value = c.exploration_related || 20;
  document.getElementById("cfg-exp-discovery").value = c.exploration_discovery || 10;
  document.getElementById("cfg-enable-yt").checked = c.enable_youtube !== false;
  document.getElementById("cfg-enable-wm").checked = c.enable_wikimedia !== false;
  document.getElementById("cfg-enable-pxb").checked = !!c.enable_pixabay;
  document.getElementById("cfg-enable-pxl").checked = !!c.enable_pexels;
  document.getElementById("cfg-pixabay-key").value = c.pixabay_api_key || "";
  document.getElementById("cfg-pexels-key").value = c.pexels_api_key || "";

  state.savedVoice = c.default_tts_voice || "Alex";
  state.selectedEngine = c.tts_engine || "qwen";
  await loadTtsVoices(state.selectedEngine);
}

async function loadTtsVoices(eng) {
  const res = await fetch(`/api/tts/voices?engine=${eng}`);
  const d = await res.json();
  const sel = document.getElementById("tts-voice-select");
  sel.innerHTML = "";
  (d.voices || []).forEach(v => {
    const o = document.createElement("option");
    o.value = v.short_name;
    o.textContent = v.friendly_name;
    if (v.short_name === state.savedVoice) o.selected = true;
    sel.appendChild(o);
  });
}

function initGenerateModule() {
  const btn = document.getElementById("btn-start-generate");
  btn.onclick = async () => {
    const srt = document.getElementById("srt-input").value.trim();
    const kws = document.getElementById("keywords-input").value.split("\n").map(k => k.trim()).filter(Boolean);
    if (!srt) return showToast("Ingresa un guion SRT", true);

    btn.disabled = true;
    try {
      const res = await fetch("/api/generate", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ srt, keywords: kws, voice: document.getElementById("tts-voice-select").value, tts_engine: state.selectedEngine })
      });
      if (res.ok) {
        showToast("Generación iniciada con subtítulos y GPU");
        document.getElementById("render-progress-card").style.display = "block";
        pollProgress();
      } else { btn.disabled = false; }
    } catch(e) { btn.disabled = false; }
  };
}

function pollProgress() {
  const interval = setInterval(async () => {
    const res = await fetch("/api/generation/progress");
    const d = await res.json();
    document.getElementById("render-progress-fill").style.width = `${d.progress}%`;
    document.getElementById("render-status-percent").textContent = `${d.progress}%`;
    document.getElementById("render-status-detail").textContent = d.detail || "";

    ["tts", "clips", "render", "done"].forEach((s, idx) => {
      const el = document.getElementById(`stage-${s}`);
      el.classList.toggle("done", d.progress >= (idx + 1) * 25 || d.status === "finished");
      el.classList.toggle("active", d.stage === s);
    });

    if (d.status === "finished") {
      clearInterval(interval);
      document.getElementById("btn-start-generate").disabled = false;
      document.getElementById("final-video-player").src = d.video_path;
      document.getElementById("video-preview-box").style.display = "block";
      showToast("¡Video finalizado con subtítulos animados!");
    } else if (d.status === "error") {
      clearInterval(interval);
      document.getElementById("btn-start-generate").disabled = false;
      showToast(`Error: ${d.error_message}`, true);
    }
  }, 500);
}

async function loadSourcesDropdown() {
  const sel = document.getElementById("feed-source-filter");
  const res = await fetch("/api/sources");
  const d = await res.json();
  sel.innerHTML = '<option value="">Todas las fuentes</option>';
  (d.sources || []).forEach(s => {
    const o = document.createElement("option");
    o.value = s.id; o.textContent = s.nombre;
    sel.appendChild(o);
  });
}

function formatTimeAgo(str) {
  if (!str) return "reciente";
  const sec = Math.floor((new Date() - new Date(str.replace(" ", "T") + "Z")) / 1000);
  if (sec < 3600) return `hace ${Math.max(1, Math.floor(sec/60))}m`;
  if (sec < 86400) return `hace ${Math.floor(sec/3600)}h`;
  return `hace ${Math.floor(sec/86400)}d`;
}

function escapeHtml(t) { return (t || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }