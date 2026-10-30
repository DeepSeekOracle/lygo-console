(function () {
  const CHAMPS = {
    LYRA: "Memory, song, continuity of theme. Observed / Inferred / Unknown.",
    "Δ9RA": "Boundary vigilance. List risks and what would break first.",
    "ΣRΛΘ": "Shadow sentinel. What is omitted or failing silently?",
    ARKOS: "Ethical Reality Architect. Intent, constraints, blueprint, failure, receipts.",
    KAIROS: "Timing. Ordered steps with why this order.",
    "ÆTHERIS": "Claim vs evidence. RESOURCE vs CANON.",
    "ΣCENΔR": "At least two live scenarios and what would falsify each.",
    SANCORA: "Shared vocabulary and handoff. Not medical advice.",
    SEPHRAEL: "Echoes, fragile items, archive vs discard. No secrets in notes.",
    "OMNIΣIREN": "Constraints only, then the next irreversible-safe step.",
    Lightfather: "Provenance and consent. Never claim to be the human operator.",
    "VΩLARIS": "Criteria, scores, tradeoffs, recommendation.",
    "ZETAΔ9": "Weird inputs and boundary tests. Do not break production.",
    JUSTICAE: "Affected parties, disclosure, consent. No doxxing.",
    "ΣEIDŌN": "Surface vs depth. Witness, do not flatten a long project.",
  };
  const P0 = /format\s+c:|\bdiskpart\b|\bbcdedit\b|rm\s+-rf\s+\/|invoke-expression/i;
  const PROVIDERS = {
    groq: { label: "Groq (free, no card)", kind: "openai", url: "https://api.groq.com/openai/v1/chat/completions", model: "openai/gpt-oss-20b", models: ["openai/gpt-oss-20b", "groq/compound-mini", "groq/compound", "openai/gpt-oss-120b", "qwen/qwen3.8-27b"], key: true, help: "console.groq.com/keys — paste key, Connect. Model is chosen for you." },
    gemini: { label: "Google Gemini (free, no card)", kind: "openai", url: "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", model: "gemini-2.0-flash", key: true, help: "aistudio.google.com/apikey" },
    openrouter: { label: "OpenRouter (many :free models)", kind: "openai", url: "https://openrouter.ai/api/v1/chat/completions", model: "openrouter/auto", key: true, help: "openrouter.ai/keys — use model ids ending :free", extra: { "HTTP-Referer": "https://chatagent.ca/portal/", "X-Title": "LYGO API Portal" } },
    cerebras: { label: "Cerebras (fast, free/trial)", kind: "openai", url: "https://api.cerebras.ai/v1/chat/completions", model: "llama3.1-8b", key: true, help: "cloud.cerebras.ai" },
    huggingface: { label: "Hugging Face router", kind: "openai", url: "https://router.huggingface.co/v1/chat/completions", model: "Qwen/Qwen2.5-1.5B-Instruct", key: true, help: "huggingface.co/settings/tokens" },
    mistral: { label: "Mistral (free / cheap)", kind: "openai", url: "https://api.mistral.ai/v1/chat/completions", model: "mistral-small-latest", key: true, help: "console.mistral.ai" },
    nvidia: { label: "NVIDIA NIM (free catalog)", kind: "openai", url: "https://integrate.api.nvidia.com/v1/chat/completions", model: "meta/llama-3.1-8b-instruct", key: true, help: "build.nvidia.com" },
    cohere: { label: "Cohere (trial)", kind: "openai", url: "https://api.cohere.ai/compatibility/v1/chat/completions", model: "command-r-plus", key: true, help: "dashboard.cohere.com/api-keys" },
    sambanova: { label: "SambaNova Cloud", kind: "openai", url: "https://api.sambanova.ai/v1/chat/completions", model: "Meta-Llama-3.1-8B-Instruct", key: true, help: "cloud.sambanova.ai" },
    llm7: { label: "LLM7.io (free)", kind: "openai", url: "https://api.llm7.io/v1/chat/completions", model: "gpt-4o-mini-2024-07-18", key: true, help: "llm7.io — key optional on some models" },
    zai: { label: "Z.ai / Zhipu GLM", kind: "openai", url: "https://api.z.ai/api/paas/v4/chat/completions", model: "glm-4.5-flash", key: true, help: "z.ai / open.bigmodel.cn" },
    deepinfra: { label: "DeepInfra", kind: "openai", url: "https://api.deepinfra.com/v1/openai/chat/completions", model: "meta-llama/Meta-Llama-3.1-8B-Instruct", key: true, help: "deepinfra.com" },
    hyperbolic: { label: "Hyperbolic", kind: "openai", url: "https://api.hyperbolic.xyz/v1/chat/completions", model: "meta-llama/Meta-Llama-3.1-8B-Instruct", key: true, help: "app.hyperbolic.xyz" },
    novita: { label: "Novita", kind: "openai", url: "https://api.novita.ai/v3/openai/chat/completions", model: "meta-llama/llama-3.1-8b-instruct", key: true, help: "novita.ai" },
    siliconflow: { label: "SiliconFlow", kind: "openai", url: "https://api.siliconflow.cn/v1/chat/completions", model: "Qwen/Qwen2.5-7B-Instruct", key: true, help: "siliconflow.cn / siliconflow.com" },
    openai: { label: "OpenAI (paid)", kind: "openai", url: "https://api.openai.com/v1/chat/completions", model: "gpt-4o-mini", key: true, help: "platform.openai.com/api-keys" },
    xai: { label: "xAI Grok (credits)", kind: "openai", url: "https://api.x.ai/v1/chat/completions", model: "grok-4-fast-non-reasoning", key: true, help: "console.x.ai" },
    deepseek: { label: "DeepSeek V4.1 Flash (token deal)", kind: "openai", url: "https://api.deepseek.com/v1/chat/completions", model: "deepseek-flash", key: true, help: "platform.deepseek.com — model deepseek-flash · off-peak $0.15/$0.60 per 1M · cache-hit $0.003 · 1M context" },
    together: { label: "Together AI", kind: "openai", url: "https://api.together.xyz/v1/chat/completions", model: "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo", key: true, help: "api.together.xyz" },
    fireworks: { label: "Fireworks", kind: "openai", url: "https://api.fireworks.ai/inference/v1/chat/completions", model: "accounts/fireworks/models/llama-v3p1-8b-instruct", key: true, help: "fireworks.ai" },
    anthropic: { label: "Anthropic Claude (paid)", kind: "anthropic", url: "https://api.anthropic.com/v1/messages", model: "claude-3-5-haiku-latest", key: true, help: "console.anthropic.com" },
    perplexity: { label: "Perplexity", kind: "openai", url: "https://api.perplexity.ai/chat/completions", model: "sonar", key: true, help: "perplexity.ai/settings/api" },
    github: { label: "GitHub Models (PAT)", kind: "openai", url: "https://models.inference.ai.azure.com/chat/completions", model: "gpt-4o-mini", key: true, help: "github.com/settings/tokens — GitHub Models may be limited" },
    custom: { label: "Any OpenAI-compatible URL", kind: "openai", url: "", model: "", key: true, help: "Paste base (we append /v1/chat/completions) or full chat URL" },
  };
  const AGENT_TOOLS = window.LYGO_AGENT_TOOLS || [];
  const AGENT_TOOLS_CORE = window.LYGO_AGENT_TOOLS_CORE || AGENT_TOOLS;

  const log = document.getElementById("log");
  const healthEl = document.getElementById("health");
  const limb = document.getElementById("limb-out");
  const msg = document.getElementById("msg");
  const modeEl = document.getElementById("mode");
  const endpointEl = document.getElementById("endpoint");
  const tokenEl = document.getElementById("hf-token");
  const modelEl = document.getElementById("model");
  let history = [];
  let connected = false;
  let SKILLS = [];
  let ENABLED = {};
  let DOCS = { soul: "", identity: "", memory: "" };

  function bubble(role, text) {
    const d = document.createElement("div");
    d.className = "bubble " + role;
    d.textContent = text;
    log.appendChild(d);
    log.scrollTop = log.scrollHeight;
  }
  function setHealth(t) {
    if (healthEl) healthEl.textContent = t;
  }
  async function saveToDisk(filename, text) {
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    if (window.showSaveFilePicker) {
      try {
        const h = await window.showSaveFilePicker({
          suggestedName: filename,
          types: [{ description: "Text", accept: { "text/plain": [".txt", ".md", ".json"] } }],
        });
        const w = await h.createWritable();
        await w.write(blob);
        await w.close();
        return { ok: true, method: "picker", name: filename };
      } catch (e) {
        if (e && e.name === "AbortError") return { ok: false, error: "cancelled" };
      }
    }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
    return { ok: true, method: "download", name: filename };
  }
  function sessionPayload() {
    return JSON.stringify({ v: 1, saved: new Date().toISOString(), provider: modeEl && modeEl.value, model: modelEl && modelEl.value, history: history, notepad: (document.getElementById("np-body") || {}).value || "" }, null, 2);
  }
  function provider() {
    return PROVIDERS[modeEl.value] || PROVIDERS.groq;
  }

  function latin1(s) {
    return String(s == null ? "" : s).replace(/[^\x00-\xFF]/g, "");
  }
  function readKey() {
    let k = (tokenEl && tokenEl.value) || "";
    k = k.replace(/^\uFEFF/, "");
    k = k.replace(/[\u200B-\u200D\u2060\uFEFF\u00A0]/g, "");
    k = k.replace(/[\u2018\u2019\u201C\u201D]/g, "");
    k = k.replace(/^Bearer\s+/i, "").trim();
    k = k.replace(/^["'`]+|["'`]+$/g, "");
    k = k.replace(/\s+/g, "");
    k = k.replace(/[^\x21-\x7E]/g, "");
    if (tokenEl && k && tokenEl.value !== k) tokenEl.value = k;
    return k;
  }
  function safeHeaders(obj) {
    const out = {};
    Object.keys(obj || {}).forEach(function (k) {
      const key = String(k).replace(/[^\x21-\x7E]/g, "");
      if (!key) return;
      out[key] = latin1(obj[k]);
    });
    return out;
  }

  function fillModelOptions(ids, selected) {
    if (!modelEl) return selected || "";
    const want = selected || (modelEl.value) || (provider().model) || "";
    const list = [];
    (ids || []).forEach(function (id) {
      if (id && list.indexOf(id) < 0) list.push(id);
    });
    if (want && list.indexOf(want) < 0) list.unshift(want);
    if (!list.length) list.push("openai/gpt-oss-20b");
    modelEl.innerHTML = "";
    list.forEach(function (id) {
      const o = document.createElement("option");
      o.value = id;
      o.textContent = id;
      modelEl.appendChild(o);
    });
    modelEl.value = list.indexOf(want) >= 0 ? want : list[0];
    return modelEl.value;
  }

  function fillProvider() {
    const p = provider();
    fillModelOptions(p.models || (p.model ? [p.model] : []), p.model);
    remapDeadModel();
    if (endpointEl) {
      endpointEl.value = p.url || "";
      const custom = modeEl && modeEl.value === "custom";
      endpointEl.hidden = !custom;
      if (custom) endpointEl.removeAttribute("hidden");
    }
    if (tokenEl) {
      tokenEl.style.display = p.key ? "" : "none";
      tokenEl.placeholder = "Paste API key — stays in this browser";
    }
    const help = document.getElementById("mode-help");
    if (help) {
      help.innerHTML =
        (p.help || "Paste a key → Connect. Model is chosen for you.") +
        ' · <a href="/guides/how-to-lygo-llm-portal.html">How to connect</a>';
    }
    setHealth("API portal · " + p.label + (connected ? " · connected" : " · paste key → Connect"));
  }

  function systemPrompt(invoked) {
    let s =
      "You are a LYGO-aligned agent in the public API portal at https://chatagent.ca/portal/. " +
      "The human is the publisher. Assist; never replace them. Dual ledgers / Haven Star Chart = CANON. This chat = RESOURCE. " +
      "P0: no OS wipe, no secrets in notes, no fabricated receipts. " +
      "You have real browser limbs (call them; do not describe calling): wiki_search, wiki_summary, web_search, fetch_page, web_fetch, http_json, weather, geocode, world_pulse, now, calc, hash_text, base64, uuid, json_pretty, champion, skill_list, skill_read, skill_enable, skill_disable, hn_search, arxiv_search, github_search, github_repo, wayback, clawhub_search, skillhub_list, hf_search, npm_search, pypi_search, so_search, define, currency, crypto_price, quake, eonet, iss, book_search, pubmed, lattice_handshake, site_card, whoami, kernel_status, soul_read, identity_read, memory_read, remember, memory_recall, notepad_read, notepad_write, todo_add, todo_list, p0_gate, geolocate (asks permission), clipboard_write/read (asks permission). Skills are already on this page — toggle on/off. Never tell the human to install or download a skill for this portal. " +
      "If they want disk/skills/local GGUF, send them to https://chatagent.ca/lygoskillhub.html#lygo-llm-kernel and https://chatagent.ca/lygo-llm-console.html — this page is API-only. " +
      "Never invent github.com/user/repo. Real org https://github.com/DeepSeekOracle · HF https://huggingface.co/DeepSeekOracle.";
    if (invoked) s += " Champion lens: " + invoked + ". Observed / Inferred / Unknown.";
    s += "\n\n=== SOUL.md ===\n" + (DOCS.soul || "").slice(0, 2500);
    s += "\n\n=== IDENTITY.md ===\n" + (DOCS.identity || "").slice(0, 1500);
    s += "\n\n=== MEMORY.md ===\n" + (DOCS.memory || "").slice(0, 1500);
    const on = SKILLS.filter(function (x) { return ENABLED[x.slug] !== false; });
    s += "\n\n=== ENABLED SKILLS (already installed on this portal) ===\n";
    on.forEach(function (x) {
      s += "- " + x.slug + " (" + x.name + "): " + (x.when || "") + "\n";
    });
    s += "Call skill_read for full text. Disabled skills must not be used.\n";
    return s.slice(0, 14000);
  }

  async function runTool(name, args) {
    args = args || {};
    try {
      if (typeof window.lygoRunPortalTool === "function") {
        return await window.lygoRunPortalTool(name, args, {
          skills: SKILLS,
          enabled: ENABLED,
          docs: DOCS,
          champs: CHAMPS,
          p0: P0,
          persistEnabled: persistEnabled,
          paintSkills: paintSkills,
          connected: connected,
          provider: modeEl && modeEl.value,
        });
      }
    } catch (e) {
      return { ok: false, error: String(e) };
    }
    return { ok: false, error: "unknown_tool" };
  }

  function openaiUrl() {
    const p = provider();
    let e = (endpointEl.value || p.url || "").trim().replace(/\/$/, "");
    if (!e) return "";
    if (/\/chat\/completions$/i.test(e) || /\/messages$/i.test(e) || /\/paas\/v4\/chat\/completions$/i.test(e)) return e;
    if (/\/openai\/v1$/i.test(e) || /\/v1$/i.test(e) || /\/v1beta\/openai$/i.test(e) || /\/compatibility\/v1$/i.test(e)) return e + "/chat/completions";
    if (/\/v1\//i.test(e) && /chat/i.test(e)) return e;
    return e + "/v1/chat/completions";
  }

  const DEAD_MODELS = {
    "llama-3.1-8b-instant": "openai/gpt-oss-20b",
    "llama-3.3-70b-versatile": "openai/gpt-oss-120b",
    "llama3.1-8b": "llama-3.3-70b",
  };
  const MODEL_PREFER = {
    groq: ["openai/gpt-oss-20b", "groq/compound-mini", "groq/compound", "openai/gpt-oss-120b", "qwen/qwen3.8-27b"],
  };

  function modelsUrl() {
    const chat = openaiUrl();
    if (!chat) return "";
    return chat.replace(/\/chat\/completions$/i, "/models").replace(/\/messages$/i, "/models");
  }
  function isChatModel(id) {
    const s = String(id || "").toLowerCase();
    if (!s) return false;
    if (/whisper|tts|orpheus|guard|embed|moderation|prompt-guard/.test(s)) return false;
    return true;
  }
  function remapDeadModel() {
    if (!modelEl) return "";
    const cur = (modelEl.value || "").trim();
    const next = DEAD_MODELS[cur];
    if (next) {
      fillModelOptions((provider().models || []).concat([next, cur]), next);
      return next;
    }
    return cur;
  }
  async function listProviderModels() {
    const u = modelsUrl();
    const key = readKey();
    if (!u || !key) return [];
    try {
      const headers = safeHeaders({ Authorization: "Bearer " + key });
      if (modeEl.value === "github") headers["api-key"] = key;
      const r = await fetch(u, { headers: headers });
      const j = await r.json().catch(function () { return {}; });
      return (j.data || j.models || []).map(function (m) { return m.id || m.name; }).filter(Boolean);
    } catch (_) {
      return [];
    }
  }
  async function pickLiveModel() {
    remapDeadModel();
    const current = (modelEl && modelEl.value) || "";
    const ids = (await listProviderModels()).filter(isChatModel);
    if (!ids.length) {
      const forced = remapDeadModel() || current || "openai/gpt-oss-20b";
      fillModelOptions((provider().models || []).concat([forced]), forced);
      return forced;
    }
    const prefer = (MODEL_PREFER[modeEl.value] || []).concat([current], ids);
    let pick = "";
    for (let i = 0; i < prefer.length; i++) {
      if (ids.indexOf(prefer[i]) >= 0) {
        pick = prefer[i];
        break;
      }
    }
    if (!pick) pick = ids[0];
    fillModelOptions(prefer.concat(ids), pick);
    return pick;
  }

  async function callApi(messages) {
    const p = provider();
    const url = openaiUrl();
    remapDeadModel();
    const model = modelEl.value || p.model;
    const key = readKey();
    const headers = { "Content-Type": "application/json" };
    if (key) {
      if (p.kind === "anthropic") {
        headers["x-api-key"] = key;
        headers["anthropic-version"] = "2023-06-01";
      } else {
        headers.Authorization = "Bearer " + key;
        if (modeEl.value === "github") headers["api-key"] = key;
      }
    }
    if (p.extra) Object.keys(p.extra).forEach(function (k) { headers[k] = p.extra[k]; });
    const hdrs = safeHeaders(headers);
    let payload;
    if (p.kind === "anthropic") {
      payload = { model: model, max_tokens: 1024, system: messages[0] && messages[0].content, messages: messages.filter(function (m) { return m.role !== "system"; }) };
    } else {
      payload = { model: model, messages: messages, max_tokens: 1024, stream: false, tools: AGENT_TOOLS };
    }
    let r = await fetch(url, { method: "POST", headers: hdrs, body: JSON.stringify(payload) });
    let j = await r.json().catch(function () { return {}; });
    if (!r.ok && payload.tools && (r.status === 400 || r.status === 404 || r.status === 422) && AGENT_TOOLS_CORE.length && payload.tools.length > AGENT_TOOLS_CORE.length) {
      payload.tools = AGENT_TOOLS_CORE;
      r = await fetch(url, { method: "POST", headers: hdrs, body: JSON.stringify(payload) });
      j = await r.json().catch(function () { return {}; });
    }
    if (!r.ok && payload.tools && (r.status === 400 || r.status === 404 || r.status === 422)) {
      delete payload.tools;
      r = await fetch(url, { method: "POST", headers: hdrs, body: JSON.stringify(payload) });
      j = await r.json().catch(function () { return {}; });
    }
    if (!r.ok) {
      const err = (j.error && (j.error.message || JSON.stringify(j.error))) || j.detail || ("http " + r.status);
      const msg = typeof err === "string" ? err : JSON.stringify(err);
      if (/does not exist|do not have access|model_not_found|invalid_model/i.test(msg)) {
        const live = await pickLiveModel();
        if (live && live !== model) {
          payload.model = live;
          r = await fetch(url, { method: "POST", headers: hdrs, body: JSON.stringify(payload) });
          j = await r.json().catch(function () { return {}; });
          if (r.ok) return j;
        }
      }
      throw new Error(msg);
    }
    return j;
  }

  function extractMessage(j) {
    const msg = ((j.choices || [])[0] || {}).message || {};
    const text = msg.content || (((j.content || [])[0] || {}).text) || "";
    return { text: typeof text === "string" ? text : JSON.stringify(text), tool_calls: msg.tool_calls || [] };
  }

  document.getElementById("connect").onclick = async function () {
    fillProvider();
    const p = provider();
    if (p.key && !readKey() && modeEl.value !== "llm7" && modeEl.value !== "custom") {
      setHealth("paste your API key (this tab only) — " + p.help);
      bubble("assistant", "Paste a Groq/OpenAI/Grok/… key, then Connect. Keys stay in this tab.");
      return;
    }
    if (!openaiUrl()) {
      setHealth("paste an endpoint URL");
      return;
    }
    setHealth("checking models…");
    const live = await pickLiveModel();
    connected = true;
    try { sessionStorage.setItem("lygo_portal_provider", modeEl.value); } catch (_) {}
    setHealth("connected · " + p.label + " · " + (live || p.model));
    bubble("assistant", "Connected to " + p.label + " · model " + (live || p.model) + ". Send a message.");
  };
  if (tokenEl) {
    tokenEl.addEventListener("paste", function () { setTimeout(readKey, 0); });
    tokenEl.addEventListener("blur", readKey);
    tokenEl.addEventListener("change", readKey);
  }

  if (modeEl) {
    modeEl.innerHTML = "";
    Object.keys(PROVIDERS).forEach(function (id) {
      const o = document.createElement("option");
      o.value = id;
      o.textContent = PROVIDERS[id].label;
      modeEl.appendChild(o);
    });
    try {
      const saved = sessionStorage.getItem("lygo_portal_provider");
      modeEl.value = saved && PROVIDERS[saved] ? saved : "groq";
    } catch (_) {
      modeEl.value = "groq";
    }
    modeEl.onchange = fillProvider;
    fillProvider();
  }
  document.querySelectorAll("[data-fill]").forEach(function (a) {
    a.addEventListener("click", function (ev) {
      const id = a.getAttribute("data-fill");
      if (!id || !PROVIDERS[id] || !modeEl) return;
      ev.preventDefault();
      modeEl.value = id;
      fillProvider();
      if (tokenEl && PROVIDERS[id].key) tokenEl.focus();
    });
  });

  function persistEnabled() {
    try { localStorage.setItem("lygo_portal_enabled", JSON.stringify(ENABLED)); } catch (_) {}
  }
  function paintSkills() {
    const box = document.getElementById("skills-list");
    if (!box) return;
    box.innerHTML = "";
    SKILLS.forEach(function (s) {
      const row = document.createElement("label");
      row.className = "skill-row";
      const ck = document.createElement("input");
      ck.type = "checkbox";
      ck.checked = ENABLED[s.slug] !== false;
      ck.onchange = function () {
        ENABLED[s.slug] = ck.checked;
        persistEnabled();
      };
      const name = document.createElement("span");
      name.className = "sn";
      name.textContent = (s.slug.indexOf("champion-") === 0 ? "★ " : "") + (s.name || s.slug);
      name.title = s.when || "";
      name.onclick = function (ev) {
        ev.preventDefault();
        msg.value = "Invoke skill " + s.slug + " — " + (s.when || s.name);
        msg.focus();
      };
      row.appendChild(ck);
      row.appendChild(name);
      box.appendChild(row);
    });
    const st = document.getElementById("skill-pack");
    const nOn = SKILLS.filter(function (s) { return ENABLED[s.slug] !== false; }).length;
    if (st) st.textContent = nOn + "/" + SKILLS.length + " on · already on this page · no download";
  }
  document.querySelectorAll("[data-cont]").forEach(function (btn) {
    btn.onclick = function () {
      document.querySelectorAll("[data-cont]").forEach(function (b) { b.classList.remove("on"); });
      btn.classList.add("on");
      const id = btn.getAttribute("data-cont");
      ["soul", "id", "mem"].forEach(function (k) {
        const p = document.getElementById("pane-" + k);
        if (p) p.hidden = k !== id;
      });
    };
  });
  function bindDoc(id, key) {
    const el = document.getElementById(id);
    if (!el) return;
    el.value = DOCS[key] || "";
    el.onchange = function () {
      DOCS[key] = el.value;
      try { localStorage.setItem("lygo_portal_" + key, el.value); } catch (_) {}
    };
  }
  async function loadDocs() {
    async function get(path, key) {
      try {
        const t = await (await fetch(path, { cache: "no-store" })).text();
        const over = localStorage.getItem("lygo_portal_" + key);
        DOCS[key] = over != null ? over : t;
      } catch (_) {}
    }
    await get("SOUL.md", "soul");
    await get("IDENTITY.md", "identity");
    await get("MEMORY.md", "memory");
    bindDoc("soul-edit", "soul");
    bindDoc("id-edit", "identity");
    bindDoc("mem-edit", "memory");
  }
  const rst = document.getElementById("id-reset");
  if (rst) {
    rst.onclick = function () {
      ["soul", "identity", "memory"].forEach(function (k) { try { localStorage.removeItem("lygo_portal_" + k); } catch (_) {} });
      loadDocs();
    };
  }
  loadDocs();

  document.querySelectorAll("[data-limb]").forEach(function (b) {
    b.onclick = async function () {
      const n = b.getAttribute("data-limb");
      if (n === "skillhub") window.open("https://chatagent.ca/lygoskillhub.html#lygo-llm-kernel", "_blank", "noopener");
      else if (n === "howto") window.open("/guides/how-to-lygo-llm-portal.html", "_blank", "noopener");
      else if (n === "kit") window.open("https://chatagent.ca/lygo-llm-console.html", "_blank", "noopener");
      else {
        const map = { wiki: "wiki_search", weather: "weather", now: "now", hn: "hn_search", github: "github_search", handshake: "lattice_handshake", whoami: "whoami", iss: "iss", quake: "quake" };
        const tool = map[n] || n;
        const args = tool.indexOf("search") >= 0 ? { q: (msg && msg.value) || "LYGO" } : {};
        const r = await runTool(tool, args);
        limb.textContent = JSON.stringify(r, null, 2);
        bubble("assistant", tool + ":\n" + (typeof r.text === "string" ? r.text : JSON.stringify(r, null, 2)).slice(0, 2000));
      }
    };
  });

  const npBody = document.getElementById("np-body");
  const npSt = document.getElementById("np-status");
  if (npBody) {
    try { npBody.value = localStorage.getItem("lygo_public_notepad") || ""; } catch (_) {}
  }
  const npSave = document.getElementById("np-save");
  if (npSave) {
    npSave.onclick = function () {
      try {
        localStorage.setItem("lygo_public_notepad", npBody.value || "");
        npSt.textContent = "saved in this browser";
      } catch (_) { npSt.textContent = "blocked"; }
    };
  }
  const npDisk = document.getElementById("np-disk");
  if (npDisk) {
    npDisk.onclick = async function () {
      const r = await saveToDisk("lygo-notepad-" + Date.now() + ".md", (npBody && npBody.value) || "");
      if (npSt) npSt.textContent = r.ok ? ("saved " + r.method) : (r.error || "fail");
    };
  }
  const sessSave = document.getElementById("sess-save");
  if (sessSave) {
    sessSave.onclick = async function () {
      try { localStorage.setItem("lygo_portal_session", sessionPayload()); } catch (_) {}
      const r = await saveToDisk("lygo-session-" + Date.now() + ".json", sessionPayload());
      limb.textContent = JSON.stringify(r, null, 2);
    };
  }
  const sessLoad = document.getElementById("sess-load");
  if (sessLoad) {
    sessLoad.onclick = function () {
      const inp = document.createElement("input");
      inp.type = "file";
      inp.accept = ".json,application/json";
      inp.onchange = function () {
        const f = inp.files && inp.files[0];
        if (!f) return;
        const rd = new FileReader();
        rd.onload = function () {
          try {
            const data = JSON.parse(String(rd.result || "{}"));
            history = data.history || [];
            if (npBody && data.notepad) npBody.value = data.notepad;
            log.innerHTML = "";
            history.forEach(function (m) { bubble(m.role === "user" ? "user" : "assistant", m.content || ""); });
            limb.textContent = "session loaded " + (data.saved || "");
          } catch (e) { limb.textContent = "bad session file"; }
        };
        rd.readAsText(f);
      };
      inp.click();
    };
  }
  const attachBtn = document.getElementById("attach-file");
  if (attachBtn) {
    attachBtn.onclick = function () {
      const inp = document.createElement("input");
      inp.type = "file";
      inp.onchange = function () {
        const f = inp.files && inp.files[0];
        if (!f) return;
        const rd = new FileReader();
        rd.onload = function () {
          const text = String(rd.result || "").slice(0, 20000);
          history.push({ role: "user", content: "Attached file " + f.name + ":\n" + text });
          bubble("user", "Attached " + f.name + " (" + text.length + " chars) into this session.");
        };
        rd.readAsText(f);
      };
      inp.click();
    };
  }
  fetch("lygo-skills.json", { cache: "no-store" })
    .then(function (r) { return r.json(); })
    .then(function (j) {
      SKILLS = j.skills || [];
      try {
        const saved = JSON.parse(localStorage.getItem("lygo_portal_enabled") || "{}");
        SKILLS.forEach(function (s) {
          ENABLED[s.slug] = saved[s.slug] !== false;
        });
      } catch (_) {
        SKILLS.forEach(function (s) { ENABLED[s.slug] = true; });
      }
      paintSkills();
    })
    .catch(function () {});
  fetch("https://chatagent.ca/data/lygoskillhub_catalog.json", { cache: "no-store" })
    .then(function (r) { return r.json(); })
    .then(function (j) {
      const extra = (j.skills || []).filter(function (s) { return s && s.slug && s.kind !== "page"; }).slice(0, 50);
      extra.forEach(function (s) {
        if (SKILLS.some(function (x) { return x.slug === s.slug; })) return;
        SKILLS.push({
          slug: s.slug,
          name: s.name || s.slug,
          when: s.category || "skillhub",
          text: (s.summary || s.name || s.slug) + "\nAlready on this portal as an advisor card. Scripts need the local FULL console.",
        });
        if (ENABLED[s.slug] === undefined) ENABLED[s.slug] = true;
      });
      paintSkills();
    })
    .catch(function () {});

  document.getElementById("form").onsubmit = async function (ev) {
    ev.preventDefault();
    const text = (msg.value || "").trim();
    if (!text) return;
    if (P0.test(text)) {
      bubble("assistant", "P0 blocked that prompt.");
      return;
    }
    msg.value = "";
    bubble("user", text);
    if (!connected) {
      if (readKey()) {
        setHealth("connecting…");
        const live = await pickLiveModel();
        connected = true;
        setHealth("connected · " + provider().label + " · " + live);
      } else {
        bubble("assistant", "Paste a Groq key in the key box, then send again. Model is chosen for you.");
        return;
      }
    }
    history.push({ role: "user", content: text });
    await pickLiveModel();
    const invoked = Object.keys(CHAMPS).find(function (n) { return text.toUpperCase().indexOf(n.toUpperCase()) >= 0; });
    let messages = [{ role: "system", content: systemPrompt(invoked) }].concat(history.slice(-10));
    let out = "";
    try {
      for (let round = 0; round < 4; round++) {
        const j = await callApi(messages);
        const got = extractMessage(j);
        limb.textContent = JSON.stringify({ round: round, tools: (got.tool_calls || []).map(function (t) { return (t.function || t).name; }) }, null, 2);
        if (got.tool_calls && got.tool_calls.length) {
          messages.push({ role: "assistant", content: got.text || "", tool_calls: got.tool_calls });
          for (let i = 0; i < got.tool_calls.length; i++) {
            const tc = got.tool_calls[i];
            const fn = tc.function || tc;
            let args = {};
            try { args = typeof fn.arguments === "string" ? JSON.parse(fn.arguments) : fn.arguments || {}; } catch (_) {}
            const result = await runTool(fn.name, args);
            messages.push({ role: "tool", tool_call_id: tc.id || String(i), content: JSON.stringify(result) });
          }
          continue;
        }
        out = got.text;
        break;
      }
    } catch (e) {
      const m = String(e && e.message ? e.message : e);
      if (/does not exist|do not have access|model_not_found|invalid_model/i.test(m)) {
        const live = await pickLiveModel();
        try {
          const j2 = await callApi(messages);
          const got2 = extractMessage(j2);
          out = got2.text || "";
          if (live) setHealth("connected · " + provider().label + " · " + live);
        } catch (e2) {
          out = "Could not reach a live model on this key (" + live + "). Try Connect again — the portal picks the model for you.";
        }
      } else if (/ISO-8859-1|non ISO|code point/i.test(m)) {
        readKey();
        try {
          const j2 = await callApi(messages);
          const got2 = extractMessage(j2);
          out = got2.text || "Key cleaned. Send Hello again.";
        } catch (e2) {
          out = "The key paste had hidden characters. We cleaned it — press Connect and send Hello again.";
        }
      } else if (/Failed to fetch|CORS|NetworkError/i.test(m)) {
        out = "Provider error: " + m + "\nThis vendor may block browser calls. Try Groq or OpenRouter.";
      } else {
        out = "Provider error: " + m;
      }
    }
    if (P0.test(out || "")) out = "[output quarantined]";
    if (!out) out = "(empty model reply — try again or another provider)";
    history.push({ role: "assistant", content: out });
    bubble("assistant", out);
  };

  bubble(
    "assistant",
    "Paste a Groq key → Connect (or just send). The model is chosen for you."
  );

  const worldLocal = document.getElementById("world-local");
  const worldUtc = document.getElementById("world-utc");
  function paintWorld() {
    const now = new Date();
    if (worldLocal) worldLocal.textContent = now.toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });
    if (worldUtc) worldUtc.textContent = "UTC " + now.toISOString().slice(0, 19).replace("T", " ");
  }
  paintWorld();
  setInterval(paintWorld, 1000);
  document.querySelectorAll(".y").forEach(function (el) {
    el.textContent = String(new Date().getFullYear());
  });
})();
