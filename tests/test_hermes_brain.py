"""The Manager brain is an isolated Hermes. It must not be the desktop profile."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import hermes_brain as hb  # noqa: E402


class IsolationTests(unittest.TestCase):
    def test_the_kit_home_is_not_a_desktop_profile(self):
        desktop = Path(tempfile.mkdtemp()) / "hermes"
        desktop.mkdir()
        kit = Path(tempfile.mkdtemp()) / "data" / "hermes-engine"
        self.assertFalse(hb.is_isolated(desktop, [desktop]))
        self.assertFalse(hb.is_isolated(desktop / "profiles" / "other", [desktop]))
        self.assertTrue(hb.is_isolated(kit, [desktop]))

    def test_prepare_refuses_a_desktop_home(self):
        desktop = Path(tempfile.mkdtemp()) / "hermes"
        desktop.mkdir()
        with self.assertRaises(RuntimeError):
            with mock.patch.object(hb, "desktop_homes", lambda: [desktop]):
                hb.prepare(root=desktop)

    def test_prepare_writes_a_loopback_custom_provider_and_no_key(self):
        root = Path(tempfile.mkdtemp()) / "hermes-engine"
        secret = "super-secret-engine-key-value"
        info = hb.prepare(model="gemma-test", engine_port=11441, console_port=9641, root=root)
        text = (root / "config.yaml").read_text(encoding="utf-8")
        self.assertIn("provider: custom", text)
        self.assertIn("http://127.0.0.1:11441/v1", text)
        self.assertIn("LYGO_ENGINE_API_KEY", text)
        self.assertNotIn(secret, text)
        self.assertNotIn("api_key:", text)
        joined = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in root.rglob("*") if p.is_file())
        self.assertNotIn(secret, joined)
        self.assertNotIn("9119", joined)
        plugin = (root / "plugins" / "lygo-console-limb" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("console_not_loopback", plugin)
        self.assertIn("/api/limb", plugin)
        self.assertIn("lygo_roster", plugin)
        self.assertIn("/api/roster", plugin)
        self.assertIn("external_dirs", text)
        self.assertIn("plugins:\n  enabled:\n    - lygo-console-limb", text)
        self.assertIn("tool_search:\n    enabled: off", text)
        self.assertIn("connectors:\n    enabled: false", text)
        self.assertIn("memory_enabled: true", text)
        self.assertIn("user_profile_enabled: true", text)
        for kept in ("terminal", "web", "memory"):
            self.assertNotIn(f"    - {kept}\n", text)
        for held in ("browser", "image_gen", "cronjob", "delegation", "computer_use", "code_execution"):
            self.assertIn(f"    - {held}", text)
        soul = (root / "SOUL.md").read_text(encoding="utf-8")
        user = (root / "USER.md").read_text(encoding="utf-8")
        memory = (root / "MEMORY.md").read_text(encoding="utf-8")
        self.assertIn("architect", soul)
        self.assertIn("github-operator", soul)
        self.assertIn("DeepSeekOracle", user)
        self.assertIn("9641", memory)
        self.assertLess(len(user), 1375)
        self.assertLess(len(memory), 2200)
        self.assertIn("lygo-lattice-manager", (root / "skills" / "lygo-lattice-manager" / "SKILL.md").read_text(encoding="utf-8"))
        manifest = (root / "plugins" / "lygo-console-limb" / "plugin.yaml").read_text(encoding="utf-8")
        self.assertIn("kind: standalone", manifest)
        folded = text.replace("\\", "/")
        self.assertNotIn("AppData/Local/hermes", folded)
        self.assertNotIn("/.hermes", folded)
        self.assertTrue(info["isolated"])
        self.assertTrue(info["home"].endswith("hermes-engine") or info["home"].endswith("hermes-engine" + "\\") or "hermes-engine" in info["home"])


class EnvTests(unittest.TestCase):
    def test_the_child_env_keeps_the_isolated_home_and_drops_desktop_keys(self):
        root = Path(tempfile.mkdtemp()) / "hermes-engine"
        secret = "desktop-openai-key-should-not-pass"
        env = hb.child_env(root, "engine-key-0123456789", {
            "PATH": "C:\\Windows",
            "OPENAI_API_KEY": secret,
            "HERMES_HOME": "<USER>\\AppData\\Local\\hermes",
            "LOCALAPPDATA": "<USER>\\AppData\\Local",
        })
        self.assertEqual(env["HERMES_HOME"], str(root))
        self.assertEqual(env["LYGO_ENGINE_API_KEY"], "engine-key-0123456789")
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn(secret, " ".join(env.values()))
        self.assertIn("PATH", env)


class TurnTests(unittest.TestCase):
    def test_wants_turn_skips_images_mock_pytest_and_the_off_switch(self):
        self.assertFalse(hb.wants_turn(True))
        self.assertFalse(hb.wants_turn(False, mock=True))
        kept = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
        kept["LYGO_HERMES_BRAIN"] = "1"
        with mock.patch.dict(os.environ, kept, clear=True):
            self.assertTrue(hb.wants_turn(False))
        with mock.patch.dict(os.environ, {"LYGO_HERMES_BRAIN": "0"}):
            self.assertFalse(hb.wants_turn(False))
        with mock.patch.dict(os.environ, {"PYTEST_CURRENT_TEST": "tests/test_x.py::t"}):
            self.assertFalse(hb.wants_turn(False))

    def test_a_missing_binary_does_not_pretend_to_answer(self):
        with mock.patch.object(hb, "find_binary", lambda: ""):
            got = hb.answer_turn(user_text="hello", runner=None, root=Path(tempfile.mkdtemp()))
        self.assertFalse(got["handled"])
        self.assertEqual(got["reason"], "binary_missing")

    def test_oneshot_uses_the_selected_model_and_never_stops_a_server(self):
        root = Path(tempfile.mkdtemp()) / "hermes-engine"
        seen = {}

        def runner(argv, env, cwd, on_wait, timeout):
            seen["argv"] = argv
            seen["env"] = env
            seen["cwd"] = cwd
            seen["timeout"] = timeout
            self.assertTrue((cwd / "turn.txt").is_file())
            self.assertIn("hello from the console", (cwd / "turn.txt").read_text(encoding="utf-8"))
            return {"code": 0, "stdout": "hermes says hello", "stderr": ""}

        with mock.patch.object(hb, "find_binary", lambda: "hermes.cmd"):
            got = hb.answer_turn(
                user_text="hello from the console",
                messages=[{"role": "user", "content": "hello from the console"}],
                model="gemma-4-test",
                engine_port=11441,
                console_port=9641,
                api_key="engine-key-0123456789",
                runner=runner,
                root=root,
            )
        argv = seen["argv"]
        self.assertIn("-z", argv)
        self.assertIn("-m", argv)
        self.assertEqual(argv[argv.index("-m") + 1], "gemma-4-test")
        self.assertIn("--provider", argv)
        self.assertEqual(argv[argv.index("--provider") + 1], "custom")
        self.assertNotIn("--stop", argv)
        self.assertNotIn("serve", argv)
        self.assertNotIn("--tui", argv)
        self.assertEqual(seen["env"]["HERMES_HOME"], str(root))
        self.assertNotIn("engine-key-0123456789", got["text"])
        self.assertEqual(got["text"], "hermes says hello")
        self.assertTrue(got["handled"])
        self.assertIsNone(seen["timeout"])
        self.assertNotIn("hermes timed out", (ROOT / "src" / "hermes_brain.py").read_text(encoding="utf-8"))
        cfg = (root / "config.yaml").read_text(encoding="utf-8")
        self.assertIn("gemma-4-test", cfg)
        self.assertIn("http://127.0.0.1:11441/v1", cfg)

    def test_api_route_uses_the_loopback_gateway_then_falls_back_local(self):
        root = Path(tempfile.mkdtemp()) / "hermes-engine"
        seen = []

        def runner(argv, env, cwd, on_wait, timeout):
            del env, on_wait, timeout
            cfg = (cwd / "config.yaml").read_text(encoding="utf-8")
            seen.append({"argv": argv, "cfg": cfg})
            if "kilo-auto/free" in argv:
                return {"code": 1, "stdout": "", "stderr": "429 rate_limit"}
            return {"code": 0, "stdout": "local fallback", "stderr": ""}

        with mock.patch.object(hb, "find_binary", lambda: "hermes.cmd"):
            with mock.patch.object(hb, "_loopback_up", lambda base: True):
                got = hb.answer_turn(
                    user_text="hello",
                    model="QwenPaw Heretic",
                    engine_port=11441,
                    api_key="engine-key-0123456789",
                    runner=runner,
                    root=root,
                    api_route={
                        "base": "http://127.0.0.1:18900/v1",
                        "model": "kilo-auto/free",
                        "key": "ofm-key",
                    },
                )
        self.assertEqual(len(seen), 2)
        self.assertIn("kilo-auto/free", seen[0]["argv"])
        self.assertIn("http://127.0.0.1:18900/v1", seen[0]["cfg"])
        self.assertIn("QwenPaw Heretic", seen[1]["argv"])
        self.assertIn("http://127.0.0.1:11441/v1", seen[1]["cfg"])
        self.assertEqual(got["text"], "local fallback")
        self.assertEqual(got["via"], "local")

    def test_dead_loopback_api_skips_straight_to_local(self) -> None:
        root = Path(tempfile.mkdtemp()) / "hermes-engine"
        seen = []

        def runner(argv, env, cwd, on_wait, timeout):
            del env, cwd, on_wait, timeout
            seen.append(argv[argv.index("-m") + 1])
            return {"code": 0, "stdout": "local only", "stderr": ""}

        with mock.patch.object(hb, "find_binary", lambda: "hermes.cmd"):
            with mock.patch.object(hb, "_loopback_up", lambda base: False):
                got = hb.answer_turn(
                    user_text="hello",
                    model="QwenPaw Heretic",
                    runner=runner,
                    root=root,
                    api_route={"base": "http://127.0.0.1:18900/v1", "model": "nvidia/nemotron-3.5-super-vl:free", "key": "x"},
                )
        self.assertEqual(seen, ["QwenPaw Heretic"])
        self.assertEqual(got["via"], "local")
        self.assertEqual(got["text"], "local only")

    def test_sync_harness_points_the_rails_at_the_api(self):
        root = Path(tempfile.mkdtemp()) / "hermes-engine"
        with mock.patch.object(hb, "_loopback_up", lambda base: True):
            info = hb.sync_harness(
                model="QwenPaw Heretic",
                engine_port=11441,
                console_port=9641,
                root=root,
                api_route={"base": "http://127.0.0.1:18900/v1", "model": "nvidia/nemotron-3.5-super-vl:free"},
            )
        cfg = (root / "config.yaml").read_text(encoding="utf-8")
        self.assertIn("nvidia/nemotron-3.5-super-vl:free", cfg)
        self.assertIn("http://127.0.0.1:18900/v1", cfg)
        self.assertEqual(info["model"], "nvidia/nemotron-3.5-super-vl:free")
        src = hb._plugin_source()
        self.assertIn("lygo_harness", src)
        soul = hb._soul()
        self.assertIn("standalone brain", soul)
        self.assertIn("skin and harness", soul)

    def test_a_spaced_model_name_stays_one_argument(self):
        root = Path(tempfile.mkdtemp())
        agent = root / "hermes-agent" / "hermes_cli"
        agent.mkdir(parents=True)
        (agent / "main.py").write_text("# stub\n", encoding="utf-8")
        python = root / "python.exe"
        python.write_bytes(b"")
        cmd = root / "bin" / "hermes.cmd"
        cmd.parent.mkdir()
        cmd.write_text(f'"{python}" -I -c "print(1)" %*\n', encoding="utf-8")
        launch = hb.launch_argv([str(cmd), "-m", "QwenPaw Heretic", "-z", "Ready?"])
        self.assertNotIn("cmd.exe", launch[0].lower())
        self.assertEqual(launch[launch.index("-m") + 1], "QwenPaw Heretic")
        self.assertEqual(launch[launch.index("-z") + 1], "Ready?")
        self.assertNotIn("cmd.exe", " ".join(launch[:1]).lower())


if __name__ == "__main__":
    unittest.main()
