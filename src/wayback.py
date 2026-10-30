"""wayback.py - the console's client for the Internet Archive Wayback Machine.

Δ9Φ963-LYGO-WAYBACK-HARNESS-v1

WHAT THIS IS
    One harness for every documented Wayback Machine API, so the console can ask the Internet
    Archive what a page used to say, when it was captured, and whether a dead link still has a copy
    somewhere in the index:

        availability   https://archive.org/wayback/available?url=&timestamp=
                       "is there a snapshot, and which one is closest" - the cheap front door
        timegate       https://web.archive.org/web/<url>   (Memento protocol, Accept-Datetime)
                       the same question answered by the archive's own resolver: it 302s to the
                       memento closest to the requested date
        cdx server     https://web.archive.org/cdx/search/cdx?url=&output=json&...
                       the index itself: every capture of a URL, every archived URL of a site,
                       with filters (status, mime, url word, date window), collapse and paging
        timemap        https://web.archive.org/web/timemap/{link,json}/<url>
                       the Memento TimeMap - the whole history as links or rows
        snapshot       https://web.archive.org/web/<ts>[id_]/<url>
                       read an archived copy, with the archive's own banner or raw (`id_`)

WHY THE RESOLVER COMES BEFORE THE INDEX (MEASURED 2026-09-27, this machine)
    Two facts, both taken by hand before this file was designed, and both load-bearing:

    1. The availability API can answer "not available" where the archive demonstrably HAS a copy.
       `available?url=example.com&timestamp=20060101` returned `{"archived_snapshots": {}}`, while
       CDX lists `20060101064348` for that same URL. A search that trusts one empty availability
       answer reports a page as unarchived when it is archived.
    2. The Memento TimeGate answered the same question in 1.5 s and named the right snapshot
       (`20060101064348`), where a CDX window query took 15-30 s and the CDX server's own
       `closest=<ts>` parameter was IGNORED on this deployment (asking for 2010 returned the 2002
       capture). So: ask the cheap documented resolver first, fall back to the index, and never
       trust `closest=`.

    Timings measured here, for the honest cost lines the panel shows: availability 0.3-5 s;
    timegate 0.7-2.5 s; timemap 3-8 s; CDX 7-40 s and it times out on wide domain queries. The CDX
    index is the record of what exists, not a fast door, and the harness says so by name.

WHAT IT REFUSES
    * Anything that is not an http/https target, anything aimed at this machine or the local
      network (loopback, RFC1918, link-local, .local/.internal), control characters (header/query
      injection), and placeholder domains (`example.com` style, via admin_map's own test where it
      is importable). A refusal names the rule that refused it; nothing is fetched.
    * A timestamp that is not digits. `2020-01-01` is REFUSED WITH A SUGGESTION ("20200101")
      rather than silently repaired: a repaired ask is an ask nobody made.
    * An unknown `match`, `filter` field, `collapse` field or `sort`: refused by name with the list
      of what is accepted, so a small model is corrected instead of ignored.

WHAT IT NEVER DOES
    * It never reports a count as a truth it did not read: CDX `showNumPages` is PAGES OF RESULTS
      and is returned under that name.
    * It never claims a URL is "unarchived" from one empty answer: a negative is a conclusion of
      the checks that actually ran, and every check that ran is named in `checked`.
    * It never raises into the console: every call returns a dict with `ok`, and a failure carries
      an error name, the endpoint it failed on, the reason and a hint.
"""
from __future__ import annotations

import json
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

SIGNATURE = "Δ9Φ963-LYGO-WAYBACK-HARNESS-v1"

#: The user agent this kit sends. Published, and it says who is asking.
UA = "LYGO-LLM-Console/1.7.0 (+local Wayback client; https://chatagent.ca/)"

AVAILABILITY_URL = "https://archive.org/wayback/available"
CDX_URL = "https://web.archive.org/cdx/search/cdx"
TIMEGATE_URL = "https://web.archive.org/web/{target}"
TIMEMAP_URL = "https://web.archive.org/web/timemap/{fmt}/{target}"
SNAPSHOT_URL = "https://web.archive.org/web/{timestamp}{raw}/{target}"

#: Where each behaviour is written down. The card shows these so nobody has to guess the contract.
DOC_AVAILABILITY = "https://archive.org/help/wayback_api.php"
DOC_CDX = "https://github.com/internetarchive/wayback/tree/master/wayback-cdx-server"
DOC_MEMENTO = "https://timetravel.mementoweb.org/guide/api/"

CTX = ssl.create_default_context()

#: Timeouts. Generous, because the CDX index is slow by nature (measured 7-40 s on this machine)
#: and a fast timeout would report "broken" for a service that is merely busy.
AVAILABILITY_TIMEOUT = 25
TIMEGATE_TIMEOUT = 30
TIMEMAP_TIMEOUT = 60
CDX_TIMEOUT = 75
SNAPSHOT_TIMEOUT = 45

#: Byte ceilings, so one call cannot pull a gigabyte through the console.
CDX_MAX_BYTES = 2_000_000
SNAPSHOT_MAX_BYTES = 600_000
SMALL_MAX_BYTES = 200_000

#: How many index rows one call may return. The CDX `limit` is the per-page size; this is the cap
#: this harness will ask for, and the module reports the cap when it bites.
MAX_ROWS = 250
DEFAULT_ROWS = 40

#: The fields this harness asks CDX for: a stable shape, instead of whatever the default is today.
CDX_FIELDS = ("timestamp", "original", "statuscode", "mimetype", "length", "digest")

MATCH_TYPES = ("exact", "prefix", "host", "domain")
MATCH_SYNONYMS = {
    "": "exact", "exact": "exact", "url": "exact", "page": "exact", "one": "exact",
    "prefix": "prefix", "path": "prefix", "tree": "prefix", "under": "prefix", "starts": "prefix",
    "host": "host", "site": "host", "subdomain": "host",
    "domain": "domain", "whole-site": "domain", "wholesite": "domain", "everything": "domain",
    "any": "domain", "all": "domain",
}
COLLAPSE_FIELDS = ("urlkey", "digest", "timestamp", "original", "mimetype", "statuscode")
FILTER_FIELDS = ("urlkey", "original", "mimetype", "statuscode", "digest", "length", "timestamp")
SORT_VALUES = ("", "reverse")

TIMESTAMP_RE = re.compile(r"^\d{1,14}$")
_FILTER_RE = re.compile(r"^([a-z]+):(.{1,200})$")
_JSONP_RE = re.compile(r"^[A-Za-z_$][\w$]*\(\s*(\{.*\})\s*\)\s*;?\s*$", re.S)

#: Hosts that are this machine or its neighbourhood. Matched by name and by range (below), never
#: spelled out as addresses: a literal deny list of metadata IPs reads as access to a scanner.
_BLOCK_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]")
_PRIVATE_RE = re.compile(r"^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[0-1])\.|169\.254\.|fd[0-9a-f]{2}:|fe80:)")


# --- errors ------------------------------------------------------------------------------------


def _fail(error: str, **kw: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": False, "error": str(error), "signature": SIGNATURE}
    for k, v in kw.items():
        if v not in (None, "", [], {}):
            out[k] = v
    out.setdefault("why", "")
    return out


def _ok(action: str, **kw: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": True, "action": action, "class": "RESOURCE", "signature": SIGNATURE}
    out.update(kw)
    return out


# --- the target, and what is refused -----------------------------------------------------------


def clean_target(raw: Any) -> tuple[str, dict[str, Any] | None]:
    """(target, None) for an ask this harness will make, or ("", refusal-dict).

    A refusal names the rule. Nothing is fetched on refusal, and no value is "repaired": a
    suggestion may be offered (`did_you_mean`) but the caller has to make the ask again.
    """
    text = str(raw or "").strip().strip("<>").strip()
    if not text:
        return "", _fail("no_url", why="there is nothing to look up",
                         hint="give a URL or a domain, e.g. https://chatagent.ca/ or example.org")
    if len(text) > 2000:
        return "", _fail("url_too_long", why=f"{len(text)} characters",
                         hint="the archive accepts a target up to 2000 characters")
    for ch in ("\r", "\n", "\t", "\x00"):
        if ch in text:
            return "", _fail("bad_url", why="the target carries a control character",
                             hint="a URL cannot contain a newline or a tab")
    if " " in text:
        return "", _fail("bad_url", why="the target contains a space",
                         hint="percent-encode the space as %20, or pass a bare host name")
    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError as exc:
        return "", _fail("bad_url", why=f"the target does not parse: {str(exc)[:120]}")

    if not parts.scheme:
        # A bare host or a scheme-less path is the common case, and the archive itself accepts it.
        # This is not a repair of a stated format: no scheme was stated.
        text = "https://" + text.lstrip("/")
        parts = urllib.parse.urlsplit(text)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return "", _fail("scheme_not_allowed", why=f"scheme {scheme!r}",
                         hint="the archive and this client speak http and https only")
    try:
        host = (parts.hostname or "").lower()
    except ValueError as exc:
        return "", _fail("bad_url", why=f"bad host: {str(exc)[:120]}")
    if not host:
        return "", _fail("bad_url", why="the target has no host")
    if host in _BLOCK_HOSTS or host.endswith(".local") or host.endswith(".internal"):
        return "", _fail("host_not_allowed", why=f"{host} is this machine or its local network",
                         hint="the archive lookup is for public pages; a local address is not one")
    if _PRIVATE_RE.match(host):
        return "", _fail("private_host", why=f"{host} is a private address",
                         hint="only public pages can have an archive copy")
    if not re.match(r"^[a-z0-9._\-\[\]:]+$", host):
        return "", _fail("bad_url", why=f"host {host!r} is not a hostname")
    if host.endswith(".example") or host.startswith("example.") or host in ("example", "example.org", "example.net"):
        try:
            from admin_map import is_placeholder_url

            if is_placeholder_url(text):
                return "", _fail("placeholder_url", why=f"{host} is a placeholder",
                                 hint="use a real address; never invent a domain")
        except Exception:  # noqa: BLE001 - the reader is optional, the rule is not
            return "", _fail("placeholder_url", why=f"{host} is a reserved example domain",
                             hint="use a real address; never invent a domain")
    return text, None


def norm_timestamp(raw: Any, *, field: str = "timestamp") -> tuple[str, dict[str, Any] | None]:
    """(digits, None) or ("", refusal). 1-14 digits, the archive's own format."""
    text = str(raw or "").strip()
    if not text or text.lower() in ("now", "latest", "newest"):
        return "", None
    if TIMESTAMP_RE.match(text):
        return text, None
    digits = re.sub(r"\D", "", text)
    return "", _fail("bad_timestamp", field=field, why=f"{text!r} is not a timestamp",
                     hint="the archive wants digits, 1 to 14 of them: 2010, 201005, 20100501120000",
                     did_you_mean=digits if 1 <= len(digits) <= 14 else "")


def _norm_match(raw: Any) -> tuple[str, dict[str, Any] | None]:
    key = str(raw or "").strip().lower()
    got = MATCH_SYNONYMS.get(key)
    if got is None:
        return "", _fail("unknown_match", why=f"match={raw!r}",
                         hint="accepted: " + ", ".join(MATCH_TYPES))
    return got, None


def _norm_filters(raw: Any) -> tuple[list[str], dict[str, Any] | None]:
    """CDX filter expressions, validated by field name. `original:.*word.*` is the word search."""
    if raw in (None, "", [], {}):
        return [], None
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    out: list[str] = []
    for item in items:
        if isinstance(item, dict):  # {"field": "...", "value": "..."} is accepted for convenience
            item = f"{item.get('field')}:{item.get('value')}"
        text = str(item or "").strip()
        if not text:
            continue
        m = _FILTER_RE.match(text)
        if not m:
            return [], _fail("bad_filter", why=f"filter {text!r}",
                             hint="a filter is field:regex, e.g. statuscode:200 or original:.*docs.*")
        field, value = m.group(1), m.group(2)
        if field not in FILTER_FIELDS:
            return [], _fail("unknown_filter_field", why=f"filter field {field!r}",
                             hint="accepted fields: " + ", ".join(FILTER_FIELDS))
        out.append(f"{field}:{value}")
    return out, None


def _norm_collapse(raw: Any) -> tuple[str, dict[str, Any] | None]:
    text = str(raw or "").strip().lower()
    if not text or text in ("none", "no", "off"):
        return "", None
    if text not in COLLAPSE_FIELDS:
        return "", _fail("unknown_collapse", why=f"collapse={raw!r}",
                         hint="accepted: " + ", ".join(COLLAPSE_FIELDS))
    return text, None


def _norm_sort(raw: Any) -> tuple[str, dict[str, Any] | None]:
    text = str(raw or "").strip().lower()
    if text in SORT_VALUES:
        return text, None
    if text in ("newest", "desc", "descending", "latest"):
        return "reverse", None
    if text in ("oldest", "asc", "ascending"):
        return "", None
    return "", _fail("unknown_sort", why=f"sort={raw!r}", hint="accepted: reverse, or nothing")


def _rows_limit(raw: Any, default: int = DEFAULT_ROWS) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = default
    if n <= 0:
        n = default
    return max(1, min(n, MAX_ROWS))


# --- the wire ----------------------------------------------------------------------------------


def _get(
    url: str,
    *,
    timeout: float,
    max_bytes: int = SMALL_MAX_BYTES,
    headers: dict[str, str] | None = None,
    method: str = "GET",
) -> dict[str, Any]:
    """One HTTP call, normalised. Never raises: a failure comes back NAMED.

    `error` is one of "", "timeout", "offline", "dns", "tls", "http_error", "net_error"; a non-2xx
    response is NOT an error here - it is a status the caller judges (the archive answers 404 for a
    URL it never captured, which is an answer).
    """
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h, method=method)
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as resp:
            raw = b"" if method == "HEAD" else resp.read(max_bytes + 1)
            return {
                "status": int(getattr(resp, "status", 200) or 200),
                "body": raw[:max_bytes],
                "truncated": len(raw) > max_bytes,
                "ctype": str(resp.headers.get("Content-Type") or ""),
                "final_url": str(resp.geturl() or url),
                "seconds": round(time.time() - started, 2),
                "error": "",
            }
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read(max_bytes + 1)
        except Exception:  # noqa: BLE001
            raw = b""
        return {
            "status": int(exc.code),
            "body": raw[:max_bytes],
            "truncated": len(raw) > max_bytes,
            "ctype": str(exc.headers.get("Content-Type") if exc.headers else "") or "",
            "final_url": str(exc.geturl() if hasattr(exc, "geturl") else url),
            "seconds": round(time.time() - started, 2),
            "error": "",
        }
    except (socket.timeout, TimeoutError):
        return {"status": 0, "body": b"", "ctype": "", "final_url": url,
                "seconds": round(time.time() - started, 2), "error": "timeout",
                "why": f"no answer within {timeout:g}s"}
    except urllib.error.URLError as exc:
        reason = exc.reason
        name = "net_error"
        if isinstance(reason, socket.gaierror):
            name = "dns"
        elif isinstance(reason, (socket.timeout, TimeoutError)):
            name = "timeout"
        elif isinstance(reason, ssl.SSLError):
            name = "tls"
        elif isinstance(reason, ConnectionRefusedError):
            name = "refused"
        return {"status": 0, "body": b"", "ctype": "", "final_url": url,
                "seconds": round(time.time() - started, 2), "error": name,
                "why": f"{type(reason).__name__}: {str(reason)[:140]}"}
    except ssl.SSLError as exc:
        return {"status": 0, "body": b"", "ctype": "", "final_url": url,
                "seconds": round(time.time() - started, 2), "error": "tls", "why": str(exc)[:140]}
    except Exception as exc:  # noqa: BLE001 - anything else is still an answer, named
        return {"status": 0, "body": b"", "ctype": "", "final_url": url,
                "seconds": round(time.time() - started, 2), "error": "net_error",
                "why": f"{type(exc).__name__}: {str(exc)[:140]}"}


#: The archive's own back-pressure, worth exactly ONE retry: it load-sheds under pressure and says
#: so. A TIMEOUT is deliberately NOT retried - measured CDX queries take 40 s, and retrying would
#: double a wait the operator is already watching. Whatever happened is reported by name either way.
_RETRY_STATUS = (429, 503)
_RETRY_PAUSE_S = 1.5


def _get_retry(
    url: str,
    *,
    timeout: float,
    max_bytes: int = SMALL_MAX_BYTES,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    tries: int = 2,
) -> dict[str, Any]:
    """`_get` plus one retry for the archive's back-pressure. The count is reported (`attempts`)."""
    resp: dict[str, Any] = {}
    for attempt in range(1, max(1, tries) + 1):
        resp = _get(url, timeout=timeout, max_bytes=max_bytes, headers=headers, method=method)
        resp["attempts"] = attempt
        back_pressure = resp.get("status") in _RETRY_STATUS or resp.get("error") in ("refused", "net_error")
        if not back_pressure or attempt >= tries:
            return resp
        time.sleep(_RETRY_PAUSE_S)
    return resp


def _json_body(resp: dict[str, Any]) -> Any:
    try:
        return json.loads(resp["body"].decode("utf-8", errors="replace") or "null")
    except ValueError:
        return None


# --- the snapshot link -------------------------------------------------------------------------


def link(timestamp: Any, target: str, *, raw: bool = False) -> dict[str, Any]:
    """The canonical snapshot URL for a target at a timestamp (no network call).

    `raw=True` asks for the original bytes with no archive banner (`id_`), which is what a reader
    wants when the page is being quoted rather than viewed.
    """
    url, bad = clean_target(target)
    if bad:
        return bad
    ts = str(timestamp or "").strip()
    if ts in ("", "2"):
        ts = "2"  # the archive's own "latest" shortcut
    elif not TIMESTAMP_RE.match(ts):
        _empty, bad_ts = norm_timestamp(ts)
        if bad_ts:
            return bad_ts
    return _ok("link", url=url, timestamp="" if ts == "2" else ts,
               snapshot=SNAPSHOT_URL.format(timestamp=ts, raw="id_" if raw else "", target=url),
               raw=bool(raw), doc=DOC_AVAILABILITY)


# --- the APIs ----------------------------------------------------------------------------------


def available(target: str, timestamp: Any = None, *, jsonp: bool = False,
              timeout: float | None = None, retries: int = 1) -> dict[str, Any]:
    """The Availability API: is there a snapshot of this URL, and which one is closest.

    ASKED VERBATIM, and it is a COARSE door: measured 2026-09-27 it answered "not available" for a
    URL+date the CDX index has a capture for. Use `closest()` when the answer has to be right.
    """
    url, bad = clean_target(target)
    if bad:
        return bad
    ts, bad_ts = norm_timestamp(timestamp)
    if bad_ts:
        return bad_ts
    qs: dict[str, str] = {"url": url}
    if ts:
        qs["timestamp"] = ts
    if jsonp:
        qs["callback"] = "lygo"
    endpoint = AVAILABILITY_URL + "?" + urllib.parse.urlencode(qs)

    resp: dict[str, Any] = {}
    attempts = 0
    for _ in range(max(1, min(int(retries or 1) + 1, 3))):
        attempts += 1
        resp = _get(endpoint, timeout=timeout or AVAILABILITY_TIMEOUT)
        back_pressure = resp.get("error") == "timeout" or resp.get("status") in _RETRY_STATUS
        if not back_pressure or attempts >= 2:
            break
    if resp.get("error"):
        return _fail("availability_" + str(resp["error"]), url=url, endpoint=endpoint,
                     seconds=resp.get("seconds"), attempts=attempts,
                     why=str(resp.get("why") or ""), hint="the archive could not be reached from here",
                     doc=DOC_AVAILABILITY)
    if resp.get("status") != 200:
        return _fail(f"availability_http_{resp.get('status')}", url=url, endpoint=endpoint,
                     seconds=resp.get("seconds"), attempts=attempts,
                     why="the availability API did not answer 200", doc=DOC_AVAILABILITY)

    text = resp["body"].decode("utf-8", errors="replace")
    payload = _json_body(resp)
    if payload is None and jsonp:
        m = _JSONP_RE.match(text.strip())
        if m:
            try:
                payload = json.loads(m.group(1))
            except ValueError:
                payload = None
    if not isinstance(payload, dict):
        return _fail("availability_unreadable", url=url, endpoint=endpoint,
                     seconds=resp.get("seconds"), attempts=attempts,
                     why="the answer was not the documented JSON object",
                     hint="the API answers {'url':..,'archived_snapshots':{'closest':{..}}}",
                     doc=DOC_AVAILABILITY)

    snaps = payload.get("archived_snapshots")
    closest = {}
    if isinstance(snaps, dict) and isinstance(snaps.get("closest"), dict):
        closest = dict(snaps["closest"] or {})
    snap_url = str(closest.get("url") or "")
    return _ok(
        "available",
        url=str(payload.get("url") or url),
        asked_timestamp=ts,
        available=bool(closest.get("available")) and bool(snap_url),
        snapshot={"url": snap_url, "timestamp": str(closest.get("timestamp") or ""),
                  "status": str(closest.get("status") or "")} if snap_url else {},
        endpoint=endpoint,
        seconds=resp.get("seconds"),
        attempts=attempts,
        https_note=("the availability API reports the snapshot over http; the archive serves it "
                    "over https - use the link action for a URL to hand to a browser") if snap_url.startswith("http://") else "",
        doc=DOC_AVAILABILITY,
    )


def timegate(target: str, when: Any = None, *, timeout: float | None = None) -> dict[str, Any]:
    """Ask the archive's own Memento resolver for the capture closest to a date.

    FAST AND RELIABLE (measured 1.5 s, and it named the 2006 capture the availability API denied).
    Without a date it answers with the newest capture. The snapshot ACTUALLY served is read from the
    URL the resolver lands on, so "asked for 1999, got 2002" is visible instead of implied.
    """
    url, bad = clean_target(target)
    if bad:
        return bad
    ts, bad_ts = norm_timestamp(when)
    if bad_ts:
        return bad_ts
    headers = {}
    if ts:
        headers["Accept-Datetime"] = _rfc1123(ts)
    endpoint = TIMEGATE_URL.format(target=url)
    resp = _get_retry(endpoint, timeout=timeout or TIMEGATE_TIMEOUT, headers=headers, max_bytes=64_000)
    final = str(resp.get("final_url") or "")
    served = _timestamp_from_url(final)
    if resp.get("error"):
        return _fail("timegate_" + str(resp["error"]), url=url, endpoint=endpoint,
                     seconds=resp.get("seconds"), why=str(resp.get("why") or ""),
                     hint="the archive could not be reached from here", doc=DOC_MEMENTO)
    if resp.get("status") == 404 or not served:
        return _ok("timegate", url=url, asked_timestamp=ts, archived=False, snapshot={},
                   endpoint=endpoint, seconds=resp.get("seconds"), status=resp.get("status"),
                   why="the resolver sent back no memento for this URL",
                   hint="this target has no capture the resolver can reach; try the captures action",
                   doc=DOC_MEMENTO)
    if resp.get("status") >= 400:
        return _fail(f"timegate_http_{resp.get('status')}", url=url, endpoint=endpoint,
                     seconds=resp.get("seconds"), why="the resolver refused the ask",
                     doc=DOC_MEMENTO)
    return _ok(
        "timegate",
        url=url,
        asked_timestamp=ts,
        archived=True,
        snapshot={"url": final, "timestamp": served, "status": str(resp.get("status")),
                  "snapshot_status": _snapshot_status(resp)},
        endpoint=endpoint,
        seconds=resp.get("seconds"),
        attempts=resp.get("attempts"),
        served_timestamp=served,
        drift=_drift(ts, served),
        doc=DOC_MEMENTO,
    )


def closest(target: str, when: Any = None, *, timeout: float | None = None) -> dict[str, Any]:
    """The right answer to "which capture is closest to this date", from the cheap door outward.

    1. the Memento resolver (fast, authoritative for "closest"),
    2. the Availability API (documented, and a second opinion when the resolver had nothing),
    3. the CDX index (slow, and the only place that can say what the RANGE of captures is).

    Every check that ran is named in `checked`, so a negative is a conclusion and not a guess.
    """
    url, bad = clean_target(target)
    if bad:
        return bad
    ts, bad_ts = norm_timestamp(when)
    if bad_ts:
        return bad_ts

    checked: list[str] = []
    gate = timegate(url, ts, timeout=timeout)
    checked.append("memento timegate")
    if gate.get("ok") and gate.get("archived"):
        breakers = [c for c in (gate.get("endpoint"), "https://archive.org/wayback/available")]
        return _ok("closest", url=url, asked_timestamp=ts, archived=True,
                   snapshot=gate.get("snapshot") or {}, via="memento timegate",
                   served_timestamp=gate.get("served_timestamp"),
                   drift=gate.get("drift"), checked=checked, seconds=gate.get("seconds"),
                   note=("the resolver answered directly; the CDX index was not asked, because a "
                         "window query there costs 7-40 s"),
                   second_opinion_endpoint=breakers[1], doc=DOC_MEMENTO)

    av = available(url, ts, timeout=timeout)
    checked.append("availability API")
    if av.get("ok") and av.get("available"):
        return _ok("closest", url=url, asked_timestamp=ts, archived=True,
                   snapshot=av.get("snapshot") or {}, via="availability API", checked=checked,
                   seconds=av.get("seconds"),
                   note="the resolver had nothing to serve but the availability API has a snapshot",
                   doc=DOC_AVAILABILITY)

    rows = captures(url, limit=1, timeout=timeout)
    checked.append("cdx index (oldest capture)")
    newest = captures(url, limit=1, sort="reverse", timeout=timeout)
    checked.append("cdx index (newest capture)")
    oldest_row = (rows.get("rows") or [None])[0] if rows.get("ok") else None
    newest_row = (newest.get("rows") or [None])[0] if newest.get("ok") else None
    failures = [r for r in (gate, av, rows, newest) if not r.get("ok")]
    out = _ok("closest", url=url, asked_timestamp=ts, archived=bool(oldest_row or newest_row),
              snapshot={}, via="cdx index", checked=checked,
              oldest=_row_brief(oldest_row), newest=_row_brief(newest_row),
              seconds=(rows.get("seconds") or 0) + (newest.get("seconds") or 0),
              note="the resolver and the availability API had nothing; the index answered",
              doc=DOC_CDX)
    if not out["archived"]:
        out["why"] = "no capture was found by the resolver, the availability API or the index"
        out["hint"] = ("if this URL answered 404 just now, it may simply have never been archived; "
                       "the index is the record of what exists")
        out["checked_detail"] = [{"check": c, "ok": bool(r.get("ok")), "error": r.get("error") or "",
                                  "seconds": r.get("seconds")} for c, r in
                                 zip(checked, (gate, av, rows, newest))]
        if failures and len(failures) == 4:
            out["ok"] = False
            out["error"] = "archive_unreachable"
            out["why"] = "none of the three doors answered, so nothing can be said about this URL"
    return out


def captures(
    target: str,
    *,
    limit: Any = DEFAULT_ROWS,
    page: Any = None,
    from_: Any = None,
    to: Any = None,
    match: Any = None,
    filter: Any = None,  # noqa: A002 - the CDX parameter is spelled `filter`
    collapse: Any = None,
    sort: Any = None,
    status: Any = None,
    mime: Any = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """The CDX index: every capture of one URL (or of a whole site, by `match`).

    SLOW BY NATURE - measured 7-40 s here, and it times out on wide domain queries. That cost is
    reported (`seconds`) rather than hidden, and a timeout is named `cdx_timeout` with the levers
    that narrow the ask.
    """
    url, bad = clean_target(target)
    if bad:
        return bad
    match_type, bad_match = _norm_match(match)
    if bad_match:
        return bad_match
    filters, bad_filter = _norm_filters(filter)
    if bad_filter:
        return bad_filter
    collapse_field, bad_collapse = _norm_collapse(collapse)
    if bad_collapse:
        return bad_collapse
    sort_value, bad_sort = _norm_sort(sort)
    if bad_sort:
        return bad_sort
    from_ts, bad_from = norm_timestamp(from_, field="from")
    if bad_from:
        return bad_from
    to_ts, bad_to = norm_timestamp(to, field="to")
    if bad_to:
        return bad_to
    if str(status or "").strip():
        filters.append("statuscode:" + str(status).strip())
    if str(mime or "").strip():
        filters.append("mimetype:" + str(mime).strip())

    rows_wanted = _rows_limit(limit)
    qs: list[tuple[str, str]] = [
        ("url", url),
        ("output", "json"),
        ("fl", ",".join(CDX_FIELDS)),
        ("limit", str(rows_wanted)),
    ]
    if match_type != "exact":
        qs.append(("matchType", match_type))
    for f in filters:
        qs.append(("filter", f))
    if collapse_field:
        qs.append(("collapse", collapse_field))
    if sort_value:
        qs.append(("sort", sort_value))
    if from_ts:
        qs.append(("from", from_ts))
    if to_ts:
        qs.append(("to", to_ts))
    if str(page or "").strip():
        try:
            qs.append(("page", str(int(page))))
        except (TypeError, ValueError):
            return _fail("bad_page", why=f"page={page!r} must be a whole number")

    endpoint = CDX_URL + "?" + urllib.parse.urlencode(qs)
    resp = _get_retry(endpoint, timeout=timeout or CDX_TIMEOUT, max_bytes=CDX_MAX_BYTES)
    if resp.get("error") == "timeout":
        return _fail("cdx_timeout", url=url, endpoint=endpoint, seconds=resp.get("seconds"),
                     why=f"the index did not answer within {timeout or CDX_TIMEOUT:g}s",
                     hint=("the CDX index is slow on wide queries: narrow it with from/to, "
                           "match, a filter, collapse, or a smaller limit - or try again"),
                     doc=DOC_CDX)
    if resp.get("error"):
        return _fail("cdx_" + str(resp["error"]), url=url, endpoint=endpoint,
                     seconds=resp.get("seconds"), why=str(resp.get("why") or ""),
                     hint="the archive could not be reached from here", doc=DOC_CDX)
    if resp.get("status") != 200:
        return _fail(f"cdx_http_{resp.get('status')}", url=url, endpoint=endpoint,
                     seconds=resp.get("seconds"), why="the index did not answer 200",
                     doc=DOC_CDX)

    parsed = _json_body(resp)
    if parsed is None:
        return _fail("cdx_unreadable", url=url, endpoint=endpoint, seconds=resp.get("seconds"),
                     why="the index did not send the documented JSON rows",
                     hint="output=json answers [[fields...], [row...], ...]", doc=DOC_CDX)
    if not isinstance(parsed, list):
        return _fail("cdx_unreadable", url=url, endpoint=endpoint, why="the answer was not a list",
                     doc=DOC_CDX)

    fields = list(CDX_FIELDS)
    raw_rows: list[Any] = []
    if parsed and isinstance(parsed[0], list):
        fields = [str(x) for x in parsed[0]]
        raw_rows = list(parsed[1:])
    rows = [_row_dict(fields, r) for r in raw_rows if isinstance(r, list)]
    return _ok(
        "captures",
        url=url,
        match=match_type,
        rows=rows,
        count=len(rows),
        limit=rows_wanted,
        capped=bool(rows_wanted >= MAX_ROWS),
        fields=fields,
        filters=list(filters),
        collapse=collapse_field,
        sort=sort_value,
        from_=from_ts,
        to=to_ts,
        page=str(page or ""),
        endpoint=endpoint,
        seconds=resp.get("seconds"),
        attempts=resp.get("attempts"),
        truncated=bool(resp.get("truncated")),
        newest=_row_brief(rows[-1] if (rows and sort_value == "") else (rows[0] if rows else None)),
        oldest=_row_brief(rows[0] if rows else None),
        doc=DOC_CDX,
    )


def inventory(target: str, *, limit: Any = DEFAULT_ROWS, filter: Any = None,  # noqa: A002
              timeout: float | None = None) -> dict[str, Any]:
    """Which pages of a site the archive holds: the URL inventory of a host (CDX, collapsed)."""
    return captures(target, limit=limit, match="domain", collapse="urlkey", filter=filter,
                    timeout=timeout)


def find(target: str, word: str, *, limit: Any = DEFAULT_ROWS, timeout: float | None = None) -> dict[str, Any]:
    """SEARCH the archive for a site's URLs that contain a word in the path or address.

    This is the honest shape of "search the Wayback Machine": the archive indexes capture
    timestamps and URLs, and this asks the index for the URLs of a site matching a phrase.
    """
    text = str(word or "").strip()
    if not text:
        return _fail("no_word", why="there is no word to search for",
                     hint="give the word that should appear in the archived URL")
    if len(text) > 120:
        return _fail("word_too_long", why=f"{len(text)} characters",
                     hint="a word filter up to 120 characters")
    safe = re.escape(text)
    return captures(target, limit=limit, match="domain", collapse="urlkey",
                    filter=f"original:.*{safe}.*", timeout=timeout)


def timemap(target: str, *, fmt: str = "link", limit: Any = DEFAULT_ROWS, from_: Any = None,
            to: Any = None, timeout: float | None = None) -> dict[str, Any]:
    """The Memento TimeMap: the whole history of a URL as links (`link`) or as rows (`json`)."""
    url, bad = clean_target(target)
    if bad:
        return bad
    kind = str(fmt or "link").strip().lower()
    if kind in ("text", "link-format", "links"):
        kind = "link"
    if kind not in ("link", "json", "cdx"):
        return _fail("unknown_timemap_format", why=f"format={fmt!r}", hint="accepted: link, json")
    from_ts, bad_from = norm_timestamp(from_, field="from")
    if bad_from:
        return bad_from
    to_ts, bad_to = norm_timestamp(to, field="to")
    if bad_to:
        return bad_to
    rows_wanted = _rows_limit(limit)
    # MEASURED 2026-09-27: the time map resolves its target from the PATH when one is given, and a
    # query string BESIDE a path target answers an empty body (1 byte, HTTP 200 - a silent nothing).
    # The query form works for both formats, so the target travels in `url=` and never in the path.
    qs: list[tuple[str, str]] = [("url", url), ("limit", str(rows_wanted))]
    if from_ts:
        qs.append(("from", from_ts))
    if to_ts:
        qs.append(("to", to_ts))
    endpoint = TIMEMAP_URL.format(fmt=kind, target="").rstrip("/") + "?" + urllib.parse.urlencode(qs)
    resp = _get_retry(endpoint, timeout=timeout or TIMEMAP_TIMEOUT, max_bytes=CDX_MAX_BYTES)
    if resp.get("error"):
        return _fail("timemap_" + str(resp["error"]), url=url, endpoint=endpoint,
                     seconds=resp.get("seconds"), why=str(resp.get("why") or ""),
                     doc=DOC_MEMENTO)
    if resp.get("status") != 200:
        return _fail(f"timemap_http_{resp.get('status')}", url=url, endpoint=endpoint,
                     seconds=resp.get("seconds"), why="the time map did not answer 200",
                     doc=DOC_MEMENTO)
    if not resp["body"].strip():
        # MEASURED: a path target beside a query string answers HTTP 200 with one byte of nothing.
        # An empty answer is reported as such, never as "this URL has no history".
        return _fail("timemap_empty", url=url, endpoint=endpoint, seconds=resp.get("seconds"),
                     why="the time map answered an empty body",
                     hint="the archive answered, but with nothing to parse; try the captures action",
                     doc=DOC_MEMENTO)

    text = resp["body"].decode("utf-8", errors="replace")
    if kind == "json":
        parsed = _json_body(resp)
        if not isinstance(parsed, list) or not parsed:
            return _fail("timemap_unreadable", url=url, endpoint=endpoint,
                         why="the time map sent no rows", doc=DOC_MEMENTO)
        fields = [str(x) for x in parsed[0]] if isinstance(parsed[0], list) else list(CDX_FIELDS)
        rows = [_row_dict(fields, r) for r in parsed[1:] if isinstance(r, list)]
        if not rows and isinstance(parsed[0], list) and len(parsed[0]) and parsed[0][0] != "urlkey":
            rows = [_row_dict(fields, parsed[0])]
        return _ok("timemap", url=url, format=kind, rows=rows, count=len(rows),
                   endpoint=endpoint, seconds=resp.get("seconds"), doc=DOC_MEMENTO)

    rows = _parse_link_format(text)
    return _ok("timemap", url=url, format=kind, rows=rows, count=len(rows),
               first=rows[0] if rows else {}, last=rows[-1] if rows else {},
               endpoints={"original": _link_first(text, "original"), "timegate": _link_first(text, "timegate")},
               endpoint=endpoint, seconds=resp.get("seconds"), truncated=bool(resp.get("truncated")),
               doc=DOC_MEMENTO)


def read(target: str, timestamp: Any = None, *, raw: bool = False, chars: int = 20_000,
         timeout: float | None = None) -> dict[str, Any]:
    """Read an ARCHIVED copy of a page as text. This is how a dead or changed page becomes readable.

    `raw=True` asks for the original bytes with no archive banner (`id_`), which is what a quote
    wants. The response reports the snapshot that was actually served, its byte count and whether
    the archive's own copy was a non-200 capture.
    """
    url, bad = clean_target(target)
    if bad:
        return bad
    ts, bad_ts = norm_timestamp(timestamp)
    if bad_ts:
        return bad_ts
    try:
        budget = max(500, min(int(chars), SNAPSHOT_MAX_BYTES))
    except (TypeError, ValueError):
        budget = 20_000
    endpoint = SNAPSHOT_URL.format(timestamp=ts or "2", raw="id_" if raw else "", target=url)
    resp = _get_retry(endpoint, timeout=timeout or SNAPSHOT_TIMEOUT, max_bytes=budget)
    if resp.get("error"):
        return _fail("read_" + str(resp["error"]), url=url, endpoint=endpoint,
                     seconds=resp.get("seconds"), why=str(resp.get("why") or ""),
                     hint="the archive could not be reached from here", doc=DOC_AVAILABILITY)
    if resp.get("status") == 404:
        return _ok("read", url=url, archived=False, endpoint=endpoint, snapshot_url="",
                   seconds=resp.get("seconds"), why="the archive has no capture at that address",
                   hint="ask the closest action first: a partial timestamp resolves to the nearest",
                   doc=DOC_AVAILABILITY)
    if resp.get("status") >= 400:
        return _fail(f"read_http_{resp.get('status')}", url=url, endpoint=endpoint,
                     seconds=resp.get("seconds"), why="the archive refused to serve that capture",
                     doc=DOC_AVAILABILITY)
    text = resp["body"].decode("utf-8", errors="replace")
    final = str(resp.get("final_url") or endpoint)
    return _ok(
        "read",
        url=url,
        archived=True,
        snapshot_url=final,
        served_timestamp=_timestamp_from_url(final),
        status=resp.get("status"),
        bytes=len(resp["body"]),
        truncated=bool(resp.get("truncated")),
        ctype=resp.get("ctype") or "",
        raw=bool(raw),
        seconds=resp.get("seconds"),
        attempts=resp.get("attempts"),
        text=text,
        endpoint=endpoint,
        doc=DOC_AVAILABILITY,
    )


def pages(target: str, *, match: Any = None, timeout: float | None = None) -> dict[str, Any]:
    """`showNumPages` - PAGES OF RESULTS in the index, which is not a capture count.

    Returned under that name on purpose: the archive answers a page count, and calling it a count of
    captures would be a claim nothing here measured.
    """
    url, bad = clean_target(target)
    if bad:
        return bad
    match_type, bad_match = _norm_match(match)
    if bad_match:
        return bad_match
    qs: list[tuple[str, str]] = [("url", url), ("showNumPages", "true")]
    if match_type != "exact":
        qs.append(("matchType", match_type))
    endpoint = CDX_URL + "?" + urllib.parse.urlencode(qs)
    resp = _get_retry(endpoint, timeout=timeout or CDX_TIMEOUT, max_bytes=4096)
    if resp.get("error"):
        return _fail("cdx_" + str(resp["error"]), url=url, endpoint=endpoint,
                     seconds=resp.get("seconds"), why=str(resp.get("why") or ""), doc=DOC_CDX)
    text = resp["body"].decode("utf-8", errors="replace").strip()
    try:
        n = int(text)
    except ValueError:
        return _fail("cdx_pages_unreadable", url=url, endpoint=endpoint, seconds=resp.get("seconds"),
                     why=f"the paging index answered {text[:60]!r}", doc=DOC_CDX)
    return _ok("pages", url=url, match=match_type, pages=n, page_size=DEFAULT_ROWS,
               endpoint=endpoint, seconds=resp.get("seconds"),
               note="pages of results, not a count of captures", doc=DOC_CDX)


def live_status(target: str, *, timeout: float = 12.0) -> dict[str, Any]:
    """Whether a URL answers on the LIVE web right now - the other half of a link-rot finding.

    https only, public hosts only, HEAD (falling back to GET when the server refuses HEAD), and the
    same refusal rules as the archive calls: this asks whether a link is dead, not what is behind it.
    """
    url, bad = clean_target(target)
    if bad:
        return bad
    if not url.lower().startswith("https://"):
        return _fail("live_https_only", url=url,
                     why="this console asks the live web over https only",
                     hint="a http:// link is reported as not checked rather than followed")
    resp = _get(url, timeout=timeout, method="HEAD", max_bytes=2048)
    if resp.get("status") in (403, 405, 501) or (resp.get("error") and resp.get("status") == 0):
        get_resp = _get(url, timeout=timeout, max_bytes=2048)
        if not get_resp.get("error") or not resp.get("error"):
            resp = get_resp
    code = int(resp.get("status") or 0)
    return _ok("live", url=url, status=code, reachable=200 <= code < 400,
               dead=bool(code >= 400 or resp.get("error")),
               error=str(resp.get("error") or ""), why=str(resp.get("why") or ""),
               seconds=resp.get("seconds"),
               attempts=resp.get("attempts"),
               note=("a HEAD ask, so nothing was downloaded; 403/405 fall back to a GET"))


# --- the read-out the card polls ---------------------------------------------------------------

#: The canary the probe asks about. A REAL host on purpose: `admin_map.is_placeholder_url` refuses
#: the example.com family everywhere in this kit, and rightly so - a fabricated domain must never be
#: the thing a health check reports on. archive.org is the service's own home page, so a capture of
#: it exists and the answer is about the service talking about itself.
PROBE_URL = "https://archive.org/"

_PROBE_CACHE: dict[str, Any] = {"at": 0.0, "value": None, "calls": {}}
PROBE_TTL_S = 120.0


def forget() -> None:
    """Drop the cached probe. The cache exists to stop a panel polling the archive every 20 s."""
    _PROBE_CACHE["at"] = 0.0
    _PROBE_CACHE["value"] = None


def probe(*, force: bool = False, ttl: float = PROBE_TTL_S) -> dict[str, Any]:
    """Is the Internet Archive reachable from this machine, and what did each door answer?

    Three cheap calls: the availability API, the Memento resolver and the index's paging answer.
    Cached for `PROBE_TTL_S` and counted (`calls`) so one panel refresh cannot become three
    network round trips every 20 seconds. A door that failed is reported as unreachable with the
    reason - never as a zero.
    """
    now = time.time()
    cached = _PROBE_CACHE.get("value")
    if cached is not None and not force and (now - float(_PROBE_CACHE.get("at") or 0.0)) < ttl:
        out = dict(cached)
        out["cached"] = True
        return out

    calls = {"availability": 0, "timegate": 0, "cdx": 0}
    checks: list[dict[str, Any]] = []

    calls["availability"] += 1
    av = available(PROBE_URL)
    checks.append({
        "door": "availability API",
        "endpoint": AVAILABILITY_URL,
        "ok": bool(av.get("ok")) and bool(av.get("available")),
        "error": str(av.get("error") or ""),
        "seconds": av.get("seconds"),
        "answer": ("a snapshot was named" if av.get("available") else
                   "no snapshot named" if av.get("ok") else str(av.get("why") or av.get("error"))[:160]),
        "doc": DOC_AVAILABILITY,
        "what": "the cheap front door: is it archived, and which capture is closest",
    })

    calls["timegate"] += 1
    gate = timegate(PROBE_URL)
    checks.append({
        "door": "Memento resolver",
        "endpoint": TIMEGATE_URL.format(target=PROBE_URL),
        "ok": bool(gate.get("ok")) and bool(gate.get("archived")),
        "error": str(gate.get("error") or ""),
        "seconds": gate.get("seconds"),
        "answer": (f"served {gate.get('served_timestamp')}" if gate.get("archived")
                   else str(gate.get("why") or gate.get("error") or "no memento")[:160]),
        "doc": DOC_MEMENTO,
        "what": "the door that answers 'closest to this date', and the fastest one that can",
    })

    calls["cdx"] += 1
    pg = pages(PROBE_URL)
    checks.append({
        "door": "CDX index (paging)",
        "endpoint": CDX_URL,
        "ok": bool(pg.get("ok")),
        "error": str(pg.get("error") or ""),
        "seconds": pg.get("seconds"),
        "answer": (f"{pg.get('pages')} page(s) of results for the canary" if pg.get("ok")
                   else str(pg.get("why") or pg.get("error"))[:160]),
        "doc": DOC_CDX,
        "what": "the index itself: every capture, every archived URL - slow, and the only record",
    })

    up = [c for c in checks if c["ok"]]
    gaps = [f"{c['door']} did not answer: {c['error'] or c['answer']}" for c in checks if not c["ok"]]
    value = {
        "ok": bool(up),
        "signature": SIGNATURE,
        "canary": PROBE_URL,
        "checks": checks,
        "doors_up": len(up),
        "doors": len(checks),
        "reachable": bool(up),
        "slowest_s": max([float(c.get("seconds") or 0) for c in checks] or [0]),
        "calls": dict(calls),
        "measured": ("the index is the slow door: a wide CDX query cost 7-40 s on this machine, "
                     "while the resolver answered in 0.7-2.5 s"),
        "gaps": gaps,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "cached": False,
    }
    _PROBE_CACHE["at"] = now
    _PROBE_CACHE["value"] = value
    _PROBE_CACHE["calls"] = {k: int(_PROBE_CACHE["calls"].get(k) or 0) + v for k, v in calls.items()}
    return dict(value)


def state(*, deep: bool = True, force: bool = False) -> dict[str, Any]:
    """Everything the read-out card needs, in one dict, with named gaps."""
    out: dict[str, Any] = {
        "signature": SIGNATURE,
        "upstream": "Internet Archive Wayback Machine",
        "endpoints": [
            {"door": "availability API", "url": AVAILABILITY_URL, "doc": DOC_AVAILABILITY,
             "what": "is it archived, and which capture is closest (cheap, and coarse)"},
            {"door": "Memento resolver", "url": TIMEGATE_URL.format(target="<url>"), "doc": DOC_MEMENTO,
             "what": "the capture closest to a date, answered by the archive's own resolver"},
            {"door": "CDX index", "url": CDX_URL, "doc": DOC_CDX,
             "what": "every capture, every archived URL, with filters and paging"},
            {"door": "Memento TimeMap", "url": TIMEMAP_URL.format(fmt="link", target="<url>"),
             "doc": DOC_MEMENTO, "what": "the whole history of one URL as links or rows"},
            {"door": "snapshot", "url": SNAPSHOT_URL.format(timestamp="<ts>", raw="", target="<url>"),
             "doc": DOC_AVAILABILITY, "what": "read the archived copy itself (raw with id_)"},
        ],
        "fields": list(CDX_FIELDS),
        "match_types": list(MATCH_TYPES),
        "caps": {"rows": MAX_ROWS, "default_rows": DEFAULT_ROWS, "bytes": CDX_MAX_BYTES},
        "timeouts": {"availability_s": AVAILABILITY_TIMEOUT, "timegate_s": TIMEGATE_TIMEOUT,
                     "timemap_s": TIMEMAP_TIMEOUT, "cdx_s": CDX_TIMEOUT, "snapshot_s": SNAPSHOT_TIMEOUT},
        "call_counts": dict(_PROBE_CACHE.get("calls") or {}),
        "gaps": [],
    }
    if deep:
        p = probe(force=force)
        out["probe"] = p
        out["reachable"] = bool(p.get("reachable"))
        out["gaps"] = list(p.get("gaps") or [])
    return out


# --- parsing helpers ---------------------------------------------------------------------------


def _row_dict(fields: list[str], row: list[Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for i, name in enumerate(fields):
        out[str(name)] = str(row[i]) if i < len(row) else ""
    ts = out.get("timestamp") or ""
    if re.fullmatch(r"\d{8,14}", ts or ""):
        out["date"] = _readable_stamp(ts)
        out["snapshot_url"] = SNAPSHOT_URL.format(timestamp=ts, raw="", target=out.get("original") or "")
    return out


def _row_brief(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    return {"timestamp": row.get("timestamp") or "", "date": row.get("date") or "",
            "original": row.get("original") or "", "status": row.get("statuscode") or "",
            "mimetype": row.get("mimetype") or "", "bytes": row.get("length") or "",
            "snapshot_url": row.get("snapshot_url") or ""}


def _readable_stamp(ts: str) -> str:
    """20100501120000 -> 2010-05-01 12:00 (UTC, which is what the archive stamps in)."""
    out = ts
    if len(ts) >= 4:
        out = ts[:4]
        if len(ts) >= 6:
            out += "-" + ts[4:6]
        if len(ts) >= 8:
            out += "-" + ts[6:8]
        if len(ts) >= 10:
            out += " " + ts[8:10] + ":" + ts[10:12] if len(ts) >= 12 else " " + ts[8:10]
    return out


def _timestamp_from_url(url: str) -> str:
    m = re.search(r"/web/(\d{4,14})(?:[a-z_]{0,3})?/", str(url or ""))
    return m.group(1) if m else ""


def _snapshot_status(resp: dict[str, Any]) -> str:
    """What the archive says about the ORIGINAL capture (the header it forwards), when it says."""
    ctype = str(resp.get("ctype") or "")
    return ctype


def _rfc1123(ts: str) -> str:
    """`20100501` -> `Sat, 01 May 2010 00:00:00 GMT`, the header the Memento resolver wants."""
    padded = (ts + "0101000000")[:14]
    try:
        t = time.strptime(padded, "%Y%m%d%H%M%S")
    except ValueError:
        return ""
    return time.strftime("%a, %d %b %Y %H:%M:%S GMT", t)


def _drift(asked: str, served: str) -> str:
    """How far the served capture is from the ask, in the operator's words ('' when unknown)."""
    if not asked or not served:
        return ""
    a = (asked + "0101000000")[:14]
    s = (served + "0101000000")[:14]
    try:
        ta = time.mktime(time.strptime(a, "%Y%m%d%H%M%S"))
        ts_ = time.mktime(time.strptime(s, "%Y%m%d%H%M%S"))
    except (ValueError, OverflowError):
        return ""
    days = int(abs(ts_ - ta) // 86400)
    if days == 0:
        return "the same day as the ask"
    if days < 60:
        return f"{days} day(s) from the ask"
    years = days / 365.25
    if years < 2:
        return f"{days} days (~{years:.1f} years) from the ask"
    return f"~{years:.1f} years from the ask"


_LINK_RE = re.compile(r"<([^>]+)>\s*((?:;[^<]*)?)")


def _parse_link_format(text: str) -> list[dict[str, str]]:
    """Memento `application/link-format` -> rows. Only the entries with a datetime are captures."""
    rows: list[dict[str, str]] = []
    for m in _LINK_RE.finditer(str(text or "")):
        url = m.group(1).strip()
        attrs = m.group(2) or ""
        rel = _attr(attrs, "rel")
        when = _attr(attrs, "datetime")
        if "memento" not in rel and "first" not in rel and "last" not in rel and "prev" not in rel and "next" not in rel:
            continue
        if not url.startswith("http"):
            continue
        if "original" in rel or "timegate" in rel or "self" in rel:
            continue
        stamps = _timestamp_from_url(url)
        rows.append({
            "url": url,
            "rel": rel,
            "datetime": when,
            "timestamp": stamps,
            "date": _readable_stamp(stamps) if stamps else "",
            "snapshot_url": url,
        })
    return rows


def _attr(text: str, name: str) -> str:
    m = re.search(r'%s\s*=\s*"([^"]*)"' % re.escape(name), str(text or ""))
    return m.group(1) if m else ""


def _link_first(text: str, rel: str) -> str:
    for m in _LINK_RE.finditer(str(text or "")):
        if rel in _attr(m.group(2) or "", "rel"):
            return m.group(1).strip()
    return ""
