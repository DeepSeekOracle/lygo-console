"""Performance-pass regression tests (2026-09-18 tuning sweep).

Four things this locks, each measured live before it was written down:

  * ``/api/chat`` with ``"tools": false`` answered **HTTP 500 handler_failed**
    (``UnboundLocalError: honest_pending``) — that is the portal's unticked "Agent limbs" checkbox
    and the payload the docs told every tester to send. The handler answers now, and a plain turn
    still reports what the engine actually did.
  * the planner charged **nothing** for the KV cache, so a model whose weights only just fit was
    planned as a full offload even when its context could not be allocated. The KV figure now comes
    from the model's own GGUF header.
  * the tuning flags are part of the launch signature, so changing one restarts the engine instead
    of silently reusing a server launched with the old flags.
  * a tuned-flag failure degrades to the shipped defaults *before* the backend is blamed: a
    performance knob may cost speed on a strange host, never the brain.

The handler tests run a real HTTP server against a mocked engine: a mock is the only way to assert
the tools:false path without a GPU and a 4 GB model in the suite.
"""
from __future__ import annotations

import json
import secrets
import socket
import sys
import struct
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import auth  # noqa: E402
import engine  # noqa: E402
import gguf_header  # noqa: E402
import lygo_engine  # noqa: E402
import paths  # noqa: E402
import perf  # noqa: E402
import server  # noqa: E402

_state: dict = {}

# A turn the engine could really have produced: the timings block is llama.cpp's own, which is what
# the console now reports back to the operator.
FAKE_TIMINGS = {
    "prompt_n": 3696,
    "prompt_per_second": 3307.8,
    "predicted_n": 120,
    "predicted_per_second": 61.5,
}
FAKE_ANSWER = "The local engine answered this turn."


def _fake_llama_chat(*args, **kwargs):
    body = json.dumps(
        {
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": FAKE_ANSWER, "tool_calls": None},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"completion_tokens": 120, "prompt_tokens": 3696},
            "timings": FAKE_TIMINGS,
        }
    ).encode("utf-8")
    return 200, body, "application/json"


def setUpModule() -> None:
    # The operator's live config/api.json is MACHINE state (gitignored), and this module measures the
    # ENGINE: with a cloud provider saved and enabled the console rightly prefers it, the mocked engine
    # is never called, and ten tests about timings and photo fitting fail on a correct working tree.
    # A test that measures the engine pins the brain to the engine, whatever the operator last chose.
    import cloud_api

    # server.py decides with `bool(st_cloud.get("enabled") and has_key)` from public_status(), so THAT
    # is the seam to pin - patching cloud_api.active() looked right and changed nothing.
    _state["cloud_mod"] = cloud_api
    _state["cloud_status"] = cloud_api.public_status
    _quiet = dict(cloud_api.public_status() or {})
    _quiet.update({"enabled": False, "has_key": False, "degraded": False})
    cloud_api.public_status = lambda: dict(_quiet)

    _state["auth"] = server.AUTH_REQUIRED
    server.AUTH_REQUIRED = False
    _state["spawn"] = server.maybe_spawn
    _state["receipt"] = server.write_receipt
    _state["session"] = server.save_session
    server.maybe_spawn = lambda model_id=None: "ready"  # no engine, no 4 GB model in the suite
    server.write_receipt = lambda **kw: {"id": "test-receipt"}
    server.save_session = lambda *a, **kw: None
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    _state["httpd"] = httpd
    _state["port"] = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()


def tearDownModule() -> None:
    _state["cloud_mod"].public_status = _state["cloud_status"]

    _state["httpd"].shutdown()
    _state["httpd"].server_close()
    server.AUTH_REQUIRED = _state["auth"]
    server.maybe_spawn = _state["spawn"]
    server.write_receipt = _state["receipt"]
    server.save_session = _state["session"]


def post_json(path: str, payload: dict, timeout: float = 30.0) -> tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    req = (
        f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
    ).encode("ascii") + body
    with socket.create_connection(("127.0.0.1", _state["port"]), timeout=timeout) as s:
        s.sendall(req)
        out = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            out += chunk
    head, _, rest = out.partition(b"\r\n\r\n")
    status = int(head.split(b" ")[1])
    return status, rest.decode("utf-8", "replace")


class ChatToolsFlagTests(unittest.TestCase):
    """`"tools": false` used to take the handler down with an UnboundLocalError."""

    def test_tools_false_is_answered_instead_of_500(self):
        with patch("openai_proxy.llama_chat", _fake_llama_chat):
            status, raw = post_json(
                "/api/chat",
                {"messages": [{"role": "user", "content": "hello there"}], "stream": False, "tools": False},
            )
        self.assertEqual(status, 200, raw[:400])
        obj = json.loads(raw)
        self.assertIn("answered", obj["text"])
        self.assertEqual(obj["active"], "local")

    def test_tools_true_is_answered(self):
        with patch("openai_proxy.llama_chat", _fake_llama_chat):
            status, raw = post_json(
                "/api/chat",
                {"messages": [{"role": "user", "content": "hello there"}], "stream": False, "tools": True},
            )
        self.assertEqual(status, 200, raw[:400])
        self.assertEqual(json.loads(raw)["active"], "local")

    def test_missing_tools_key_defaults_to_tools_on(self):
        with patch("openai_proxy.llama_chat", _fake_llama_chat):
            status, raw = post_json(
                "/api/chat", {"messages": [{"role": "user", "content": "hello there"}], "stream": False}
            )
        self.assertEqual(status, 200, raw[:400])

    def test_a_plain_turn_reports_the_engine_timings(self):
        with patch("openai_proxy.llama_chat", _fake_llama_chat):
            _, raw = post_json(
                "/api/chat",
                {"messages": [{"role": "user", "content": "hello there"}], "stream": False, "tools": False},
            )
        got = json.loads(raw)["perf"]
        self.assertEqual(got["engine_calls"], 1)
        self.assertEqual(got["gen_tokens"], 120)
        self.assertEqual(got["gen_tok_s"], 61.5)
        self.assertEqual(got["prompt_tokens"], 3696)
        self.assertEqual(got["prompt_tok_s"], 3307.8)

    def test_health_carries_the_last_turn_timings(self):
        with patch("openai_proxy.llama_chat", _fake_llama_chat):
            post_json("/api/chat", {"messages": [{"role": "user", "content": "hello there"}], "stream": False})
        with socket.create_connection(("127.0.0.1", _state["port"]), timeout=30) as s:
            s.sendall(b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
            out = b""
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                out += chunk
        body = json.loads(out.partition(b"\r\n\r\n")[2].decode("utf-8", "replace"))
        self.assertEqual(body["last_perf"]["gen_tok_s"], 61.5)

    def test_the_streamed_done_event_carries_the_timings(self):
        with patch("openai_proxy.llama_chat", _fake_llama_chat):
            status, raw = post_json(
                "/api/chat",
                {"messages": [{"role": "user", "content": "hello there"}], "stream": True, "tools": False},
            )
        self.assertEqual(status, 200)
        events = [json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: ")]
        done = [e for e in events if e.get("type") == "done"]
        self.assertTrue(done, raw[:400])
        self.assertEqual(done[0]["perf"]["gen_tok_s"], 61.5)

    def test_a_turn_the_engine_never_ran_has_no_perf_claim(self):
        """No timing block, no number — telemetry must not invent a speed."""
        def bare(*args, **kwargs):
            body = json.dumps({"choices": [{"message": {"role": "assistant", "content": FAKE_ANSWER}}]}).encode()
            return 200, body, "application/json"

        with patch("openai_proxy.llama_chat", bare):
            _, raw = post_json(
                "/api/chat",
                {"messages": [{"role": "user", "content": "hello there"}], "stream": False, "tools": False},
            )
        self.assertEqual(json.loads(raw)["perf"], {})


class KvMathTests(unittest.TestCase):
    """The KV cache is the number the planner used to ignore."""

    # Qwen2.5-7B: 28 layers, 4 KV heads (GQA), 128 key + 128 value length, f16.
    QWEN25_7B = {
        "general.architecture": "qwen2",
        "qwen2.block_count": 28,
        "qwen2.attention.head_count": 28,
        "qwen2.attention.head_count_kv": 4,
        "qwen2.attention.key_length": 128,
        "qwen2.attention.value_length": 128,
        "qwen2.context_length": 32768,
    }

    def test_kv_bytes_per_token_comes_from_the_header(self):
        self.assertEqual(perf.kv_bytes_per_token(self.QWEN25_7B), 57344)  # 28*4*256*2

    def test_gqa_is_honoured_not_the_attention_head_count(self):
        """28 attention heads would be 401 KiB/token — planning that would refuse every GPU."""
        no_kv = {k: v for k, v in self.QWEN25_7B.items() if "head_count_kv" not in k}
        self.assertEqual(perf.kv_bytes_per_token(no_kv), 28 * 28 * 256 * 2)

    def test_an_unknown_header_is_charged_flat_not_free(self):
        self.assertIsNone(perf.kv_bytes_per_token({}))
        self.assertIsNone(perf.kv_bytes_per_token(None))
        self.assertEqual(perf.kv_cache_mib(None, 8192), perf.KV_UNKNOWN_MIB)

    def test_the_cache_scales_with_context_and_type(self):
        bpt = perf.kv_bytes_per_token(self.QWEN25_7B)
        self.assertEqual(perf.kv_cache_mib(bpt, 8192), 448)  # measured live: ~450 MiB at ctx 8192
        self.assertEqual(perf.kv_cache_mib(bpt, 16384), 896)
        self.assertEqual(perf.kv_cache_mib(bpt, 16384, "q8_0"), 448)  # half the cache, same context
        self.assertEqual(perf.kv_cache_mib(bpt, 16384, "q4_0"), 224)

    def test_a_junk_kv_type_falls_back_to_f16(self):
        self.assertEqual(perf.sanitize_kv_type("q8_0"), "q8_0")
        self.assertEqual(perf.sanitize_kv_type("Q4_0"), "q4_0")
        for junk in ("", None, "q3_k", "on", 7):
            self.assertEqual(perf.sanitize_kv_type(junk), "f16", junk)


class PlannerKvTests(unittest.TestCase):
    """plan_ngl keeps its verdicts, and now it knows what the cache costs."""

    def test_a_model_whose_cache_does_not_fit_is_not_fully_offloaded(self):
        ngl, why = perf.plan_ngl(5 * 1024**3, 8192, kv_mib=4096)
        self.assertTrue(0 < ngl < 99, ngl)
        self.assertTrue(why.startswith("partial_offload_"), why)

    def test_the_same_model_without_its_cache_still_fits(self):
        ngl, why = perf.plan_ngl(5 * 1024**3, 8192)
        self.assertEqual((99, "fits_vram"), (ngl, why))

    def test_kv_is_still_charged_when_nobody_measured_it(self):
        ngl, why = perf.plan_ngl(6 * 1024**3, 8192, kv_mib=0)
        self.assertEqual((99, "fits_vram"), (ngl, why))  # 6144 + 256 + 245 = 6645 <= 7168
        ngl, why = perf.plan_ngl(7 * 1024**3, 8192, kv_mib=0)
        self.assertNotEqual(99, ngl)  # 7168 + 256 + 286 = 7710 > 7168 -> partial, not a lie

    def test_the_resolved_profile_reports_the_kv_it_planned_with(self):
        got = perf.resolve(
            lim={"ngl": "auto", "threads": "auto"},
            hw={"ram_bytes": 32 * 1024**3, "ram_gib": 32, "devices": [], "backends": [], "gpu_ok": True},
            model_bytes=4 * 1024**3,
            model_id="qwen",
            kv_mib=448,
        )
        self.assertEqual(got["kv_mib"], 448)


class EngineFlagTests(unittest.TestCase):
    """The launch flags are sanitised here, clamped there, and part of the launch signature."""

    def test_kv_type_is_whitelisted(self):
        for good in ("q8_0", "Q4_0", " f16 "):
            self.assertIn(engine.clean_kv_type(good), engine.KV_TYPES)
        for bad in ("", None, "q3_k_m", "on", 12, "q8_0; rm -rf /"):
            self.assertEqual(engine.clean_kv_type(bad), "", bad)

    def test_spawn_runner_accepts_the_tuning_knobs(self):
        import inspect

        params = inspect.signature(engine.spawn_runner).parameters
        for key in ("kv_type", "batch", "ubatch", "flash_attn", "mmap"):
            self.assertIn(key, params, key)


class ConsoleLimitTests(unittest.TestCase):
    """The engine knobs are read exactly where the rest of the launch limits are read."""

    def _limits(self, cfg):
        with patch.object(paths, "_CFG_CACHE", cfg):
            return paths.console_limits()

    def test_the_knobs_are_part_of_the_launch_limits(self):
        lim = self._limits({"kv_type": "q8_0", "batch": 2048, "ubatch": 1024})
        self.assertEqual(lim["kv_type"], "q8_0")
        self.assertEqual(lim["batch"], 2048)
        self.assertEqual(lim["ubatch"], 1024)

    def test_junk_reads_as_no_tuning(self):
        lim = self._limits({"kv_type": None, "batch": "lots", "ubatch": True})
        self.assertEqual(lim["kv_type"], "")
        self.assertEqual(lim["batch"], 0)
        self.assertEqual(lim["ubatch"], 0)

    def test_a_shipped_kit_that_says_nothing_gets_no_flags(self):
        lim = self._limits({})
        self.assertEqual((lim["kv_type"], lim["batch"], lim["ubatch"]), ("", 0, 0))


class PlanKnobsTests(unittest.TestCase):
    """plan() must state the knobs boot() will really send."""

    HW = {
        "ram_bytes": 32 * 1024**3,
        "ram_gib": 32,
        "vram_bytes": 8 * 1024**3,
        "vram_gib": 8,
        "threads": 16,
        "llama_binary": True,
        "ssd_stream": True,
    }

    def _plan(self, rec, cfg=None):
        captured = {}

        def fake_resolve(**kw):
            captured.update(kw)
            return {
                "ngl": 99, "threads": 16, "mode": "gpu_full", "source": "auto", "reason": "fits_vram",
                "device": "GPU", "vram_free_mib": 7000, "vram_total_mib": 8187, "backends": ["cuda"],
                "host": "h", "backend": "cuda", "engine_dir": "", "gpu_ok": True, "fallback": False,
                "known_devices": True, "signature": "s",
            }

        with patch("lygo_engine.probe", return_value=dict(self.HW)), patch(
            "lygo_engine.perf.resolve", side_effect=fake_resolve
        ), patch.object(paths, "_CFG_CACHE", cfg or {}):
            return lygo_engine.plan(rec), captured

    def test_plan_hands_the_kv_figure_to_the_planner(self):
        rec = {"id": "qwen", "path": "no-such.gguf", "kind": "chat", "architecture": "qwen2", "ctx": 8192}
        p, captured = self._plan(rec)
        self.assertEqual(captured["kv_mib"], perf.KV_UNKNOWN_MIB)  # unreadable header, flat charge
        self.assertEqual(p["llama"]["kv_mib"], perf.KV_UNKNOWN_MIB)
        self.assertEqual(p["llama"]["ctx"], 8192)

    def test_the_knobs_travel_with_the_plan(self):
        rec = {"id": "qwen", "path": "no-such.gguf", "kind": "chat", "architecture": "qwen2"}
        p, _ = self._plan(rec, cfg={"kv_type": "q8_0", "batch": 2048, "ubatch": 1024})
        self.assertEqual(p["llama"]["kv_type"], "q8_0")
        self.assertEqual(p["llama"]["batch"], 2048)
        self.assertEqual(p["llama"]["ubatch"], 1024)

    def test_the_cache_is_sized_at_the_context_the_engine_will_run(self):
        """A 32k-native model under a 16k cap: charge 16k of cache, not 32k (measured live)."""
        with tempfile.TemporaryDirectory() as td:
            gguf = write_dim_gguf(Path(td) / "m.gguf")
            rec = {
                "id": "dim-model",
                "path": str(gguf),
                "kind": "chat",
                "architecture": "qwen2",
                "ctx": 32768,
            }
            p, captured = self._plan(rec, cfg={"ctx_max": 16384, "kv_type": "q8_0"})
        self.assertEqual(p["llama"]["ctx"], 16384)
        self.assertEqual(captured["kv_mib"], 448)  # 57344 B/token x 16384 x q8_0
        self.assertEqual(p["llama"]["kv_mib"], 448)

    def test_the_same_model_unquantised_costs_twice_the_cache(self):
        with tempfile.TemporaryDirectory() as td:
            gguf = write_dim_gguf(Path(td) / "m.gguf")
            rec = {"id": "dim-model", "path": str(gguf), "kind": "chat", "architecture": "qwen2", "ctx": 32768}
            _, captured = self._plan(rec, cfg={"ctx_max": 16384})
        self.assertEqual(captured["kv_mib"], 896)

    def test_a_junk_kv_type_in_config_becomes_f16(self):
        rec = {"id": "qwen", "path": "no-such.gguf", "kind": "chat", "architecture": "qwen2"}
        p, _ = self._plan(rec, cfg={"kv_type": "q3_k_m"})
        self.assertEqual(p["llama"]["kv_type"], "f16")


class TunedFlagFallbackTests(unittest.TestCase):
    """A performance flag that breaks a launch must cost speed, never the brain."""

    def _boot(self, calls_recorder, fail_first: int = 4):
        """fail_first: the layer ladder tries 99/49/8/0, so four failures exhaust it."""

        def fake_spawn(**kw):
            calls_recorder.append(kw)
            if len(calls_recorder) <= fail_first:
                raise RuntimeError("llama-server exited 1")
            return object()

        plan = {
            "backend": "llama",
            "llama": {
                "ngl": 99, "threads": 16, "mmap": True, "mlock": False, "flash_attn": False,
                "kv_type": "q8_0", "kv_mib": 448, "ctx": 16384, "batch": 2048, "ubatch": 1024,
            },
            "perf": {"host": "h", "backend": "cuda"},
            "hardware": {"ram_bytes": 32 * 1024**3, "ram_gib": 32},
            "limits": {"ctx_max": 16384},
        }
        td = tempfile.TemporaryDirectory()
        gguf = Path(td.name) / "tiny.gguf"
        import gguf_header

        gguf_header.write_tiny_gguf(gguf)
        rec = {"id": "tiny", "path": str(gguf), "kind": "chat", "ctx": 16384, "bytes": gguf.stat().st_size}
        state: dict = {}
        with patch("lygo_engine.plan", return_value=plan), patch(
            "lygo_engine.resolve_binary", return_value=Path(td.name) / "llama-server.exe"
        ), patch("lygo_engine.spawn_runner", side_effect=fake_spawn), patch(
            "lygo_engine.runner_for", return_value=None
        ), patch.object(
            engine, "ENGINE_LOCK", threading.Lock()
        ), patch(
            "perf.remember_host", lambda *a, **kw: {}
        ):
            status = lygo_engine.boot(rec, api_key="k", state=state)
        td.cleanup()
        return status, state

    def test_tuned_flags_are_sent_when_they_work(self):
        calls: list = []
        status, state = self._boot(calls, fail_first=0)
        self.assertEqual(status, "ready", state)
        self.assertEqual(calls[0]["kv_type"], "q8_0")
        self.assertEqual(calls[0]["batch"], 2048)
        self.assertEqual(calls[0]["ubatch"], 1024)

    def test_a_flag_failure_retries_with_the_shipped_defaults(self):
        """Four layer attempts fail, then the last resort runs with the shipped flags."""
        calls: list = []
        status, state = self._boot(calls)
        self.assertEqual(status, "ready", state)
        self.assertEqual(len(calls), 5)
        self.assertEqual(calls[4]["kv_type"], "")
        self.assertEqual(calls[4]["batch"], 0)
        self.assertEqual(calls[4]["ubatch"], 0)
        self.assertFalse(calls[4]["flash_attn"])
        self.assertIn("shipped defaults", state["perf_fallback"])

    def test_the_effective_profile_names_the_flags_that_ran(self):
        calls: list = []
        _, state = self._boot(calls)
        eff = state["lygo_engine"]["perf"]["effective"]
        self.assertEqual(eff["kv_type"], "")
        self.assertEqual(eff["batch"], 0)


def write_dim_gguf(path: Path, *, mode: str = "gqa", native_ctx: int = 32768) -> Path:
    """A GGUF v3 header with architecture dimensions, in the order a converter writes them.

    mode: gqa (grouped heads declared), mha (no kv head key = full attention), none.
    """
    a = "qwen2"
    U32, S = gguf_header.UINT32, gguf_header.STRING
    pairs: list = [
        ("general.architecture", S, a),
        ("general.name", S, "dim-model"),
        (f"{a}.block_count", U32, 28),
        (f"{a}.context_length", U32, native_ctx),
        (f"{a}.embedding_length", U32, 3584),
    ]
    if mode in ("gqa", "mha"):
        pairs.append((f"{a}.attention.head_count", U32, 28))
    if mode == "gqa":
        pairs.append((f"{a}.attention.head_count_kv", U32, 4))

    def wstr(s: str) -> bytes:
        b = s.encode("utf-8")
        return struct.pack("<Q", len(b)) + b

    body = b""
    for key, typ, val in pairs:
        body += wstr(key) + struct.pack("<I", typ)
        body += wstr(val) if typ == S else struct.pack("<I", val)
    path.write_bytes(b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", len(pairs)) + body)
    return path


class GgufHeaderDimTests(unittest.TestCase):
    """The KV cache size is read from the model's own header, whatever order it writes keys in.

    This is the trap that made the first two attempts at this estimate silently useless: GGUF
    writers put <arch>.context_length BEFORE <arch>.attention.head_count*, and the header parser
    stops as soon as it has what it was asked for. Stopping at context_length meant
    attention.head_count_kv was never seen, so every model fell back to a flat allowance.
    """

    ARCH = "qwen2"

    def _write(self, path: Path, *, mode: str = "gqa") -> Path:
        return write_dim_gguf(path, mode=mode)

    def test_the_cache_is_read_when_context_length_comes_first(self):
        with tempfile.TemporaryDirectory() as td:
            h = gguf_header.parse_gguf_header(self._write(Path(td) / "m.gguf"))
        bpt = perf.kv_bytes_per_token(h["found"])
        self.assertEqual(bpt, 57344)  # 28 layers x 4 kv heads x (128+128) x 2 bytes
        self.assertEqual(perf.kv_cache_mib(bpt, 8192), 448)
        self.assertEqual(perf.kv_cache_mib(bpt, 16384, "q8_0"), 448)

    def test_no_kv_head_key_means_full_attention_not_a_guess(self):
        """A model without attention.head_count_kv is MHA: the attention head count IS the KV count."""
        with tempfile.TemporaryDirectory() as td:
            h = gguf_header.parse_gguf_header(self._write(Path(td) / "m.gguf", mode="mha"))
        self.assertEqual(perf.kv_bytes_per_token(h["found"]), 401408)  # 28 heads, not 4

    def test_a_header_with_no_attention_dimensions_gets_the_flat_allowance(self):
        with tempfile.TemporaryDirectory() as td:
            h = gguf_header.parse_gguf_header(self._write(Path(td) / "m.gguf", mode="none"))
        self.assertIsNone(perf.kv_bytes_per_token(h["found"]))
        self.assertEqual(perf.kv_cache_mib(None, 16384, "q8_0"), perf.KV_UNKNOWN_MIB)


class EngineKeyTests(unittest.TestCase):
    """The engine key is generated per install; a placeholder in the file is not a permanent answer."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self._real = auth.LLAMA_KEY_PATH
        auth.LLAMA_KEY_PATH = Path(self._td.name) / ".llama_api_key"

    def tearDown(self):
        auth.LLAMA_KEY_PATH = self._real
        self._td.cleanup()

    def test_a_placeholder_key_is_replaced(self):
        auth.LLAMA_KEY_PATH.write_text("secret-key", encoding="utf-8")
        got = auth.ensure_llama_key()
        self.assertNotEqual(got, "secret-key")
        self.assertGreaterEqual(len(got), auth.MIN_KEY_CHARS)
        self.assertEqual(auth.LLAMA_KEY_PATH.read_text(encoding="utf-8").strip(), got)

    def test_a_short_key_is_replaced(self):
        auth.LLAMA_KEY_PATH.write_text("abc123", encoding="utf-8")
        self.assertNotEqual(auth.ensure_llama_key(), "abc123")

    def test_a_real_key_is_kept_stable(self):
        real = secrets.token_urlsafe(32)
        auth.LLAMA_KEY_PATH.write_text(real, encoding="utf-8")
        self.assertEqual(auth.ensure_llama_key(), real)
        self.assertEqual(auth.ensure_llama_key(), real)  # and stays stable across calls

    def test_a_missing_key_is_generated(self):
        self.assertFalse(auth.LLAMA_KEY_PATH.exists())
        got = auth.ensure_llama_key()
        self.assertGreaterEqual(len(got), auth.MIN_KEY_CHARS)
        self.assertTrue(auth.LLAMA_KEY_PATH.is_file())


class PortalPerfWiringTests(unittest.TestCase):
    """The console reports tok/s per turn; the portal has to actually show it."""

    def setUp(self):
        self.js = (Path(__file__).resolve().parents[1] / "portal" / "app.js").read_text(encoding="utf-8")

    def test_both_turn_paths_feed_the_readout(self):
        self.assertIn("notePerf(evn.perf)", self.js)  # streamed turn
        self.assertIn("notePerf(j.perf)", self.js)  # non-streamed turn

    def test_the_readout_says_tokens_per_second(self):
        self.assertIn("tok/s gen", self.js)
        self.assertIn("tok/s prefill", self.js)

    def test_a_console_without_perf_does_not_break_the_readout(self):
        """An older console sends no perf block: the line must degrade, not print undefined."""
        self.assertIn("typeof perf !== \"object\"", self.js)


class ForeignDaemonProbeIsBoundedAndHeld(unittest.TestCase):
    """`GET /api/health` may not spend a second waiting out a probe nobody asked for.

    Measured on this box 2026-09-25: the health payload took 1,135 ms, of which `foreign_daemon_port_open`
    was 1,000.6 ms - a timeout expiring, not an answer arriving - while every other probe in that payload
    came in under 20 ms. The portal polls this route every 3 seconds (`setInterval(refreshHealth, 3000)`).
    The cause is the host, not the daemon: a TCP connect to a CLOSED loopback port here fails after
    ~2,007 ms with WinError 10061, so one urlopen with `timeout=1` could only ever wait out its full
    timeout before answering False. The probe now bounds that with a socket timeout and holds its answer,
    so a poll pays it at most once per `FOREIGN_TTL_S`. Before the fix: cold 1,000.6 ms / held 1,093 ms
    for the whole payload. After: cold 250 ms / held 20.6 ms.
    """

    def setUp(self) -> None:
        engine.forget_foreign_reading()

    def tearDown(self) -> None:
        engine.forget_foreign_reading()

    def test_the_probe_answers_quickly_whatever_the_port_does(self) -> None:
        t0 = time.perf_counter()
        out = engine.foreign_daemon_port_open()
        took = time.perf_counter() - t0
        self.assertIsInstance(out, bool)
        self.assertLess(took, 0.9, "the probe must not wait out the old one-second timeout")

    def test_the_answer_is_held_so_a_three_second_poll_cannot_pay_twice(self) -> None:
        engine.foreign_daemon_port_open()                     # pays once
        t0 = time.perf_counter()
        engine.foreign_daemon_port_open()                     # must not pay again
        self.assertLess(time.perf_counter() - t0, 0.05, "a held reading must be free to re-read")
        engine.forget_foreign_reading()                       # and a re-probe is still possible
        self.assertIsInstance(engine.foreign_daemon_port_open(), bool)

    def test_the_health_payload_is_not_held_hostage_by_that_probe(self) -> None:
        server.health_payload(local_caller=True, authenticated=True)   # cold: pays the probe once
        t0 = time.perf_counter()
        out = server.health_payload(local_caller=True, authenticated=True)
        took = (time.perf_counter() - t0) * 1000
        self.assertTrue(out.get("ok"))
        self.assertIn("foreign_daemon_port_open", out)
        self.assertLess(took, 500, f"a held health poll should be tens of ms, took {took:.0f} ms")


if __name__ == "__main__":
    unittest.main()
