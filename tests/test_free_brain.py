"""The free brain: the shim's shape, and the switch that points the console's turns at it.

Two things are proven here that a card cannot show by itself:
  * the shim answers in the exact shape the console parses, and a failure comes back as the CLI's
    own words - never as a diagnosis this kit invented;
  * turning the switch ON really engages cloud_api's chain (a keyless provider row is dropped in
    silence, which is why a placeholder key is saved), and turning it OFF restores the provider,
    model and on/off that were there before - WITHOUT copying any key material into our state.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import cloud_api  # noqa: E402
import free_brain as fb  # noqa: E402


@pytest.fixture()
def api_json(tmp_path, monkeypatch):
    """An isolated config/api.json: the operator's real one is never touched by a test."""
    path = tmp_path / "api.json"
    path.write_text(json.dumps({
        "provider": "deepseek", "model": "deepseek-chat", "url": "", "enabled": True,
        "key": "not-a-real-key-1", "keys": {"deepseek": "not-a-real-key-1"},
    }), encoding="utf-8")
    monkeypatch.setattr(cloud_api, "API_PATH", path)
    return path


@pytest.fixture()
def shim():
    got = fb.serve(port=0)
    assert got.get("ok"), got
    yield got
    fb.stop()


def _post(url, body, timeout=30):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                headers={"Content-Type": "application/json",
                                         "Authorization": "Bearer " + fb.PLACEHOLDER_KEY})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def test_shim_answers_in_the_shape_the_console_parses(shim, monkeypatch):
    seen = {}

    def fake(prompt, model, timeout=0):
        seen["prompt"], seen["model"] = prompt, model
        return {"ok": True, "answer": "FREE_BRAIN_OK"}

    monkeypatch.setattr(fb, "_answer_via_cli", fake)
    code, body = _post(shim["url"] + "/v1/chat/completions", {
        "model": "opencode/glm-5.3-flash-free",
        "messages": [{"role": "system", "content": "be brief"},
                     {"role": "user", "content": "say FREE_BRAIN_OK"}],
        "max_tokens": 16, "stream": False, "tools": [{"type": "function"}]})
    assert code == 200, body
    assert body["choices"][0]["message"]["content"] == "FREE_BRAIN_OK"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["model"] == "opencode/glm-5.3-flash-free"
    # the roles reach the CLI, and the tool schema is not smuggled into the prompt
    assert "SYSTEM: be brief" in seen["prompt"] and "USER: say FREE_BRAIN_OK" in seen["prompt"]
    assert seen["model"] == "opencode/glm-5.3-flash-free"


def test_shim_lists_the_roster(shim):
    with urllib.request.urlopen(shim["url"] + "/v1/models", timeout=15) as r:
        body = json.loads(r.read().decode())
    assert body["object"] == "list" and body["data"]


def test_shim_answers_404_for_an_unknown_route(shim):
    code, body = _post(shim["url"] + "/v1/completions", {"model": "x", "messages": []})
    assert code == 404 and "no such route" in body["error"]["message"]


def test_a_failure_is_the_cli_own_words_not_a_diagnosis(shim, monkeypatch):
    monkeypatch.setattr(fb, "_answer_via_cli", lambda *a, **k: {
        "ok": False, "error": "engine_masked_error",
        "hint": "run subagent_pool(probe=true)",
        "engine_error": [{"name": "UnknownError", "message": "Unexpected server error.", "ref": "err_abc"}]})
    code, body = _post(shim["url"] + "/v1/chat/completions",
                       {"model": "opencode/retired-model-free", "messages": [{"role": "user", "content": "hi"}]})
    assert code == 502
    said = body["error"]["message"]
    assert "engine_masked_error" in said and "err_abc" in said
    # the four look-alikes are never collapsed into one of them
    for claim in ("rate limit", "out of quota", "retired", "down"):
        assert claim not in said.lower()


def test_a_long_prompt_is_handed_over_as_a_file_not_through_argv(shim, monkeypatch):
    """Measured: 18,168 characters on the command line came back exit 0 with no answer in 0.06 s --
    the npm .CMD wrapper re-parses its line through cmd.exe, whose limit is ~8191. The text goes in a
    file and the argv carries one sentence naming it (proven working at 17,783 characters)."""
    seen = {}

    def fake(task, model, timeout=0):
        seen["task"] = task
        return {"ok": True, "answer": "FILE_ROUTE_OK"}

    monkeypatch.setattr(fb, "_answer_via_cli", fake)
    long_text = "x" * (fb.ARGV_PROMPT_CHARS + 500)
    code, body = _post(shim["url"] + "/v1/chat/completions", {
        "model": "opencode/glm-5.3-flash-free",
        "messages": [{"role": "user", "content": long_text}]})
    assert code == 200, body
    assert body["choices"][0]["message"]["content"] == "FILE_ROUTE_OK"
    assert len(seen["task"]) < 400 and "Read the file" in seen["task"]   # argv stayed short
    path = Path(seen["task"].split("Read the file ", 1)[1].split(" completely", 1)[0])
    assert path.exists()
    assert path.read_text(encoding="utf-8").count("x") > fb.ARGV_PROMPT_CHARS
    assert fb._SERVER["requests"][-1]["via"] == "file"
    assert body["lygo"]["transport"] == "file"


def test_a_short_prompt_still_goes_through_argv(shim, monkeypatch):
    seen = {}
    monkeypatch.setattr(fb, "_answer_via_cli",
                        lambda task, model, timeout=0: (seen.setdefault("task", task),
                                                        {"ok": True, "answer": "ARGV_OK"})[1])
    code, _ = _post(shim["url"] + "/v1/chat/completions",
                    {"model": "opencode/glm-5.3-flash-free",
                     "messages": [{"role": "user", "content": "say ARGV_OK"}]})
    assert code == 200 and "Read the file" not in seen["task"]
    assert fb._SERVER["requests"][-1]["via"] == "argv"


def test_a_prompt_over_the_argv_ceiling_refuses_by_name_and_spends_nothing(shim, monkeypatch):
    called = {"n": 0}

    def spy(*a, **k):
        called["n"] += 1
        return {"ok": True, "answer": "should not happen"}

    monkeypatch.setattr(fb, "_answer_via_cli", spy)
    code, body = _post(shim["url"] + "/v1/chat/completions", {
        "model": "opencode/glm-5.3-flash-free",
        "messages": [{"role": "user", "content": "x" * (fb.MAX_PROMPT_CHARS + 1)}]})
    assert code == 502
    assert body["error"]["type"] == "lygo_free_brain_prompt_too_long"
    # the prompt is the message plus the house head and role labels, so it is over the ceiling -
    # not equal to it: assert the CONTRACT (over the limit, and the number reported back)
    assert body["error"]["prompt_chars"] > fb.MAX_PROMPT_CHARS
    assert body["error"]["prompt_chars"] > 24000
    assert called["n"] == 0                      # the CLI was never spawned
    assert fb._SERVER["requests"][-1]["code"] == 502


def test_switch_on_engages_the_chain_and_off_restores_it(api_json, tmp_path):
    state = tmp_path / "free_brain.json"
    before = cloud_api.public_status()
    assert before["provider"] == "deepseek" and before["enabled"] is True

    got = fb.on("opencode/glm-5.3-flash-free", state)
    assert got["ok"], got
    st = cloud_api.public_status()
    # the row must actually be IN the chain, or the console would never use it
    assert st["provider"] == "custom" and st["enabled"] is True
    assert st["has_key"] is True and "custom" in [c["provider"] for c in cloud_api.chain_for()]
    assert st["model"] == "opencode/glm-5.3-flash-free"
    assert "127.0.0.1" in st["url"] and st["url"].endswith("/v1/chat/completions")

    saved = json.loads(api_json.read_text(encoding="utf-8"))
    assert saved["keys"]["custom"] == fb.PLACEHOLDER_KEY          # the row's placeholder
    assert saved["keys"]["deepseek"] == "not-a-real-key-1"        # the operator's key is untouched

    # the state file records what to restore - and never a key
    text = state.read_text(encoding="utf-8")
    assert "not-a-real-key-1" not in text          # never the operator's key material
    saved_state = json.loads(text)
    assert saved_state["placeholder_key"] == fb.PLACEHOLDER_KEY   # recorded, because it is not one
    assert saved_state["previous"]["provider"] == "deepseek"

    back = fb.off(state)
    assert back["ok"], back
    after = cloud_api.public_status()
    assert after["provider"] == "deepseek" and after["model"] == "deepseek-chat"
    assert after["enabled"] is True
    assert fb.listening() is False


def test_the_adapter_comes_back_when_the_switch_is_already_on(tmp_path):
    """Measured: a console restarted with the switch on paid one failed hop per turn, because the
    config still pointed at a shim that was no longer listening."""
    state = tmp_path / "free_brain.json"
    state.write_text(json.dumps({"enabled": True, "model": "opencode/glm-5.3-flash-free",
                                 "url": "", "previous": {"provider": "deepseek"}}), encoding="utf-8")
    assert fb.listening() is False
    got = fb.ensure_if_enabled(state)
    assert got["ok"] is True and got["healed"] is True and fb.listening() is True
    try:
        again = fb.ensure_if_enabled(state)
        assert again["healed"] is False and again["why"] == "already listening"
    finally:
        fb.stop()


def test_the_adapter_stays_off_when_the_switch_is_off(tmp_path):
    state = tmp_path / "free_brain.json"
    state.write_text(json.dumps({"enabled": False, "model": ""}), encoding="utf-8")
    got = fb.ensure_if_enabled(state)
    assert got["healed"] is False and fb.listening() is False
    # and with no state file at all it must not invent one
    assert fb.ensure_if_enabled(tmp_path / "absent.json")["healed"] is False


def test_switch_refuses_with_no_model_and_changes_nothing(api_json, tmp_path):
    state = tmp_path / "free_brain.json"
    got = fb.on("   ", state)
    assert got["ok"] is False and got["error"] == "no_model"
    assert cloud_api.public_status()["provider"] == "deepseek"


def test_off_with_no_state_turns_the_api_side_off(api_json, tmp_path):
    got = fb.off(tmp_path / "never-written.json")
    assert got["ok"] is True and got["restored"]["enabled"] is False


def test_status_says_whether_the_shim_is_the_one_answering(shim, api_json, tmp_path):
    state = tmp_path / "free_brain.json"
    fb.on("opencode/space-bunny-free", state)
    st = fb.status(state)
    assert st["enabled"] is True and st["cloud"]["points_at_shim"] is True
    assert st["model"] == "opencode/space-bunny-free" and st["shim"]["listening"] is True
    # and it is a CHECK, not a memory: stop the shim and the same status stops agreeing
    fb.stop()
    assert fb.status(state)["cloud"]["points_at_shim"] is False
