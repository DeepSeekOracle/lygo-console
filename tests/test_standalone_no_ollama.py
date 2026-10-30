"""The console must be standalone: own engine, own model vault, no daemon anywhere.

The steward's rule for this kit is a zero-Ollama system - our own backend services and
infrastructure. A legacy blob store may still be *imported* once (read-only) by
src/cas_import.py, but nothing may require it, scan it implicitly, or talk to a daemon.

These tests pin that contract so a later edit cannot quietly put the dependency back:
  1. no source file subprocesses anything called ollama,
  2. the public gateway defaults to our own engine, not a daemon,
  3. scan roots do not silently include %USERPROFILE%\\.ollama\\models,
  4. the shipped config does not list an Ollama path,
  5. the declared vault (LYGO_MODELS) is honoured,
  6. a model record the kit owns points at a file the kit owns.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import public_gateway  # noqa: E402
import server  # noqa: E402

VAULT = Path(r"<MODELS>")


class NoDaemonTests(unittest.TestCase):
    def test_nothing_subprocesses_ollama(self) -> None:
        bad = []
        for path in (ROOT / "src").glob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r"^.*subprocess.*ollama.*$", text, re.I | re.M):
                if "never subprocesses anything" in m.group(0).lower():  # the reader's own contract
                    continue
                bad.append("%s: %s" % (path.name, m.group(0).strip()[:80]))
        self.assertEqual([], bad, "the kit must never launch a daemon")

    def test_the_importer_says_it_is_read_only(self) -> None:
        text = (ROOT / "src" / "cas_import.py").read_text(encoding="utf-8")
        self.assertIn("never subprocesses anything", text.lower())
        self.assertIn("never opens a socket", text.lower())

    def test_the_public_gateway_defaults_to_our_own_engine(self) -> None:
        self.assertEqual("local", public_gateway.Handler.backend)
        self.assertIn(str(public_gateway.LLAMA_PORT), public_gateway.Handler.openai_url)

    def test_the_gateway_model_default_comes_from_our_own_registry(self) -> None:
        self.assertTrue(public_gateway._selected_model())


class ScanRootTests(unittest.TestCase):
    def test_a_clean_pc_needs_no_ollama_folder(self) -> None:
        """Simulate a machine that never ran ollama: the kit still finds a usable root."""
        with tempfile.TemporaryDirectory() as td:
            fake_home = Path(td) / "home"
            fake_home.mkdir()
            with mock.patch.dict(os.environ, {"USERPROFILE": str(fake_home)}, clear=False):
                roots = server.default_scan_roots({})
        self.assertIn(str(server.KIT_ROOT / "models"), roots)

    def test_the_home_ollama_folder_is_not_scanned_implicitly(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home_cas = Path(td) / ".ollama" / "models"
            (home_cas / "blobs").mkdir(parents=True)
            with mock.patch.dict(os.environ, {"USERPROFILE": td}, clear=False):
                os.environ.pop("OLLAMA_MODELS", None)
                roots = server.default_scan_roots({})
        self.assertNotIn(str(home_cas), roots,
                         "an Ollama folder must only be scanned when the operator maps it")

    def test_the_declared_vault_is_honoured(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"LYGO_MODELS": td}, clear=False):
                roots = server.default_scan_roots({})
        self.assertIn(str(Path(td)), roots)

    def test_the_shipped_config_lists_no_ollama_path(self) -> None:
        cfg = json.loads((ROOT / "config" / "console.json").read_text(encoding="utf-8"))
        joined = " ".join(str(x) for x in (cfg.get("scan_roots") or []))
        self.assertNotIn("ollama", joined.lower())


class OurVocabularyTests(unittest.TestCase):
    """Our words carry no other service's name. Exactly two exceptions, each with a reason.

    A migration has to be able to name what it migrates FROM, and a guard that forbids nesting a
    foreign daemon's binary inside our tree has to name what it forbids. Everything else must be
    clean, so a later edit cannot smuggle a real dependency in under cover of an exception.
    """

    # Split so this file does not itself trip the rule it enforces.
    LEGACY = "oll" + "ama"
    ALLOWED = {
        "cas_import.py": "the migration names what it migrates from, and the format's own media types",
        "engine.py": "the guard that REFUSES a nested copy of that binary in our tree",
    }

    def test_only_the_two_documented_modules_name_the_legacy_store(self) -> None:
        offenders = []
        for path in sorted((ROOT / "src").glob("*.py")):
            if path.name in self.ALLOWED:
                continue
            if self.LEGACY in path.read_text(encoding="utf-8", errors="replace").lower():
                offenders.append(path.name)
        self.assertEqual([], offenders,
                         "the legacy store's name belongs only in: %s" % ", ".join(sorted(self.ALLOWED)))

    def test_no_foreign_daemon_env_var_is_read(self) -> None:
        offenders = []
        for path in sorted((ROOT / "src").glob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            if re.search(self.LEGACY.upper() + r"_[A-Z_]+", text):
                offenders.append(path.name)
        self.assertEqual([], offenders,
                         "a kit must not resolve models through another daemon's environment")

    def test_scan_roots_never_descend_into_a_foreign_store(self) -> None:
        """A folder shaped like a store, but named after the daemon, must not become a scan root."""
        with tempfile.TemporaryDirectory() as td:
            store = Path(td) / "models" / self.LEGACY
            (store / "manifests").mkdir(parents=True)
            (store / "blobs").mkdir(parents=True)
            with mock.patch.dict(os.environ, {"LYGO_STACK_ROOT": td}, clear=False):
                roots = server.default_scan_roots({})
        self.assertNotIn(str(store), roots)

    def test_a_cas_shaped_store_under_our_own_name_IS_scanned(self) -> None:
        """The mirror of the test above: the neutral name still works, or we broke the stick."""
        with tempfile.TemporaryDirectory() as td:
            store = Path(td) / "models" / "cas"
            (store / "manifests").mkdir(parents=True)
            (store / "blobs").mkdir(parents=True)
            with mock.patch.dict(os.environ, {"LYGO_STACK_ROOT": td}, clear=False):
                roots = server.default_scan_roots({})
        self.assertIn(str(store), roots)


@unittest.skipUnless(VAULT.is_dir(), "no LYGO_MODELS vault on this machine")
class VaultTests(unittest.TestCase):
    def test_the_declared_vault_is_a_scan_candidate(self) -> None:
        roots = server.default_scan_roots({})
        self.assertIn(str(VAULT), roots)

    def test_models_the_kit_owns_live_in_a_root_the_kit_scans(self) -> None:
        """An owned model must sit in a folder THIS kit scans.

        'The vault' is `<MODELS>` on the PC and the stick's own CAS (`product\\models\\cas`)
        on the USB, so the contract has to be 'a declared scan root', not one hard-coded drive:
        pinning `I:` here failed the stick while the stick was right. Records whose file cannot be
        found are skipped - other tests patch the registry and leave temp paths behind that are gone
        by the time this runs.
        """
        roots = [Path(r) for r in server.default_scan_roots({})]
        reg = json.loads((ROOT / "save" / "registry.json").read_text(encoding="utf-8"))
        owned = [r for r in (reg.get("models") or []) if str(r.get("source")) == "lygo_vault"]
        if not owned:
            # A tree whose models were imported by the CAS reader (the USB kit) has none: its own CAS
            # is the vault. Nothing to validate here is a skip, not a failure.
            self.skipTest("no lygo_vault records on this machine - its models come from its own CAS")
        checked = 0
        for rec in owned:
            for key in ("path", "mmproj"):
                value = str(rec.get(key) or "")
                if not value or not Path(value).is_file():
                    continue
                checked += 1
                self.assertTrue(any(value.lower().startswith(str(r).lower()) for r in roots),
                                "%s %s is outside every model root this kit scans: %s"
                                % (rec.get("id"), key, value))
        if not checked:
            self.skipTest("owned records exist but none of their files are on disk any more")

    def test_the_vault_manifest_points_at_files_it_owns(self) -> None:
        manifest = VAULT / "manifest.json"
        if not manifest.is_file():
            self.skipTest("no vault manifest on this machine")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertTrue(data, "the vault holds no models")
        for mid, entry in data.items():
            for key in ("path", "mmproj"):
                value = entry.get(key)
                if not value:
                    continue
                self.assertTrue(str(value).lower().startswith(str(VAULT).lower()),
                                "vault manifest %s %s is outside the vault: %s" % (mid, key, value))
                self.assertTrue(Path(str(value)).is_file(), "vault manifest %s is missing" % value)

    def test_a_vision_model_is_registered_with_its_projector(self) -> None:
        """The registry carries a projector for a real multimodal model: pick one that still exists."""
        import image_tools

        rec = image_tools.vision_record()
        if rec is None:
            self.skipTest("this tree has no multimodal model registered")
        self.assertTrue(Path(str(rec["mmproj"])).is_file())
        self.assertTrue(Path(str(rec["path"])).is_file())


class RescanTests(unittest.TestCase):
    """A refresh run rewrote the registry once, and it took the standalone work with it.

    The scanner reports every store it finds - an imported CAS included - so its record carries that
    store's path, and the picker re-chose the default by available RAM. Both halves are pinned here.
    """

    def _patched(self, t: Path):
        import paths
        import registry as reg_mod

        return [
            mock.patch.object(paths, "SAVE", t),
            mock.patch.object(reg_mod, "SAVE", t),
            mock.patch.object(reg_mod, "REGISTRY_PATH", t / "registry.json"),
            mock.patch.object(paths, "REGISTRY_PATH", t / "registry.json"),
        ]

    def test_a_rescan_does_not_take_an_owned_model_back(self) -> None:
        import registry as reg_mod

        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            vault = t / "LYGO_MODELS"
            vault.mkdir()
            owned = vault / "owned.gguf"
            owned.write_bytes(b"gguf")
            cas = t / "cas-blob"
            cas.write_bytes(b"gguf")
            patches = self._patched(t)
            for p in patches:
                p.start()
            try:
                reg_mod.save({"models": [{"id": "m:1", "source": "lygo_vault", "path": str(owned), "kind": "chat"}],
                              "selected": "m:1", "selected_source": "operator"})
                reg_mod.upsert([{"id": "m:1", "source": "legacy_cas", "path": str(cas), "kind": "chat"}])
                data = reg_mod.load()
            finally:
                for p in patches:
                    p.stop()
        rec = [m for m in data["models"] if m["id"] == "m:1"][0]
        self.assertEqual(str(owned), str(rec["path"]), "a rescan re-pointed an owned model at a foreign store")
        self.assertEqual("lygo_vault", rec["source"])

    def test_an_operator_default_survives_a_rescan(self) -> None:
        import registry as reg_mod

        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            files = {}
            for mid in ("m:1", "m:2", "m:3"):
                f = t / (mid.replace(":", "-") + ".gguf")
                f.write_bytes(b"gguf")
                files[mid] = f
            patches = self._patched(t)
            for p in patches:
                p.start()
            try:
                reg_mod.save({"models": [{"id": "m:2", "source": "lygo_vault", "path": str(files["m:2"]), "kind": "chat"}],
                              "selected": "m:2", "selected_source": "operator"})
                reg_mod.upsert([{"id": mid, "source": "ollama_cas", "path": str(files[mid]), "kind": "chat"}
                                for mid in ("m:1", "m:2", "m:3")])
                data = reg_mod.load()
            finally:
                for p in patches:
                    p.stop()
        self.assertEqual("m:2", data.get("selected"), "a rescan re-picked the operator's default")
        self.assertEqual("operator", data.get("selected_source"))


class SilentTurnTests(unittest.TestCase):
    """The first ask after a boot must not look like a dead engine.

    A local turn streams nothing until the engine finishes prefilling its context - on this class of
    machine about 40 s for the system prompt plus the tool schemas - and the portal used to stop the
    answer after 60 s of silence. The console now primes that prefix as soon as the engine reports
    ready, and the portal waits longer for a local turn than for an API. Both only work while they
    stay in step with the chat path, which is what these pin.
    """

    def _src(self, name: str) -> str:
        return (ROOT / "src" / name).read_text(encoding="utf-8", errors="replace")

    def test_the_primer_reuses_the_opening_the_chat_path_sends(self) -> None:
        import inspect

        import server

        msg = server.local_system_message("local")
        self.assertEqual({"role", "content"}, set(msg))
        self.assertTrue(str(msg.get("content") or "").strip(), "the system message must not be empty")
        # As data, not as source text: the payload builder is a named function precisely so the primer
        # can be held to the chat path's opening as a VALUE. A docstring mentioning the right names is
        # not the same as sending them, and the earlier grep-shaped version of this test passed on the
        # docstring alone.
        payload = server.prefix_prime_payload("gemma4-12b")
        self.assertEqual(msg, payload["messages"][0], "the primer must open exactly as a turn opens")
        self.assertTrue(payload["tools"], "it must send the same tool schemas a local turn sends")
        self.assertEqual(1, payload["max_tokens"])
        self.assertFalse(payload["stream"])
        self.assertIn("local_system_message", inspect.getsource(server.prefix_prime_payload),
                      "the primer must reuse the chat path's opening")
        self.assertIn('local_system_message("api" if use_cloud else "local")', self._src("server.py"),
                      "the chat path must keep opening through the shared builder, not compose_system directly")

    def test_a_boot_primes_the_prefix(self) -> None:
        src = self._src("server.py")
        self.assertIn("warm_prefix(model_id)", src, "the boot funnel must start the primer")
        self.assertIn('if status == "ready"', src, "only a ready engine has a cache to prime")

    def test_a_local_turn_is_not_cut_by_a_page_clock(self) -> None:
        js = (ROOT / "portal" / "app.js").read_text(encoding="utf-8", errors="replace")
        self.assertNotIn("STREAM_IDLE_LOCAL_MS", js)
        self.assertNotIn("STREAM_IDLE_MS", js)
        self.assertNotIn("capWindowMs()", js)

    def test_the_console_writes_its_own_log(self) -> None:
        """It has exited on its own twice with nothing on disk to explain it."""
        src = self._src("server.py")
        self.assertIn("install_console_log", src)
        self.assertIn("console-", src)


class EmptyTurnTests(unittest.TestCase):
    """A turn that ran a limb must never reach the operator as an empty bubble.

    The engine can answer the console's "now answer in plain prose" instruction with another tool call,
    and the loop has no step left to narrate it: measured 2026-09-19 with the plainest ask there is
    ("what time is it right now?"), the model called now({}) and the console showed an empty answer while
    holding the readout. The guard has to sit BEFORE the echo and shrug guards: both fall through on an
    empty string (the shrug guard needs a word like "unknown" to match at all).
    """

    def test_a_blank_answer_with_real_tool_results_falls_back_to_the_readout(self):
        src = (ROOT / "src" / "server.py").read_text(encoding="utf-8", errors="replace")
        self.assertIn('if use_tools and traces and not str(assistant or "").strip():', src)
        guard = src.index('if use_tools and traces and not str(assistant or "").strip():')
        echo = src.index("if use_tools and traces and is_tool_call_echo(assistant, cur_msg):")
        shrug = src.index("def _limb_card_text") if False else src.index("shrug_prose = tool_prose(traces)")
        self.assertLess(guard, echo, "the empty-answer guard must run before the echo guard")
        self.assertLess(guard, shrug, "the empty-answer guard must run before the shrug guard")
        block = src[guard:guard + 1400]   # my own note above it is long
        self.assertIn("tool_prose(", block, "the fallback must be the readout the limbs actually returned")


if __name__ == "__main__":
    unittest.main()
