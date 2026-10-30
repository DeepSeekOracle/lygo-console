"""LYGO module host — the kernel seam that lets the console carry Function Modules.

Δ9Φ963-LYGO-MODULE-CORE-v1

WHAT THIS IS
    One loader and one dispatch table. `server.py` keeps its own routes; this host adds a single
    lookup in front of them (`ModuleHost.handle`), so a module's declared route answers without any
    edit to `server.py`. That is the whole point of the work order: adding a function later must not
    touch the kernel.

WHAT IT REFUSES TO DO
    - It never imports a module's internals (a module is reached through `register(ctx)` only).
    - It never guesses a default for a missing manifest field: a bad manifest refuses that module,
      by name, and the console keeps answering.
    - It never lets a module take the console down. Every handler, `register()`, `startup()` and
      `health()` runs inside a guard; a raising module is marked `DEGRADED(reason)`, its route
      answers a named 500 for that module only, and everything else keeps serving.
    - It never writes state itself: modules write through `ctx` (atomicio), inside their declared
      `state.owns`.

EMERGENCY SWITCH
    `LYGO_MODULES=off` in the environment loads no modules at all (the host then serves only
    `/api/modules`, which reports why). Use it to prove the console without the module layer.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from . import validate

SIGNATURE = "Δ9Φ963-LYGO-MODULE-CORE-v1"
MODULES_DIR = Path(__file__).resolve().parent
KIT_ROOT = MODULES_DIR.parents[1]
CATALOG_PATH = MODULES_DIR / "catalog.json"
KERNEL_RELEASE_FALLBACK = "1.9.0"
MAX_BODY = 512_000
#: How long a module's health function may take during `load()`, which sits on the console's boot
#: path. A check that has not answered by then is reported as a named gap and the boot carries on -
#: see `_health` for the measurement that made this a number instead of a hope.
HEALTH_MAX_SECONDS = 8.0


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def detect_edition(kit_root: Path) -> str:
    """`pc` / `usb` / `web`.

    v1.1.1 has no edition fact anywhere in the tree, so it is inferred (and can be pinned):
    `LYGO_EDITION` wins; otherwise the stick's own launcher (`LYGO_AGENT_STICK.bat`) marks the USB
    kit; anything else is the PC kit. The launcher sets LYGO_EDITION once the promotion pass lands.
    """
    raw = os.environ.get("LYGO_EDITION", "").strip().lower()
    if raw in ("pc", "usb", "web"):
        return raw
    try:
        if (Path(kit_root) / "LYGO_AGENT_STICK.bat").is_file():
            return "usb"
    except OSError:
        pass
    return "pc"


class Bus:
    """The only sanctioned coupling between modules: topics, no imports."""

    def __init__(self, log: Callable[[str], None] | None = None) -> None:
        self._lock = threading.Lock()
        self._subs: dict[str, list[Callable[[Any], None]]] = {}
        self._log = log or (lambda _msg: None)

    def subscribe(self, topic: str, fn: Callable[[Any], None]) -> bool:
        if not callable(fn):
            return False
        with self._lock:
            self._subs.setdefault(str(topic), []).append(fn)
        return True

    def publish(self, topic: str, payload: Any = None) -> int:
        """Deliver to every subscriber. A raising subscriber is logged, never propagated."""
        with self._lock:
            subs = list(self._subs.get(str(topic)) or [])
        sent = 0
        for fn in subs:
            try:
                fn(payload)
                sent += 1
            except Exception as exc:  # noqa: BLE001 - one bad subscriber must not stop the rest
                self._log(f"bus: subscriber of {topic!r} raised: {exc}")
        return sent

    def topics(self) -> dict[str, int]:
        with self._lock:
            return {k: len(v) for k, v in self._subs.items()}


class _P0:
    """The gate, as a module sees it. Read-only verdict; modules act on it."""

    def __init__(self, log: Callable[[str], None]) -> None:
        self._log = log

    def check(self, text: str, kind: str = "prompt") -> dict[str, Any]:
        try:
            import p0_hook
        except Exception as exc:  # noqa: BLE001
            return {"verdict": "QUARANTINE", "reason": f"p0_unavailable: {exc}"}
        try:
            fn = p0_hook.gate_output_window if kind in ("output", "window") else p0_hook.gate_prompt
            out = fn(text or "")
            return out if isinstance(out, dict) else {"verdict": "QUARANTINE", "reason": "p0_bad_answer"}
        except Exception as exc:  # noqa: BLE001
            self._log(f"p0 gate raised: {exc}")
            return {"verdict": "QUARANTINE", "reason": f"p0_raised: {exc}"}

    def allowed(self, text: str, kind: str = "prompt") -> bool:
        return str(self.check(text, kind).get("verdict") or "").upper() == "ALLOW"


class Ctx:
    """Kernel services handed to a module. Nothing else is reachable from a module."""

    def __init__(self, module_id: str, kit_root: Path, host: "ModuleHost") -> None:
        self.module_id = module_id
        self.kit_root = Path(kit_root)
        self._host = host
        self.operators_words = ""            # read-only for the module: set by the chat loop
        self.edition = detect_edition(self.kit_root)
        self.release = KERNEL_RELEASE_FALLBACK
        self.build = SIGNATURE
        try:
            import version

            self.release = version.release()
            self.build = version.stamp()
        except Exception as exc:  # noqa: BLE001 - a display fact must not stop a module
            self.log(f"ctx: version module unavailable ({exc}); release reads {self.release}")
        self.p0 = _P0(self.log)
        self.bus = host.bus

    # --- paths -------------------------------------------------------------------------------
    def save_dir(self, *parts: str) -> Path:
        """A directory under the kit's save/ tree (created on demand)."""
        from paths import SAVE

        p = SAVE.joinpath(*[str(x) for x in parts if str(x)])
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return p

    def kit_path(self, *parts: str) -> Path:
        return self.kit_root.joinpath(*[str(x) for x in parts if str(x)])

    # --- io (atomicio only) -------------------------------------------------------------------
    def read_text(self, path: Any, default: str = "") -> str:
        try:
            import atomicio

            return atomicio.read_text(path)
        except Exception as exc:  # noqa: BLE001
            self.log(f"read_text({path}) failed: {exc}")
            return default

    def write_text(self, path: Any, text: str) -> bool:
        try:
            import atomicio

            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            atomicio.atomic_write_text(target, text)
            return True
        except Exception as exc:  # noqa: BLE001
            self.log(f"write_text({path}) failed: {exc}")
            return False

    def read_json(self, path: Any, default: Any = None) -> Any:
        text = self.read_text(path, "")
        if not text:
            return default
        try:
            return json.loads(text)
        except ValueError:
            self.log(f"read_json({path}) is not valid JSON")
            return default

    def write_json(self, path: Any, data: Any) -> bool:
        try:
            text = json.dumps(data, indent=2, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            self.log(f"write_json({path}) could not serialise: {exc}")
            return False
        return self.write_text(path, text)

    # --- receipts, gating, log ---------------------------------------------------------------
    def receipt(self, spec: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
        """Write a receipt. `spec` is a dict; keyword arguments win over it."""
        data: dict[str, Any] = dict(spec or {})
        data.update(kw)
        data.setdefault("prompt", "")
        data.setdefault("output", "")
        data.setdefault("model", f"module:{self.module_id}")
        data.setdefault("gate", {"verdict": "ALLOW", "reason": "module", "signature": SIGNATURE})
        data.setdefault("extra", {"module": self.module_id})
        try:
            from receipts import write_receipt

            return write_receipt(**data)
        except TypeError as exc:
            self.log(f"receipt rejected the spec ({exc}); wrote the minimal one")
            try:
                from receipts import write_receipt

                return write_receipt(
                    prompt=str(data.get("prompt") or ""),
                    output=str(data.get("output") or ""),
                    model=str(data.get("model") or f"module:{self.module_id}"),
                    gate=data.get("gate") or {"verdict": "ALLOW", "reason": "module"},
                )
            except Exception as exc2:  # noqa: BLE001
                return {"ok": False, "error": f"receipt_failed: {exc2}"}
        except Exception as exc:  # noqa: BLE001
            self.log(f"receipt failed: {exc}")
            return {"ok": False, "error": f"receipt_failed: {exc}"}

    def log(self, msg: str) -> None:
        """A line in the kit's own console log. Logging may never take the console down."""
        line = f"[{_now()}] [module {self.module_id}] {msg}"
        self._host.note(line)
        try:
            from paths import LOGS

            LOGS.mkdir(parents=True, exist_ok=True)
            with open(LOGS / f"console-{time.strftime('%Y%m%d')}.log", "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:  # noqa: BLE001 - a log write is never fatal
            pass


class Request:
    """One HTTP request, as a module handler sees it."""

    def __init__(self, handler: Any, method: str, path: str, module_id: str, entry: str) -> None:
        self._handler = handler
        self.method = method
        self.path = path
        self.module_id = module_id
        self.entry = entry
        self.replied = False
        self._query: dict[str, str] | None = None
        self._body: dict[str, Any] | None = None

    @property
    def query(self) -> dict[str, str]:
        if self._query is None:
            try:
                raw = parse_qs(urlparse(str(getattr(self._handler, "path", "") or self.path)).query)
            except Exception:  # noqa: BLE001
                raw = {}
            self._query = {k: (v[0] if isinstance(v, list) and v else "") for k, v in raw.items()}
        return self._query

    def get(self, key: str, default: Any = None) -> Any:
        return self.query.get(key, default)

    def body(self, limit: int = 300_000) -> dict[str, Any]:
        """The parsed JSON body, `{}` when there is none or it is not JSON. Read once."""
        if self._body is None:
            self._body = {}
            try:
                raw = self._handler._read_body(limit)  # the kernel owns body limits
                if raw:
                    obj = json.loads(raw.decode("utf-8") or "{}")
                    if isinstance(obj, dict):
                        self._body = obj
            except Exception:  # noqa: BLE001 - a bad body is not a module failure
                # The kernel treats an unreadable/unparsable body as {} and carries on (see
                # /api/scan, /api/select). A module's route answers the same way for parity.
                self._body = {}
        return self._body

    def json(self, code: int, payload: Any) -> None:
        """Answer. The kernel writes the bytes (same encoder, same headers)."""
        self.replied = True
        self._handler._json(code, payload)


class ModuleHost:
    """Loads the catalog, owns one route/limb table, and answers for its modules."""

    def __init__(
        self,
        kit_root: Path | None = None,
        modules_dir: Path | None = None,
        catalog_path: Path | None = None,
    ) -> None:
        self.kit_root = Path(kit_root) if kit_root else KIT_ROOT
        self.modules_dir = Path(modules_dir) if modules_dir else MODULES_DIR
        self.catalog_path = Path(catalog_path) if catalog_path else self.modules_dir / "catalog.json"
        self.bus = Bus(log=self.note)
        self.modules: dict[str, dict[str, Any]] = {}
        self.routes: dict[tuple[str, str], dict[str, Any]] = {}
        self.limbs: dict[str, dict[str, Any]] = {}
        self.loaded = False
        self.load_error = ""
        self.catalog: dict[str, Any] = {"ok": True, "error": "", "modules": []}
        self.notes: list[str] = []
        self._ctxs: dict[str, Ctx] = {}

    # --- small helpers ------------------------------------------------------------------------
    def note(self, line: str) -> None:
        self.notes.append(line)
        if len(self.notes) > 500:
            del self.notes[:200]

    def ctx_for(self, module_id: str) -> Ctx:
        ctx = self._ctxs.get(module_id)
        if ctx is None:
            ctx = Ctx(module_id, self.kit_root, self)
            self._ctxs[module_id] = ctx
        return ctx

    def _row(self, mid: str, **kw: Any) -> dict[str, Any]:
        row = self.modules.setdefault(
            mid,
            {
                "id": mid,
                "title": mid,
                "state": "SCAFFOLDED",
                # `state` is the EFFECTIVE status (the manifest's rung, or REFUSED/DISABLED/DEGRADED
                # after a failure) and `lifecycle` is what the manifest itself declared. They are
                # separate keys because they answer different questions, and because a pane's own
                # `state` (green/amber/red) is not a rung at all. Measured 2026-09-20: a report that
                # read row["lifecycle"] got None for every module and called five healthy modules
                # nameless, which is how this key came to exist.
                "lifecycle": "",
                "enabled": True,
                "error": "",
                "surfaces": {},
                "routes": [],
                "limbs": [],
                "panes": [],
                "notes": "",
                "health": {},
            },
        )
        row.update(kw)
        # A row can exist before its manifest is readable (a disabled catalog entry, or a refusal
        # raised by the manifest check itself). "No declared rung" is then the honest answer - the
        # effective `state` still carries REFUSED/DISABLED.
        row.setdefault("lifecycle", "")
        return row

    # --- load ---------------------------------------------------------------------------------
    def load(self, force: bool = False) -> "ModuleHost":
        """Load every enabled module and return this host (chainable). Idempotent; never raises."""
        if self.loaded and not force:
            return self
        self.modules.clear()
        self.routes.clear()
        self.limbs.clear()
        self._ctxs.clear()
        self.loaded = True

        if os.environ.get("LYGO_MODULES", "").strip().lower() in ("off", "0", "false"):
            self.load_error = "modules_disabled_by_env"
            self.note("LYGO_MODULES=off: no modules loaded (the console serves without the module layer)")
            return self

        self.catalog = validate.load_catalog(self.modules_dir, self.catalog_path)
        if not self.catalog.get("ok"):
            self.load_error = str(self.catalog.get("error") or "catalog_unreadable")
            self.note(f"module catalog not loaded: {self.load_error}")
            return self

        for entry in self.catalog.get("modules") or []:
            if not isinstance(entry, dict):
                continue
            mid = str(entry.get("id") or "")
            if not mid:
                continue
            if not entry.get("enabled", True):
                self._row(mid, enabled=False, state="DISABLED", error="", notes="disabled in catalog.json")
                continue
            try:
                self._load_one(mid, entry)
            except Exception as exc:  # noqa: BLE001 - one module must not stop the rest
                self._refuse(mid, f"load_failed: {exc}")
        self.note(
            f"loaded {len([m for m in self.modules.values() if m['state'] != 'REFUSED'])}"
            f"/{len(self.modules)} modules, {len(self.routes)} route(s), {len(self.limbs)} limb(s)"
        )
        return self

    def _load_one(self, mid: str, entry: dict[str, Any]) -> None:
        mdir = self.modules_dir / mid
        manifest_path = mdir / "module.json"
        if not manifest_path.is_file():
            self._refuse(mid, "module.json is missing")
            return
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            self._refuse(mid, f"module.json is not valid JSON ({exc})")
            return

        problems = validate.validate_manifest(manifest, mdir, catalog_id=mid)
        if problems:
            self._refuse(mid, "invalid manifest: " + "; ".join(problems[:3]))
            return

        self._row(
            mid,
            title=str(manifest.get("title") or mid),
            state=str(manifest.get("lifecycle") or "SCAFFOLDED"),
            lifecycle=str(manifest.get("lifecycle") or "SCAFFOLDED"),
            surfaces=manifest.get("surfaces") or {},
            notes=str(manifest.get("notes") or ""),
            order=entry.get("order"),
        )

        requires = manifest.get("requires") or {}
        want = str(requires.get("console") or "*")
        ctx = self.ctx_for(mid)
        if not validate.console_satisfies(want, ctx.release):
            self._refuse(mid, f"module_unmet: {mid} needs console {want}, this kit is {ctx.release}")
            return
        missing_env = [str(e) for e in (requires.get("env") or []) if str(e) and str(e) not in os.environ]
        if missing_env:
            self._refuse(mid, f"module_unmet: needs env {', '.join(missing_env)}")
            return

        adapter = str(manifest.get("adapter") or "backend.py")
        backend = self._import_adapter(mid, mdir / adapter)
        register = getattr(backend, "register", None)
        if not callable(register):
            self._refuse(mid, f"{adapter} has no callable register(ctx)")
            return

        reg = register(ctx)
        if not isinstance(reg, dict):
            self._refuse(mid, "register(ctx) did not return a dict")
            return

        claimed: list[str] = []
        declared: dict[tuple[str, str], dict[str, Any]] = {}
        for r in manifest.get("routes") or []:
            if isinstance(r, dict):
                declared[(str(r.get("method") or "").upper(), str(r.get("path") or ""))] = r
        runtime: set[tuple[str, str]] = set()
        for spec in reg.get("routes") or []:
            method, path, fn = self._parse_route(spec)
            if not method or not path or not callable(fn):
                self._refuse(mid, f"route spec is not a (method, path, handler) triple: {spec!r}")
                return
            key = (method, path)
            if key not in declared:
                # The manifest is the contract: a route that is not declared in module.json does not
                # exist as far as the console is concerned.
                self._refuse(mid, f"undeclared route: {method} {path} is not listed in module.json")
                return
            owner = self.routes.get(key)
            if owner is not None:
                self._refuse(
                    mid,
                    f"route collision: {method} {path} is also claimed by {owner['module']}",
                )
                return
            meta = declared.get(key) or {}
            self.routes[key] = {"module": mid, "handler": fn, "entry": getattr(fn, "__name__", "?"),
                                "legacy": bool(meta.get("legacy")), "declared": True}
            runtime.add(key)
            claimed.append(f"{method} {path}")

        missing = sorted(k for k in declared if k not in runtime)
        if missing:
            self._refuse(mid, "declared route not registered: " + ", ".join(f"{m} {p}" for m, p in missing))
            return

        for spec in reg.get("limbs") or []:
            if not isinstance(spec, dict) or not str(spec.get("name") or ""):
                continue
            name = str(spec["name"])
            owner = self.limbs.get(name)
            if owner is not None:
                self._refuse(mid, f"limb collision: {name!r} is also claimed by {owner['module']}")
                return
            self.limbs[name] = {"module": mid, "effect": str(spec.get("effect") or "read"),
                                "handler": spec.get("handler"), "schema": spec.get("schema")}

        health_fn = reg.get("health")
        panes = [p.get("id") if isinstance(p, dict) else p for p in (reg.get("panes") or [])]

        startup = reg.get("startup")
        if callable(startup):
            try:
                startup()
            except Exception as exc:  # noqa: BLE001
                self._degrade(mid, f"startup failed: {exc}")

        self._row(mid, routes=claimed, limbs=[str(l.get("name")) for l in (reg.get("limbs") or [])
                                              if isinstance(l, dict) and l.get("name")],
                  panes=[p for p in panes if p], health=self._health(mid, health_fn), routes_declared=True)
        ctx.log(f"wired: {len(claimed)} route(s), {len(reg.get('limbs') or [])} limb(s)")

    def _parse_route(self, spec: Any) -> tuple[str, str, Any]:
        if isinstance(spec, dict):
            return (str(spec.get("method") or "").upper(), str(spec.get("path") or ""), spec.get("handler"))
        if isinstance(spec, (tuple, list)) and len(spec) >= 3:
            return (str(spec[0]).upper(), str(spec[1]), spec[2])
        return ("", "", None)

    def _import_adapter(self, mid: str, path: Path) -> Any:
        name = "lygo_mod_" + mid.replace(".", "_").replace("-", "_")
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path.name}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _refuse(self, mid: str, reason: str) -> None:
        """Refuse one module, by name, and keep the console up."""
        self._row(mid, state="REFUSED", error=reason, enabled=True)
        self.note(f"REFUSED {mid}: {reason}")

    def _degrade(self, mid: str, reason: str) -> None:
        row = self.modules.get(mid)
        if row is None:
            row = self._row(mid)
        row["state"] = "DEGRADED"
        row["error"] = str(reason)[:200]
        row.setdefault("health", {})
        row["health"] = {"ok": False, "detail": str(reason)[:200]}
        self.note(f"DEGRADED {mid}: {reason}")

    def _health(self, mid: str, fn: Any) -> dict[str, Any]:
        """Ask one module's health function, but never let it hold the console.

        This runs on the BOOT path: `load()` calls it for every module, and `/api/modules` is the
        first thing a page asks for. A health function is allowed to be a real check - reaching a
        service, reading a device - so it is given HEALTH_MAX_SECONDS and then reported as a named
        gap instead of blocking. Measured need: `lygo.archiveinfo`'s health asks the Internet Archive
        (its per-door timeouts are 25-75 s), so on a network where the TLS read stalls, the console
        sat inside `load()` for minutes and the whole test suite looked hung. A check that did not
        answer is NOT a module that failed: it is reported as `ok: false` with the reason, and the
        module stays wired.
        """
        if not callable(fn):
            return {"ok": None, "detail": "no health function declared"}
        box: dict[str, Any] = {}

        def ask() -> None:
            try:
                box["out"] = fn(self.ctx_for(mid))
            except Exception as exc:  # noqa: BLE001
                box["err"] = exc

        worker = threading.Thread(target=ask, name=f"health:{mid}", daemon=True)
        worker.start()
        worker.join(HEALTH_MAX_SECONDS)
        if worker.is_alive():
            # Left running as a daemon on purpose: killing a thread mid-socket is not a thing Python
            # can do safely, and the answer it would have given is the answer that never arrived.
            return {"ok": False,
                    "detail": f"health did not answer within {HEALTH_MAX_SECONDS:g}s - reported as a "
                              f"named gap, not a fault, and the console carried on"}
        if "err" in box:
            return {"ok": False, "detail": f"health raised: {box['err']}"}
        out = box.get("out")
        return out if isinstance(out, dict) else {"ok": None, "detail": f"health returned {type(out).__name__}"}

    # --- serving ------------------------------------------------------------------------------
    def handle(self, handler: Any, method: str, path: str) -> bool:
        """Answer `path` if a module owns it. True = answered (including a named failure)."""
        try:
            if not self.loaded:
                self.load()
            method = str(method or "").upper()
            if method == "GET" and path == "/api/modules":
                handler._json(200, self.table())
                return True
            route = self.routes.get((method, path))
            if route is None:
                return False
            req = Request(handler, method, path, route["module"], route.get("entry") or "?")
            try:
                route["handler"](self.ctx_for(route["module"]), req)
            except Exception as exc:  # noqa: BLE001 - a module's failure is the module's
                self._degrade(route["module"], f"{route['entry']} raised: {exc}")
                self.note(traceback.format_exc(limit=3))
                if not req.replied:
                    handler._json(
                        500,
                        {
                            "ok": False,
                            "error": "module_error",
                            "module": route["module"],
                            "entry": route["entry"],
                            "detail": str(exc)[:160],
                        },
                    )
            return True
        except Exception as exc:  # noqa: BLE001 - the host may never be why the console stops
            self.note(f"host.handle failed on {method} {path}: {exc}")
            return False

    # --- reporting ----------------------------------------------------------------------------
    def rows(self) -> list[dict[str, Any]]:
        order = {str(e.get("id")): e.get("order") for e in (self.catalog.get("modules") or [])
                 if isinstance(e, dict)}
        rows = list(self.modules.values())
        rows.sort(key=lambda r: (order.get(str(r["id"])) if isinstance(order.get(str(r["id"])), int) else 999,
                                 str(r["id"])))
        return rows

    def table(self) -> dict[str, Any]:
        rows = self.rows()
        wired = [r for r in rows if r["state"] not in ("REFUSED", "DISABLED")]
        return {
            "ok": True,
            "signature": SIGNATURE,
            "edition": detect_edition(self.kit_root),
            "release": self.ctx_for("lygo.health").release if self._ctxs else KERNEL_RELEASE_FALLBACK,
            "build": self.ctx_for("lygo.health").build if self._ctxs else SIGNATURE,
            "catalog": self.catalog_path.name,
            "catalog_ok": bool(self.catalog.get("ok")),
            "load_error": self.load_error,
            "counts": {
                "modules": len(rows),
                "wired": len(wired),
                "refused": len([r for r in rows if r["state"] == "REFUSED"]),
                "disabled": len([r for r in rows if r["state"] == "DISABLED"]),
                "degraded": len([r for r in rows if r["state"] == "DEGRADED"]),
                "routes": len(self.routes),
                "limbs": len(self.limbs),
            },
            "modules": [
                {
                    "id": r["id"],
                    "title": r["title"],
                    "state": r["state"],
                    "lifecycle": r.get("lifecycle") or "",
                    "enabled": r["enabled"],
                    "error": r["error"],
                    "surfaces": r["surfaces"],
                    "routes": r["routes"],
                    "limbs": r["limbs"],
                    "panes": r["panes"],
                    "health": r.get("health") or {},
                    "notes": r.get("notes") or "",
                }
                for r in rows
            ],
            "hash": self.table_hash(rows),
        }

    def table_hash(self, rows: list[dict[str, Any]] | None = None) -> str:
        """A stable digest of the wired surface — the value MODULES.lock.json will carry."""
        rows = rows if rows is not None else self.rows()
        canon = [
            {
                "id": r["id"],
                "state": r["state"],
                "enabled": bool(r["enabled"]),
                "routes": sorted(r["routes"]),
                "limbs": sorted(r["limbs"]),
                "panes": sorted(r["panes"]),
            }
            for r in sorted(rows, key=lambda x: str(x["id"]))
        ]
        blob = json.dumps(canon, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    def health(self) -> dict[str, Any]:
        """The module section for /api/health (M3 wires it in; callable now)."""
        rows = self.rows()
        bad = [r["id"] for r in rows if r["state"] in ("REFUSED", "DEGRADED")]
        return {
            "ok": not bad,
            "count": len(rows),
            "problem_modules": bad,
            "hash": self.table_hash(rows),
            "signature": SIGNATURE,
        }

    def limb(self, name: str) -> dict[str, Any] | None:
        """The declared limb, or None. The kernel's own tool catalog stays authoritative in M1."""
        return self.limbs.get(str(name))


_HOST: ModuleHost | None = None


def current() -> ModuleHost:
    """The process-wide host. Loaded lazily so importing this module is always cheap."""
    global _HOST
    if _HOST is None:
        _HOST = ModuleHost()
        try:
            _HOST.load()
        except Exception as exc:  # noqa: BLE001 - the console serves without the module layer
            _HOST.load_error = f"load_failed: {exc}"
    return _HOST


def handle(handler: Any, method: str, path: str) -> bool:
    """The seam `server.py` calls: True when a module answered."""
    try:
        return current().handle(handler, method, path)
    except Exception as exc:  # noqa: BLE001
        try:
            print(f"[modules] dispatch failed for {method} {path}: {exc}", flush=True)
        except Exception:  # noqa: BLE001
            pass
        return False
