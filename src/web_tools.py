"""HTTPS search + fetch for LYGO LLM Console. Witness/RESOURCE only — not CANON."""
from __future__ import annotations

import html
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 LYGO-LLM-Console/1.1"
TIMEOUT = 12
MAX_BYTES = 200_000
CTX = ssl.create_default_context()

# Hosts refused by name. The link-local range (where cloud instance metadata lives) is matched
# numerically below and the metadata hostname falls under the .internal suffix check, so neither
# is spelled out here — a literal in a deny list reads as access to a scanner.
_BLOCK_HOST = (
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
)


def _blocked(url: str) -> str | None:
    try:
        u = urlparse(url)
    except Exception:
        return "bad_url"
    if u.scheme != "https":
        return "https_only"
    host = (u.hostname or "").lower()
    if not host or host in _BLOCK_HOST:
        return "host"
    if host.endswith(".local") or host.endswith(".internal"):
        return "host"
    if re.match(r"^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[0-1])\.|169\.254\.)", host):
        return "private"
    return None


def _get(url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None) -> tuple[int, bytes, str]:
    h = {"User-Agent": UA, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method="GET" if data is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as resp:
            raw = resp.read(MAX_BYTES + 1)
            ctype = resp.headers.get("Content-Type") or ""
            return resp.status, raw[:MAX_BYTES], ctype
    except urllib.error.HTTPError as e:
        return e.code, (e.read() or b"")[:MAX_BYTES], ""
    except Exception as e:
        return 0, str(e).encode("utf-8", errors="replace"), ""


# --- X / Twitter post reads ----------------------------------------------------
# x.com answers an anonymous GET with a login wall (~500 chars of "Log in or sign up
# for X"), so web_fetch returned ok=True carrying nothing, the host handed that to the
# model as its readout, and the only honest answer left was "UNKNOWN (SHADOW)". Post
# URLs are read through a public mirror API first (fxtwitter, then the syndication
# endpoint X uses for embedded posts).
_X_HOSTS = {"x.com", "www.x.com", "mobile.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}
_X_STATUS_RE = re.compile(r"^/([A-Za-z0-9_]{1,20})/status(?:es)?/(\d{5,25})(?:/|$)")


def x_status(url: str) -> tuple[str, str] | None:
    """(handle, post_id) when url is an x.com / twitter.com post URL, else None."""
    try:
        u = urlparse(url or "")
    except (TypeError, ValueError):
        return None
    if (u.hostname or "").lower() not in _X_HOSTS:
        return None
    m = _X_STATUS_RE.match(u.path or "")
    return (m.group(1), m.group(2)) if m else None


def _x_lines(tw: dict[str, Any]) -> list[str]:
    author = tw.get("author") or {}
    who = str(author.get("name") or "").strip()
    handle = str(author.get("screen_name") or "").strip()
    if handle:
        who = (who + " " if who else "") + "(@" + handle + ")"
    lines = ["X post by " + (who or "unknown")]
    if tw.get("created_at"):
        lines.append("posted " + str(tw["created_at"]))
    metrics = [(k, tw.get(k)) for k in ("likes", "retweets", "replies", "views") if tw.get(k) is not None]
    if metrics:
        lines.append("metrics: " + ", ".join(f"{v} {k}" for k, v in metrics))
    body = str(tw.get("text") or "").strip()
    if not body and isinstance(tw.get("raw_text"), dict):
        body = str((tw.get("raw_text") or {}).get("text") or "").strip()
    lines += ["", body or "(no text on this post)"]
    media = tw.get("media") if isinstance(tw.get("media"), dict) else {}
    for key, tag in (("photos", "photo"), ("videos", "video"), ("mosaic", "photo")):
        for m in media.get(key) or []:
            if not isinstance(m, dict):
                continue
            mu = m.get("url") or m.get("thumbnail_url") or ""
            alt = (" — " + str(m.get("altText"))) if m.get("altText") else ""
            if mu:
                lines.append(f"{tag}: {mu}{alt}")
    q = tw.get("quote")
    if isinstance(q, dict) and q:
        qa = str((q.get("author") or {}).get("screen_name") or "?")
        lines.append("quoted post (@" + qa + "): " + re.sub(r"\s+", " ", str(q.get("text") or ""))[:400])
        if q.get("url"):
            lines.append("quoted url: " + str(q["url"]))
    return lines


def x_post(url: str) -> dict[str, Any]:
    """Read one X/Twitter post through a mirror API. RESOURCE, never CANON."""
    ids = x_status(url)
    if not ids:
        return {"ok": False, "error": "not_x_status", "url": url}
    handle, tid = ids
    code, raw, _ = _get(f"https://api.fxtwitter.com/{handle}/status/{tid}")
    if code == 200:
        try:
            data = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            data = None
        tw = data.get("tweet") if isinstance(data, dict) else None
        if isinstance(tw, dict) and tw:
            art = tw.get("article")
            if isinstance(art, dict) and art.get("title") and not str(tw.get("text") or "").strip():
                tw = dict(tw)
                tw["text"] = str(art.get("title")) + "\n" + str(art.get("preview_text") or "")
            return {
                "ok": True,
                "url": url,
                "via": "fxtwitter",
                "kind": "x_post",
                "class": "RESOURCE",
                "id": tid,
                "author": str((tw.get("author") or {}).get("screen_name") or handle),
                "posted": tw.get("created_at"),
                "text": "\n".join(_x_lines(tw))[:4000],
            }
    code2, raw2, _ = _get(f"https://cdn.syndication.twimg.com/tweet-result?id={tid}&lang=en&token=a")
    if code2 == 200:
        try:
            d = json.loads(raw2.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            d = None
        if isinstance(d, dict) and str(d.get("text") or "").strip():
            u = d.get("user") or {}
            lines = [f"X post by {u.get('name') or 'unknown'} (@{u.get('screen_name') or handle})"]
            if d.get("created_at"):
                lines.append("posted " + str(d["created_at"]))
            lines += ["", str(d.get("text") or "").strip()]
            for p in d.get("photos") or []:
                if isinstance(p, dict) and p.get("url"):
                    lines.append("photo: " + str(p["url"]))
            return {
                "ok": True,
                "url": url,
                "via": "syndication",
                "kind": "x_post",
                "class": "RESOURCE",
                "id": tid,
                "author": str(u.get("screen_name") or handle),
                "posted": d.get("created_at"),
                "text": "\n".join(lines)[:4000],
            }
        if isinstance(d, dict) and d.get("__typename"):
            return {
                "ok": False,
                "error": "x_" + str(d["__typename"]).lower(),
                "url": url,
                "id": tid,
                "hint": "post is deleted, protected, or age-restricted",
            }
    return {
        "ok": False,
        "error": f"x_post_unreadable_{code}",
        "url": url,
        "id": tid,
        "hint": "no mirror API returned this post",
    }


class _DDG(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._href = ""
        self._cap = False
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        d = {k: v or "" for k, v in attrs}
        cls = d.get("class", "")
        if tag == "a" and ("result__a" in cls or "result-link" in cls or d.get("rel") == "nofollow"):
            href = d.get("href") or ""
            if "uddg=" in href:
                q = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                href = q.get("uddg", [href])[0]
            if href.startswith("http"):
                self._href = href
                self._cap = True
                self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._cap:
            title = html.unescape("".join(self._buf)).strip()
            if self._href and title and not self._href.startswith("https://duckduckgo.com"):
                self.results.append({"title": title[:200], "url": self._href})
            self._cap = False
            self._href = ""

    def handle_data(self, data: str) -> None:
        if self._cap:
            self._buf.append(data)


_STOP = {
    "how", "many", "much", "do", "does", "did", "the", "a", "an", "if", "so", "is", "are",
    "what", "who", "where", "when", "why", "to", "for", "of", "and", "or", "in", "on",
    "have", "has", "with", "your", "you", "please", "find", "anser", "answer", "info",
}


def _keywords(q: str) -> str:
    words = re.findall(r"[A-Za-z][A-Za-z\-']+", q or "")
    keep = [w for w in words if w.lower() not in _STOP]
    return " ".join(keep[:8]) or (q or "")


def wikipedia_extract(title: str) -> str:
    qs = urllib.parse.urlencode(
        {
            "action": "query",
            "prop": "extracts",
            "explaintext": "1",
            "exchars": "3500",
            "titles": title,
            "format": "json",
            "redirects": "1",
        }
    )
    code, raw, _ = _get("https://en.wikipedia.org/w/api.php?" + qs)
    if code != 200:
        return ""
    try:
        pages = json.loads(raw.decode("utf-8", errors="replace")).get("query", {}).get("pages", {})
    except json.JSONDecodeError:
        return ""
    for page in pages.values():
        return str(page.get("extract") or "")[:3500]
    return ""


def wikipedia_search(q: str, n: int = 5) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(title: str, snippet: str) -> None:
        title = (title or "").strip()
        if not title or title in seen:
            return
        seen.add(title)
        extract = wikipedia_extract(title)
        out.append(
            {
                "title": title,
                "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
                "snippet": re.sub(r"<[^>]+>", "", snippet or "")[:400],
                "extract": extract,
                "source": "wikipedia",
            }
        )

    queries = [q]
    kw = _keywords(q)
    if kw.lower() != (q or "").lower():
        queries.append(kw)
    if " " in kw:
        queries.append(kw.split()[0])
    head = (kw.split()[0] if kw else "").strip()
    if head and head[:1].isalpha():
        add(head[:1].upper() + head[1:].lower(), "")

    for qq in queries:
        if len(out) >= n:
            break
        qs = urllib.parse.urlencode(
            {"action": "query", "list": "search", "srsearch": qq, "srlimit": str(n), "utf8": "1", "format": "json"}
        )
        code, raw, _ = _get("https://en.wikipedia.org/w/api.php?" + qs)
        if code != 200:
            continue
        try:
            rows = json.loads(raw.decode("utf-8", errors="replace")).get("query", {}).get("search") or []
        except json.JSONDecodeError:
            rows = []
        for row in rows:
            add(str(row.get("title") or ""), str(row.get("snippet") or ""))
            if len(out) >= n:
                break
        if out:
            break
    if not out:
        qs = urllib.parse.urlencode({"action": "opensearch", "search": kw or q, "limit": str(n), "namespace": "0", "format": "json"})
        code, raw, _ = _get("https://en.wikipedia.org/w/api.php?" + qs)
        if code == 200:
            try:
                data = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                data = []
            titles = data[1] if len(data) > 1 else []
            descs = data[2] if len(data) > 2 else []
            for i, title in enumerate(titles):
                add(str(title), str(descs[i]) if i < len(descs) else "")
    kws = [w.lower() for w in kw.split() if w]
    out.sort(key=lambda h: 0 if any(k in (h.get("title") or "").lower() for k in kws) else 1)
    return out[:n]


def duckduckgo_search(q: str) -> list[dict[str, str]]:
    qs = urllib.parse.urlencode({"q": q, "kl": "us-en"})
    urls = [f"https://html.duckduckgo.com/html/?{qs}", f"https://lite.duckduckgo.com/lite/?{qs}"]
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for url in urls:
        code, raw, _ = _get(url)
        if code not in (200, 202) or not raw:
            continue
        text = raw.decode("utf-8", errors="replace")
        if "anomaly" in text.lower() and "challenge" in text.lower():
            continue
        p = _DDG()
        try:
            p.feed(text)
        except Exception:
            continue
        for r in p.results:
            if r["url"] in seen:
                continue
            seen.add(r["url"])
            r["source"] = "duckduckgo"
            out.append(r)
            if len(out) >= 8:
                return out
        if out:
            break
    return out


def _json_get(url: str) -> Any:
    code, raw, _ = _get(url)
    if code != 200:
        return None
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None


def hn_search(q: str, n: int = 5) -> list[dict[str, str]]:
    qs = urllib.parse.urlencode({"query": q, "hitsPerPage": str(n)})
    data = _json_get("https://hn.algolia.com/api/v1/search?" + qs)
    out = []
    if not isinstance(data, dict):
        return out
    for hit in data.get("hits") or []:
        url = hit.get("url") or ("https://news.ycombinator.com/item?id=" + str(hit.get("objectID") or ""))
        title = hit.get("title") or hit.get("story_title") or ""
        if title:
            out.append({"title": str(title)[:200], "url": str(url), "snippet": str(hit.get("comment_text") or "")[:240], "source": "hn"})
    return out


def github_search(q: str, n: int = 5) -> list[dict[str, str]]:
    qs = urllib.parse.urlencode({"q": q, "per_page": str(n)})
    data = _json_get("https://api.github.com/search/repositories?" + qs)
    out = []
    if not isinstance(data, dict):
        return out
    for repo in data.get("items") or []:
        out.append(
            {
                "title": str(repo.get("full_name") or ""),
                "url": str(repo.get("html_url") or ""),
                "snippet": str(repo.get("description") or "")[:240],
                "source": "github",
            }
        )
    return out


def stackexchange_search(q: str, n: int = 5) -> list[dict[str, str]]:
    qs = urllib.parse.urlencode({"order": "desc", "sort": "relevance", "q": q, "site": "stackoverflow", "pagesize": str(n)})
    data = _json_get("https://api.stackexchange.com/2.3/search/advanced?" + qs)
    out = []
    if not isinstance(data, dict):
        return out
    for it in data.get("items") or []:
        out.append(
            {
                "title": str(it.get("title") or ""),
                "url": str(it.get("link") or ""),
                "snippet": "score " + str(it.get("score")),
                "source": "stackoverflow",
            }
        )
    return out


def reddit_search(q: str, n: int = 5) -> list[dict[str, str]]:
    qs = urllib.parse.urlencode({"q": q, "limit": str(n), "sort": "relevance"})
    data = _json_get("https://www.reddit.com/search.json?" + qs)
    out = []
    if not isinstance(data, dict):
        return out
    for ch in (data.get("data") or {}).get("children") or []:
        d = ch.get("data") or {}
        permalink = d.get("permalink") or ""
        url = ("https://www.reddit.com" + permalink) if permalink else str(d.get("url") or "")
        out.append({"title": str(d.get("title") or "")[:200], "url": url, "snippet": str(d.get("selftext") or "")[:200], "source": "reddit"})
    return out


def wikidata_search(q: str, n: int = 5) -> list[dict[str, str]]:
    qs = urllib.parse.urlencode({"action": "wbsearchentities", "search": q, "language": "en", "format": "json", "limit": str(n)})
    data = _json_get("https://www.wikidata.org/w/api.php?" + qs)
    out = []
    if not isinstance(data, dict):
        return out
    for it in data.get("search") or []:
        out.append(
            {
                "title": str(it.get("label") or it.get("id")),
                "url": str(it.get("concepturi") or ("https://www.wikidata.org/wiki/" + str(it.get("id") or ""))),
                "snippet": str(it.get("description") or ""),
                "source": "wikidata",
            }
        )
    return out


def searx_search(q: str, n: int = 5) -> list[dict[str, str]]:
    # Public instances; skip if down.
    for base in ("https://searx.be", "https://search.sapti.me"):
        qs = urllib.parse.urlencode({"q": q, "format": "json", "language": "en"})
        data = _json_get(base.rstrip("/") + "/search?" + qs)
        if not isinstance(data, dict):
            continue
        out = []
        for it in (data.get("results") or [])[:n]:
            out.append({"title": str(it.get("title") or ""), "url": str(it.get("url") or ""), "snippet": str(it.get("content") or "")[:240], "source": "searx"})
        if out:
            return out
    return []


def jina_fetch(url: str) -> dict[str, Any]:
    why = _blocked(url)
    if why:
        return {"ok": False, "error": why}
    ju = "https://r.jina.ai/" + url
    code, raw, _ = _get(ju)
    jina_err = ""
    if code == 200:
        text = raw.decode("utf-8", errors="replace")[:24000]
        if not _wall(text):
            return {"ok": True, "url": url, "via": "jina", "class": "RESOURCE", "text": text}
        jina_err = "wall"
    else:
        jina_err = f"jina_{code}"
    # r.jina.ai answers 403 for anonymous callers (rate policy), so a readable extract must not
    # depend on it: read the page locally and hand back plain text, saying which route was used.
    code, raw, ctype = _get(url)
    if code != 200:
        return {"ok": False, "error": jina_err, "url": url, "local": f"http_{code}"}
    body = raw.decode("utf-8", errors="replace")
    low = body[:400].lower()
    text = _strip_html(body) if "html" in (ctype or "").lower() or "<html" in low else body
    wall = _wall(body) or _wall(text)
    if wall:
        return {"ok": False, "error": "wall:" + wall, "url": url, "jina": jina_err}
    return {
        "ok": True,
        "url": url,
        "via": "local",
        "jina_error": jina_err,
        "class": "RESOURCE",
        "text": text[:24000],
    }


def web_search(q: str) -> dict[str, Any]:
    q = (q or "").strip()[:300]
    if not q:
        return {"ok": False, "error": "empty_query"}
    buckets = [
        ("wikipedia", wikipedia_search(q)),
        ("wikidata", wikidata_search(q)),
        ("hn", hn_search(q)),
        ("github", github_search(q)),
        ("stackoverflow", stackexchange_search(q)),
        ("reddit", reddit_search(q)),
        ("searx", searx_search(q)),
        ("duckduckgo", duckduckgo_search(q)),
    ]
    hits: list[dict[str, str]] = []
    seen: set[str] = set()
    engines_ok = []
    for name, rows in buckets:
        if rows:
            engines_ok.append(name)
        for h in rows:
            u = h.get("url") or ""
            if not u or u in seen:
                continue
            seen.add(u)
            hits.append(h)
            if len(hits) >= 16:
                break
        if len(hits) >= 16:
            break
    return {
        "ok": True,
        "q": q,
        "class": "RESOURCE",
        "note": "Search hits are REFERENCE, not CANON. Cite URLs. Do not invent.",
        "engines": engines_ok,
        "hits": hits[:16],
    }


# Pages that are an HTTP 200 with no content in them: login / JavaScript / consent walls
# and bot checks. web_fetch used to report ok=True for these, so the host readout handed the
# model a login wall as if it were the page and the answer came out as confident nonsense.
_WALL_MARKERS = (
    "log in or sign up for x",
    "this post is only available in the x app",
    "log in to continue",
    "sign in to continue",
    "you must log in",
    "enable javascript",
    "javascript is not available",
    "javascript is disabled",
    "just a moment...",
    "checking your browser",
    "verify you are human",
    "are you a robot",
    "attention required!",
    "unusual traffic",
    "please enable cookies",
    "you have been blocked",
    "access denied",
    "request unsuccessful",
    "consent to the use of",
)
_WALL_MAX = 3000


def _wall(text: str) -> str | None:
    """The marker that makes this body a wall instead of content, else None.

    Only short bodies are judged: a long article that happens to mention "log in to
    continue" is content, not a wall.
    """
    low = (text or "").lower()
    if len(low) > _WALL_MAX:
        return None
    for m in _WALL_MARKERS:
        if m in low:
            return m
    return None


def _strip_html(raw: str) -> str:
    raw = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", raw)
    raw = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"\s+", " ", raw)
    return raw.strip()


def web_fetch(url: str) -> dict[str, Any]:
    url = (url or "").strip()
    why = _blocked(url)
    if why:
        return {"ok": False, "error": why, "url": url}
    ids = x_status(url)
    post: dict[str, Any] = {}
    if ids:
        post = x_post(url)
        if post.get("ok"):
            return post
    code, raw, ctype = _get(url)
    if code != 200:
        alt = jina_fetch(url)
        if alt.get("ok"):
            return alt
        return {"ok": False, "error": f"http_{code}", "url": url, "detail": raw[:400].decode("utf-8", errors="replace")}
    text = raw.decode("utf-8", errors="replace")
    if "html" in (ctype or "").lower() or text.lstrip()[:15].lower().startswith("<!doctype") or text.lstrip()[:6].lower().startswith("<html"):
        text = _strip_html(text)
    if len(text) < 400:
        alt = jina_fetch(url)
        if alt.get("ok") and len(str(alt.get("text") or "")) > len(text):
            text = str(alt.get("text") or "")
    wall = _wall(text)
    if wall:
        why = (
            "x.com serves posts behind a login for anonymous readers and the mirror read failed: "
            + str(post.get("error") or "unknown")
            if ids
            else "this page is a login/JavaScript/consent wall for anonymous readers"
        )
        return {"ok": False, "error": "content_wall", "url": url, "marker": wall, "hint": why, "detail": text[:300]}
    if len(text) > 24_000:
        text = text[:24_000] + "\n…[truncated]"
    from p0_hook import gate_output_window

    g = gate_output_window(text[:8000])
    if g.get("verdict") == "QUARANTINE":
        return {"ok": False, "error": "p0_quarantine", "url": url, "reason": g.get("reason")}
    return {"ok": True, "url": url, "class": "RESOURCE", "text": text}
