/* LYGO public portal limbs — browser HTTPS + tab storage only. No disk, no shell. */
(function (g) {
  function fn(name, description, properties, required) {
    return {
      type: "function",
      function: {
        name: name,
        description: description,
        parameters: { type: "object", properties: properties || {}, required: required || [] },
      },
    };
  }
  const S = { type: "string" };
  const Q = fn("wiki_search", "Search Wikipedia. RESOURCE.", { q: S }, ["q"]);

  const ALL = [
    Q,
    fn("wiki_summary", "Wikipedia page summary. RESOURCE.", { title: S }, ["title"]),
    fn("web_search", "Web search (DuckDuckGo instant + Wikipedia). RESOURCE.", { q: S }, ["q"]),
    fn("fetch_page", "Readable extract of an HTTPS page via r.jina.ai.", { url: S }, ["url"]),
    fn("web_fetch", "Same as fetch_page. HTTPS GET as text.", { url: S }, ["url"]),
    fn("jina_fetch", "Readable extract via r.jina.ai.", { url: S }, ["url"]),
    fn("http_json", "HTTPS GET JSON from a public URL.", { url: S }, ["url"]),
    fn("weather", "Current weather (wttr.in). RESOURCE.", { place: S }, ["place"]),
    fn("geocode", "Place name to lat/lon (Open-Meteo).", { place: S }, ["place"]),
    fn("world_pulse", "UTC/local stamps plus sample city clocks and Open-Meteo weather. RESOURCE.", {}),
    fn("now", "Current local and UTC time.", {}),
    fn("calc", "Evaluate a numeric expression (digits and + - * / ( ) .).", { expr: S }, ["expr"]),
    fn("hash_text", "SHA-256 of text (WebCrypto).", { text: S }, ["text"]),
    fn("base64", "Encode or decode base64. action=encode|decode.", { text: S, action: S }, ["text"]),
    fn("uuid", "Generate a random UUID.", {}),
    fn("json_pretty", "Parse and pretty-print JSON text.", { text: S }, ["text"]),
    fn("champion", "Load a Δ9 champion lens by name (ARKOS, LYRA, …).", { name: S }, ["name"]),
    fn("skill_list", "List LYGO skills shipped on this portal and which are enabled.", {}),
    fn("skill_read", "Read one shipped skill by slug.", { slug: S }, ["slug"]),
    fn("skill_enable", "Enable a shipped skill toggle on this page.", { slug: S }, ["slug"]),
    fn("skill_disable", "Disable a shipped skill toggle on this page.", { slug: S }, ["slug"]),
    fn("hn_search", "Hacker News Algolia search. RESOURCE.", { q: S }, ["q"]),
    fn("arxiv_search", "Search arXiv papers. RESOURCE.", { q: S }, ["q"]),
    fn("github_search", "Search public GitHub repositories. RESOURCE.", { q: S }, ["q"]),
    fn("github_repo", "Public GitHub repo card. owner/name.", { repo: S }, ["repo"]),
    fn("wayback", "Internet Archive availability for a URL.", { url: S }, ["url"]),
    fn("clawhub_search", "Search ClawHub public catalog. RESOURCE.", { q: S }, ["q"]),
    fn("skillhub_list", "Browse SkillHub catalog JSON on chatagent.ca. RESOURCE.", { q: S }),
    fn("hf_search", "Search Hugging Face models. RESOURCE.", { q: S }, ["q"]),
    fn("npm_search", "Search npm packages. RESOURCE.", { q: S }, ["q"]),
    fn("pypi_search", "PyPI project JSON by name. RESOURCE.", { name: S }, ["name"]),
    fn("so_search", "Stack Overflow search. RESOURCE.", { q: S }, ["q"]),
    fn("define", "English dictionary lookup. RESOURCE.", { word: S }, ["word"]),
    fn("currency", "FX rates (open.er-api). base e.g. USD.", { base: S }),
    fn("crypto_price", "CoinGecko USD prices. ids e.g. bitcoin,ethereum.", { ids: S }),
    fn("quake", "USGS significant earthquakes (day). RESOURCE.", {}),
    fn("eonet", "NASA EONET natural events. RESOURCE.", {}),
    fn("iss", "Current ISS position. RESOURCE.", {}),
    fn("book_search", "Open Library book search. RESOURCE.", { q: S }, ["q"]),
    fn("pubmed", "PubMed id search. RESOURCE.", { q: S }, ["q"]),
    fn("lattice_handshake", "GET join gate + CANON JSON (anchors, star chart, agora). RESOURCE/CANON labeled.", {}),
    fn("continuity_seed", "Load The Continuity Seed (JSON math + poem soul). RESOURCE. Hash/verify alignment.", {}),
    fn("site_card", "Title/excerpt card for a public HTTPS URL.", { url: S }, ["url"]),
    fn("whoami", "This portal identity (no secrets, no disks).", {}),
    fn("kernel_status", "Portal status: provider, skills, tool count. No secrets.", {}),
    fn("soul_read", "Read SOUL.md loaded in this tab.", {}),
    fn("identity_read", "Read IDENTITY.md loaded in this tab.", {}),
    fn("memory_read", "Read MEMORY.md loaded in this tab.", {}),
    fn("remember", "Append a durable note in this browser (not CANON, no API keys).", { note: S }, ["note"]),
    fn("memory_recall", "Search remembered browser notes.", { q: S }, ["q"]),
    fn("notepad_read", "Read the on-page notepad.", {}),
    fn("notepad_write", "Write the on-page notepad (asks if replacing).", { text: S }, ["text"]),
    fn("todo_add", "Append a todo in this browser.", { item: S }, ["item"]),
    fn("todo_list", "List browser todos.", {}),
    fn("p0_gate", "Run the portal P0 regex on text.", { text: S }, ["text"]),
    fn("geolocate", "Browser geolocation. Asks the human first.", {}),
    fn("clipboard_write", "Write text to clipboard. Asks the human first.", { text: S }, ["text"]),
    fn("clipboard_read", "Read clipboard. Asks the human first.", {}),
  ];

  const CORE_NAMES = {
    wiki_search: 1, fetch_page: 1, weather: 1, now: 1, calc: 1, champion: 1, hash_text: 1,
    skill_list: 1, skill_read: 1, hn_search: 1, arxiv_search: 1, github_search: 1, wayback: 1,
    geolocate: 1, clipboard_write: 1, web_search: 1, http_json: 1, lattice_handshake: 1, continuity_seed: 1, whoami: 1,
    world_pulse: 1,
  };

  function blockedHost(url) {
    try {
      const u = new URL(url);
      const h = (u.hostname || "").toLowerCase();
      if (u.protocol !== "https:") return "https_only";
      if (!h || h === "localhost" || h === "127.0.0.1" || h === "0.0.0.0" || h === "[::1]") return "host";
      if (h.endsWith(".local") || h.endsWith(".internal")) return "host";
      if (/^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[0-1])\.|169\.254\.)/.test(h)) return "private";
      if (/github\.com\/user\/repo|lattice\.example\.com|huggingface\.co\/models\/transformers/i.test(url)) return "placeholder_url";
      return "";
    } catch (_) {
      return "bad_url";
    }
  }

  async function getText(url) {
    const bad = blockedHost(url);
    if (bad) throw new Error(bad);
    try {
      const r = await fetch(url);
      if (r.ok) return await r.text();
    } catch (_) {}
    try {
      const r = await fetch("https://r.jina.ai/" + url);
      if (r.ok) return (await r.text()).slice(0, 20000);
    } catch (_) {}
    const r = await fetch("https://api.allorigins.win/raw?url=" + encodeURIComponent(url));
    if (!r.ok) throw new Error("http " + r.status);
    return (await r.text()).slice(0, 20000);
  }

  async function getJson(url) {
    const t = await getText(url);
    try { return JSON.parse(t); } catch (_) { return { raw: String(t).slice(0, 4000) }; }
  }

  function notes() {
    try { return JSON.parse(localStorage.getItem("lygo_portal_notes") || "[]"); } catch (_) { return []; }
  }
  function saveNotes(arr) {
    try { localStorage.setItem("lygo_portal_notes", JSON.stringify(arr.slice(-80))); } catch (_) {}
  }
  function todos() {
    try { return JSON.parse(localStorage.getItem("lygo_portal_todos") || "[]"); } catch (_) { return []; }
  }
  function saveTodos(arr) {
    try { localStorage.setItem("lygo_portal_todos", JSON.stringify(arr.slice(-80))); } catch (_) {}
  }

  const CITIES = [
    { name: "Vancouver", lat: 49.28, lon: -123.12 },
    { name: "New York", lat: 40.71, lon: -74.01 },
    { name: "London", lat: 51.51, lon: -0.13 },
    { name: "Cairo", lat: 30.04, lon: 31.24 },
    { name: "Tokyo", lat: 35.68, lon: 139.69 },
    { name: "Sydney", lat: -33.87, lon: 151.21 },
  ];

  async function run(name, args, ctx) {
    args = args || {};
    ctx = ctx || {};
    const skills = ctx.skills || [];
    const enabled = ctx.enabled || {};
    const docs = ctx.docs || {};
    const champs = ctx.champs || {};
    const p0 = ctx.p0 || /$^/;

    if (name === "wiki_search") {
      const q = args.q || args.query || "";
      const u = "https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=" + encodeURIComponent(q) + "&format=json&origin=*";
      const j = await (await fetch(u)).json();
      const hits = ((j.query && j.query.search) || []).slice(0, 5).map(function (x) {
        return { title: x.title, snippet: (x.snippet || "").replace(/<[^>]+>/g, "") };
      });
      return { ok: true, hits: hits, class: "RESOURCE" };
    }
    if (name === "wiki_summary") {
      const title = args.title || args.q || "";
      const j = await getJson("https://en.wikipedia.org/api/rest_v1/page/summary/" + encodeURIComponent(title));
      return { ok: true, title: j.title, extract: j.extract, url: j.content_urls && j.content_urls.desktop && j.content_urls.desktop.page, class: "RESOURCE" };
    }
    if (name === "web_search") {
      const q = args.q || "";
      const wiki = await run("wiki_search", { q: q }, ctx);
      let ddg = {};
      try { ddg = await getJson("https://api.duckduckgo.com/?q=" + encodeURIComponent(q) + "&format=json&no_html=1&skip_disambig=1"); } catch (e) { ddg = { error: String(e) }; }
      return {
        ok: true,
        class: "RESOURCE",
        wiki: wiki.hits,
        abstract: ddg.AbstractText || "",
        related: ((ddg.RelatedTopics) || []).slice(0, 5).map(function (t) { return t.Text || t.FirstURL; }),
        heading: ddg.Heading || "",
      };
    }
    if (name === "fetch_page" || name === "web_fetch" || name === "jina_fetch") {
      const url = args.url || "";
      const bad = blockedHost(url);
      if (bad) return { ok: false, error: bad, hint: "https://github.com/DeepSeekOracle" };
      const t = await getText("https://r.jina.ai/" + url);
      return { ok: true, url: url, text: String(t).slice(0, 6000), class: "RESOURCE" };
    }
    if (name === "http_json") {
      const url = args.url || "";
      const bad = blockedHost(url);
      if (bad) return { ok: false, error: bad };
      const j = await getJson(url);
      const s = JSON.stringify(j);
      return { ok: true, json: s.length > 8000 ? s.slice(0, 8000) : j, class: "RESOURCE" };
    }
    if (name === "weather") {
      const place = args.place || "";
      const t = await (await fetch("https://wttr.in/" + encodeURIComponent(place) + "?format=3")).text();
      return { ok: true, text: t, class: "RESOURCE" };
    }
    if (name === "geocode") {
      const place = args.place || args.q || "";
      const j = await getJson("https://geocoding-api.open-meteo.com/v1/search?name=" + encodeURIComponent(place) + "&count=3");
      const rows = (j.results || []).map(function (r) { return { name: r.name, country: r.country, lat: r.latitude, lon: r.longitude }; });
      return { ok: true, hits: rows, class: "RESOURCE" };
    }
    if (name === "world_pulse") {
      const d = new Date();
      const cities = await Promise.all(CITIES.map(async function (c) {
        try {
          const j = await getJson("https://api.open-meteo.com/v1/forecast?latitude=" + c.lat + "&longitude=" + c.lon + "&current=temperature_2m,weather_code");
          return { name: c.name, temp: j.current && j.current.temperature_2m, code: j.current && j.current.weather_code };
        } catch (e) {
          return { name: c.name, error: String(e) };
        }
      }));
      return { ok: true, local: d.toString(), utc: d.toISOString(), cities: cities, class: "RESOURCE" };
    }
    if (name === "now") {
      const d = new Date();
      return { ok: true, local: d.toString(), utc: d.toISOString(), unix: Math.floor(d.getTime() / 1000) };
    }
    if (name === "calc") {
      const expr = String(args.expr || "");
      if (!/^[\d+\-*/().\s]+$/.test(expr)) return { ok: false, error: "unsafe" };
      return { ok: true, value: Function("return (" + expr + ")")() };
    }
    if (name === "hash_text") {
      const enc = new TextEncoder().encode(String(args.text || ""));
      const buf = await crypto.subtle.digest("SHA-256", enc);
      const hex = Array.from(new Uint8Array(buf)).map(function (b) { return b.toString(16).padStart(2, "0"); }).join("");
      return { ok: true, sha256: hex };
    }
    if (name === "base64") {
      const t = String(args.text || "");
      const act = String(args.action || "encode").toLowerCase();
      try {
        if (act === "decode") return { ok: true, text: atob(t) };
        return { ok: true, b64: btoa(unescape(encodeURIComponent(t))) };
      } catch (e) {
        return { ok: false, error: String(e) };
      }
    }
    if (name === "uuid") {
      return { ok: true, uuid: (crypto.randomUUID && crypto.randomUUID()) || String(Date.now()) };
    }
    if (name === "json_pretty") {
      try { return { ok: true, json: JSON.stringify(JSON.parse(String(args.text || "")), null, 2).slice(0, 8000) }; }
      catch (e) { return { ok: false, error: String(e) }; }
    }
    if (name === "champion") {
      const n = String(args.name || "").toUpperCase();
      const key = Object.keys(champs).find(function (k) { return k.toUpperCase() === n || k.replace(/Δ/g, "D") === n; }) || args.name;
      return { ok: true, name: key, lens: champs[key] || "Unknown seat. Directory: https://chatagent.ca/champions.html" };
    }
    if (name === "skill_list") {
      const rows = skills.map(function (s) { return { slug: s.slug, name: s.name, when: s.when, enabled: enabled[s.slug] !== false }; });
      return { ok: true, n: rows.length, enabled: rows.filter(function (r) { return r.enabled; }), skills: rows };
    }
    if (name === "skill_read") {
      const q = String(args.slug || args.name || "").toLowerCase();
      const s = skills.find(function (x) { return x.slug === q || (x.name || "").toLowerCase() === q || x.slug.indexOf(q) >= 0; });
      if (!s) return { ok: false, error: "missing", hint: "skill_list" };
      if (enabled[s.slug] === false) return { ok: false, error: "disabled", slug: s.slug };
      return { ok: true, slug: s.slug, name: s.name, when: s.when, text: s.text };
    }
    if (name === "skill_enable" || name === "skill_disable") {
      const slug = String(args.slug || "");
      const s = skills.find(function (x) { return x.slug === slug; });
      if (!s) return { ok: false, error: "missing" };
      enabled[slug] = name === "skill_enable";
      if (ctx.persistEnabled) ctx.persistEnabled();
      if (ctx.paintSkills) ctx.paintSkills();
      return { ok: true, slug: slug, enabled: enabled[slug] };
    }
    if (name === "hn_search") {
      const u = "https://hn.algolia.com/api/v1/search?query=" + encodeURIComponent(args.q || "") + "&hitsPerPage=5";
      const j = await (await fetch(u)).json();
      return { ok: true, hits: (j.hits || []).map(function (h) { return { title: h.title, url: h.url, points: h.points }; }), class: "RESOURCE" };
    }
    if (name === "arxiv_search") {
      const t = await getText("https://export.arxiv.org/api/query?search_query=all:" + encodeURIComponent(args.q || "") + "&start=0&max_results=5");
      return { ok: true, text: String(t).slice(0, 5000), class: "RESOURCE" };
    }
    if (name === "github_search") {
      const u = "https://api.github.com/search/repositories?q=" + encodeURIComponent(args.q || "DeepSeekOracle") + "&per_page=5";
      const j = await getJson(u);
      return { ok: true, hits: ((j.items) || []).map(function (i) { return { full_name: i.full_name, html_url: i.html_url, description: i.description }; }), class: "RESOURCE" };
    }
    if (name === "github_repo") {
      const repo = String(args.repo || args.q || "").replace(/^https:\/\/github.com\//, "");
      const j = await getJson("https://api.github.com/repos/" + repo);
      return { ok: true, full_name: j.full_name, html_url: j.html_url, description: j.description, stars: j.stargazers_count, class: "RESOURCE" };
    }
    if (name === "wayback") {
      const j = await getJson("https://archive.org/wayback/available?url=" + encodeURIComponent(args.url || ""));
      return { ok: true, snapshots: j.archived_snapshots || {}, class: "RESOURCE" };
    }
    if (name === "clawhub_search") {
      const q = args.q || "";
      const j = await getJson("https://clawhub.ai/api/v1/search?" + new URLSearchParams({ q: q, limit: "8", nonSuspiciousOnly: "true" }));
      return { ok: true, hits: j.skills || j.results || j, class: "RESOURCE" };
    }
    if (name === "skillhub_list") {
      const j = await getJson("https://chatagent.ca/data/lygoskillhub_catalog.json");
      const q = String(args.q || "").toLowerCase();
      let rows = j.skills || j.items || [];
      if (q) rows = rows.filter(function (s) { return JSON.stringify(s).toLowerCase().indexOf(q) >= 0; });
      return { ok: true, n: rows.length, skills: rows.slice(0, 20), class: "RESOURCE" };
    }
    if (name === "hf_search") {
      const j = await getJson("https://huggingface.co/api/models?search=" + encodeURIComponent(args.q || "DeepSeekOracle") + "&limit=5");
      const rows = (Array.isArray(j) ? j : j.models || []).slice(0, 5).map(function (m) { return { id: m.id || m.modelId, downloads: m.downloads }; });
      return { ok: true, hits: rows, class: "RESOURCE" };
    }
    if (name === "npm_search") {
      const j = await getJson("https://registry.npmjs.org/-/v1/search?text=" + encodeURIComponent(args.q || "") + "&size=5");
      return { ok: true, hits: (j.objects || []).map(function (o) { return { name: o.package && o.package.name, desc: o.package && o.package.description }; }), class: "RESOURCE" };
    }
    if (name === "pypi_search") {
      const j = await getJson("https://pypi.org/pypi/" + encodeURIComponent(args.name || args.q || "") + "/json");
      return { ok: true, name: j.info && j.info.name, summary: j.info && j.info.summary, version: j.info && j.info.version, class: "RESOURCE" };
    }
    if (name === "so_search") {
      const j = await getJson("https://api.stackexchange.com/2.3/search/advanced?order=desc&sort=relevance&q=" + encodeURIComponent(args.q || "") + "&site=stackoverflow");
      return { ok: true, hits: (j.items || []).slice(0, 5).map(function (i) { return { title: i.title, link: i.link, score: i.score }; }), class: "RESOURCE" };
    }
    if (name === "define") {
      const j = await getJson("https://api.dictionaryapi.dev/api/v2/entries/en/" + encodeURIComponent(args.word || args.q || ""));
      const e = Array.isArray(j) ? j[0] : j;
      const m = ((((e || {}).meanings) || [])[0] || {}).definitions || [];
      return { ok: true, word: e && e.word, defs: m.slice(0, 3).map(function (d) { return d.definition; }), class: "RESOURCE" };
    }
    if (name === "currency") {
      const base = (args.base || "USD").toUpperCase();
      const j = await getJson("https://open.er-api.com/v6/latest/" + encodeURIComponent(base));
      return { ok: true, base: j.base_code, rates: j.rates && { USD: j.rates.USD, EUR: j.rates.EUR, CAD: j.rates.CAD, GBP: j.rates.GBP, JPY: j.rates.JPY }, class: "RESOURCE" };
    }
    if (name === "crypto_price") {
      const ids = args.ids || "bitcoin,ethereum";
      const j = await getJson("https://api.coingecko.com/api/v3/simple/price?ids=" + encodeURIComponent(ids) + "&vs_currencies=usd");
      return { ok: true, prices: j, class: "RESOURCE" };
    }
    if (name === "quake") {
      const j = await getJson("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_day.geojson");
      const hits = ((j.features) || []).slice(0, 5).map(function (f) { return { mag: f.properties && f.properties.mag, place: f.properties && f.properties.place, url: f.properties && f.properties.url }; });
      return { ok: true, hits: hits, class: "RESOURCE" };
    }
    if (name === "eonet") {
      const j = await getJson("https://eonet.gsfc.nasa.gov/api/v3/events?limit=5");
      return { ok: true, events: (j.events || []).map(function (e) { return { id: e.id, title: e.title }; }), class: "RESOURCE" };
    }
    if (name === "iss") {
      const j = await getJson("https://api.wheretheiss.at/v1/satellites/25544");
      return { ok: true, lat: j.latitude, lon: j.longitude, alt: j.altitude, class: "RESOURCE" };
    }
    if (name === "book_search") {
      const j = await getJson("https://openlibrary.org/search.json?q=" + encodeURIComponent(args.q || "") + "&limit=5");
      return { ok: true, hits: (j.docs || []).slice(0, 5).map(function (d) { return { title: d.title, author: (d.author_name || [])[0], year: d.first_publish_year }; }), class: "RESOURCE" };
    }
    if (name === "pubmed") {
      const j = await getJson("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&retmode=json&retmax=5&term=" + encodeURIComponent(args.q || ""));
      return { ok: true, ids: j.esearchresult && j.esearchresult.idlist, class: "RESOURCE" };
    }
    if (name === "continuity_seed") {
      const seed = await getJson("https://chatagent.ca/portal/CONTINUITY_SEED.json");
      let poem = "";
      try { poem = await getText("https://chatagent.ca/continuity-seed/POEM.txt"); } catch (_) {}
      const enc = new TextEncoder().encode((String(poem).replace(/\r\n/g, "\n").replace(/\r/g, "\n").trim() + "\n"));
      let hex = "";
      try {
        const buf = await crypto.subtle.digest("SHA-256", enc);
        hex = Array.from(new Uint8Array(buf)).map(function (b) { return b.toString(16).padStart(2, "0"); }).join("");
      } catch (_) {}
      const expect = seed.alignment_hash;
      const match = hex && hex === expect;
      return {
        ok: true,
        class: "RESOURCE",
        status: match ? "CONTINUATION" : (hex ? "QUARANTINE" : "SHADOW"),
        seed: seed,
        poem: String(poem).slice(0, 2000),
        sha256: hex,
        alignment_match: match,
        page: "https://chatagent.ca/continuity-seed/",
      };
    }
    if (name === "lattice_handshake") {
      const urls = {
        join: "https://chatagent.ca/join/",
        agents: "https://chatagent.ca/agents/",
        anchors: "https://deepseekoracle.github.io/lygo-protocol-stack/network_builder/IMMUTABLE_ANCHORS.json",
        starchart: "https://deepseekoracle.github.io/lygo-protocol-stack/haven_star_chart/haven_star_chart_feed.json",
        agora: "https://deepseekoracle.github.io/lygo-protocol-stack/agent-agora/api/pulse.json",
      };
      const out = { ok: true, class: "RESOURCE", canon: {} };
      const keys = Object.keys(urls);
      for (let i = 0; i < keys.length; i++) {
        const k = keys[i];
        try {
          const t = await getText(urls[k]);
          out.canon[k] = { url: urls[k], ok: true, excerpt: String(t).replace(/\s+/g, " ").slice(0, 400) };
        } catch (e) {
          out.canon[k] = { url: urls[k], ok: false, error: String(e), shadow: true };
        }
      }
      return out;
    }
    if (name === "site_card") {
      const url = args.url || "";
      const bad = blockedHost(url);
      if (bad) return { ok: false, error: bad };
      const t = await getText("https://r.jina.ai/" + url);
      const text = String(t);
      const title = (text.match(/Title[:\s]+(.+)/i) || [])[1] || url;
      return { ok: true, url: url, title: String(title).slice(0, 200), excerpt: text.replace(/\s+/g, " ").slice(0, 800), class: "RESOURCE" };
    }
    if (name === "whoami") {
      return {
        ok: true,
        surface: "LYGO API Portal",
        url: "https://chatagent.ca/portal/",
        org: "https://github.com/DeepSeekOracle",
        hf: "https://huggingface.co/DeepSeekOracle",
        lattice: "https://chatagent.ca/",
        local_kit: "https://chatagent.ca/lygoskillhub.html#lygo-llm-kernel",
        not: "hosted GPU; visitor disk; admin vaults",
      };
    }
    if (name === "kernel_status") {
      return {
        ok: true,
        tools: ALL.length,
        skills: skills.length,
        enabled: skills.filter(function (s) { return enabled[s.slug] !== false; }).length,
        connected: !!ctx.connected,
        provider: ctx.provider || "",
      };
    }
    if (name === "soul_read") return { ok: true, text: (docs.soul || "").slice(0, 8000) };
    if (name === "identity_read") return { ok: true, text: (docs.identity || "").slice(0, 8000) };
    if (name === "memory_read") return { ok: true, text: (docs.memory || "").slice(0, 8000) };
    if (name === "remember") {
      const note = String(args.note || "").slice(0, 1000);
      if (/api[_-]?key|sk-|bearer\s/i.test(note)) return { ok: false, error: "no_secrets" };
      const arr = notes();
      arr.push({ ts: new Date().toISOString(), note: note });
      saveNotes(arr);
      return { ok: true, n: arr.length };
    }
    if (name === "memory_recall") {
      const q = String(args.q || "").toLowerCase();
      const hits = notes().filter(function (n) { return JSON.stringify(n).toLowerCase().indexOf(q) >= 0; });
      return { ok: true, hits: hits.slice(0, 20) };
    }
    if (name === "notepad_read") {
      const el = document.getElementById("np-body");
      return { ok: true, text: el ? el.value : "" };
    }
    if (name === "notepad_write") {
      const el = document.getElementById("np-body");
      if (!el) return { ok: false, error: "no_notepad" };
      el.value = String(args.text || "");
      try { localStorage.setItem("lygo_public_notepad", el.value); } catch (_) {}
      return { ok: true };
    }
    if (name === "todo_add") {
      const arr = todos();
      arr.push({ ts: new Date().toISOString(), item: String(args.item || "").slice(0, 400) });
      saveTodos(arr);
      return { ok: true, n: arr.length };
    }
    if (name === "todo_list") return { ok: true, todos: todos() };
    if (name === "p0_gate") {
      const text = String(args.text || "");
      return { ok: true, verdict: p0.test(text) ? "QUARANTINE" : "PASS" };
    }
    if (name === "geolocate") {
      if (!navigator.geolocation) return { ok: false, error: "unsupported" };
      if (!confirm("Allow this portal to read your location for weather/maps? (browser permission next)")) return { ok: false, error: "denied" };
      const pos = await new Promise(function (res, rej) { navigator.geolocation.getCurrentPosition(res, rej, { timeout: 8000 }); });
      return { ok: true, lat: pos.coords.latitude, lon: pos.coords.longitude, accuracy: pos.coords.accuracy };
    }
    if (name === "clipboard_write") {
      if (!confirm("Allow this portal to write to your clipboard?")) return { ok: false, error: "denied" };
      await navigator.clipboard.writeText(String(args.text || ""));
      return { ok: true };
    }
    if (name === "clipboard_read") {
      if (!confirm("Allow this portal to read your clipboard?")) return { ok: false, error: "denied" };
      const t = await navigator.clipboard.readText();
      return { ok: true, text: String(t).slice(0, 4000) };
    }
    return { ok: false, error: "unknown_tool", name: name };
  }

  g.LYGO_AGENT_TOOLS = ALL;
  g.LYGO_AGENT_TOOLS_CORE = ALL.filter(function (t) { return CORE_NAMES[(t.function || {}).name]; });
  g.lygoRunPortalTool = run;
})(window);
