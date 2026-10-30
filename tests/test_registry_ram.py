"""RAM-auto brain: the console picks the biggest model THIS host can hold.

Console.json `prefer_by_ram` is what makes one stick behave correctly on an 8 GB laptop and a
64 GB tower. Sizes here mirror the real LYGO CLAW CAS (Ollama blobs).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import registry  # noqa: E402

GiB = 1024**3

MODELS = [
    {"id": "qwen2.5:1.5b", "kind": "chat", "runnable": True, "bytes": 986_000_000},
    {"id": "llama3.2:1b", "kind": "chat", "runnable": True, "bytes": 1_300_000_000},
    {"id": "qwen2.5:3b", "kind": "chat", "runnable": True, "bytes": 1_900_000_000},
    {"id": "llama3.1:8b", "kind": "chat", "runnable": True, "bytes": 4_920_000_000},
    {"id": "gemma2:9b", "kind": "chat", "runnable": True, "bytes": 5_400_000_000},
    {"id": "nomic-embed-text", "kind": "embed", "runnable": True, "bytes": 274_000_000},
]


class RamChoiceTests(unittest.TestCase):
    def setUp(self) -> None:
        # Pin BOTH host inputs. These tests pin the RAM rule; VRAM placement and the installed-RAM
        # floor have their own tests in VramAwareChoiceTests. Leaving either live would make the
        # expectations depend on the reviewer's machine (this box: 8 GB VRAM, 32 GB RAM).
        for name, value in (("vram_free_mib", 0), ("_ram_floor_bytes", 0)):
            p = patch.object(registry, name, lambda v=value: v)
            p.start()
            self.addCleanup(p.stop)

    def test_big_host_prefers_the_tool_capable_model_over_the_bigger_weak_one(self) -> None:
        """Policy: agentic tool-calling beats raw size. gemma2:9b is bigger, llama3.1:8b is the
        better agent, so the 8b wins a host that can hold either."""
        self.assertEqual(registry.ram_choice(MODELS, 64 * GiB), "llama3.1:8b")

    def test_24gb_also_gets_the_tool_capable_8b(self) -> None:
        self.assertEqual(registry.ram_choice(MODELS, 24 * GiB), "llama3.1:8b")

    def test_a_coder_brain_outranks_a_bigger_general_model(self) -> None:
        models = list(MODELS) + [
            {"id": "qwen2.5-coder:7b", "kind": "chat", "runnable": True, "bytes": 4_470_000_000}
        ]
        self.assertEqual(registry.ram_choice(models, 64 * GiB), "qwen2.5-coder:7b")
        self.assertEqual(registry.pick_default(models, prefer_ram=False), "qwen2.5-coder:7b")

    def test_9gb_free_picks_the_3b(self) -> None:
        self.assertEqual(registry.ram_choice(MODELS, 9 * GiB), "qwen2.5:3b")

    def test_4gb_free_picks_the_1b(self) -> None:
        self.assertEqual(registry.ram_choice(MODELS, int(4.5 * GiB)), "llama3.2:1b")

    def test_nothing_fits_still_returns_the_smallest_chat(self) -> None:
        self.assertEqual(registry.ram_choice(MODELS, 1 * GiB), "qwen2.5:1.5b")

    def test_unknown_ram_returns_none_so_normal_order_applies(self) -> None:
        self.assertIsNone(registry.ram_choice(MODELS, 0))

    def test_embed_models_are_never_chosen_as_brain(self) -> None:
        self.assertNotEqual(registry.ram_choice(MODELS, 64 * GiB), "nomic-embed-text")

    def test_pick_default_without_ram_keeps_preference_order(self) -> None:
        self.assertEqual(registry.pick_default(MODELS, prefer_ram=False), "qwen2.5:3b")

    def test_pick_default_falls_back_when_ram_unknown(self) -> None:
        self.assertEqual(
            registry.pick_default(MODELS, prefer_ram=True, avail_bytes=0), "qwen2.5:3b"
        )

    def test_pick_default_uses_ram_when_asked(self) -> None:
        self.assertEqual(
            registry.pick_default(MODELS, prefer_ram=True, avail_bytes=24 * GiB), "llama3.1:8b"
        )

    def test_plain_pick_default_stays_deterministic_even_with_the_flag_on(self) -> None:
        """console.json enabling RAM-auto must not silently change pick_default's contract."""
        original = registry.prefer_by_ram
        registry.prefer_by_ram = lambda: True
        try:
            self.assertEqual(registry.pick_default(MODELS), "qwen2.5:3b")
        finally:
            registry.prefer_by_ram = original


class UpsertPinTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = (
            registry.REGISTRY_PATH,
            registry.prefer_by_ram,
            registry.avail_ram_bytes,
            registry.vram_free_mib,
            registry._ram_floor_bytes,
        )
        registry.REGISTRY_PATH = Path(self._tmp.name) / "registry.json"
        registry.prefer_by_ram = lambda: True
        registry.avail_ram_bytes = lambda: 24 * GiB
        # Pin the two host inputs this class does not test: free VRAM and the installed-RAM floor
        # are covered by VramAwareChoiceTests. Left live they only make the expectation depend on
        # the reviewer's machine (8 GB VRAM / 32 GB RAM here).
        registry.vram_free_mib = lambda: 0
        registry._ram_floor_bytes = lambda: 0

    def tearDown(self) -> None:
        (
            registry.REGISTRY_PATH,
            registry.prefer_by_ram,
            registry.avail_ram_bytes,
            registry.vram_free_mib,
            registry._ram_floor_bytes,
        ) = self._orig
        self._tmp.cleanup()

    def test_first_scan_auto_picks_by_ram_and_records_the_source(self) -> None:
        data = registry.upsert([dict(m) for m in MODELS])
        self.assertEqual(data["selected"], "llama3.1:8b")
        self.assertEqual(data["selected_source"], "ram")

    def test_human_switch_wins_over_ram(self) -> None:
        registry.upsert([dict(m) for m in MODELS], selected="qwen2.5:3b")
        data = registry.upsert([dict(m) for m in MODELS])
        self.assertEqual(data["selected"], "qwen2.5:3b")
        self.assertEqual(data["selected_source"], "manual")

    def test_same_stick_retunes_when_the_human_pin_no_longer_fits(self) -> None:
        registry.upsert([dict(m) for m in MODELS], selected="llama3.1:8b")
        registry.avail_ram_bytes = lambda: int(4.5 * GiB)
        data = registry.upsert([dict(m) for m in MODELS])
        self.assertEqual(data["selected"], "llama3.2:1b")
        self.assertEqual(data["selected_source"], "ram")

    def test_auto_pins_follow_a_machine_change(self) -> None:
        registry.upsert([dict(m) for m in MODELS])
        registry.avail_ram_bytes = lambda: 9 * GiB
        data = registry.upsert([dict(m) for m in MODELS])
        self.assertEqual(data["selected"], "qwen2.5:3b")

    def test_without_the_flag_the_source_is_auto_not_ram(self) -> None:
        registry.prefer_by_ram = lambda: False
        data = registry.upsert([dict(m) for m in MODELS])
        self.assertEqual(data["selected"], "qwen2.5:3b")
        self.assertEqual(data["selected_source"], "auto")

    def test_registry_is_json_round_trippable(self) -> None:
        registry.upsert([dict(m) for m in MODELS])
        blob = json.loads(registry.REGISTRY_PATH.read_text(encoding="utf-8"))
        self.assertEqual(blob["selected"], "llama3.1:8b")
        self.assertEqual(registry.get("llama3.1:8b")["id"], "llama3.1:8b")


class VramAwareChoiceTests(unittest.TestCase):
    """A brain has to FIT THE GPU to be fast: placement is worth more than parameters."""

    MODELS = [
        {"id": "qwen2.5-coder:14b", "kind": "chat", "runnable": True, "bytes": 9 * GiB, "path": "a.gguf"},
        {"id": "qwen2.5-coder:7b", "kind": "chat", "runnable": True, "bytes": 5 * GiB, "path": "b.gguf"},
        {"id": "qwen2.5:1.5b", "kind": "chat", "runnable": True, "bytes": 986 * 1024 * 1024, "path": "c.gguf"},
    ]

    def test_a_coder_that_fits_vram_beats_a_bigger_coder_that_does_not(self):
        # 8 GB GPU, ~7 GiB free: the 14B cannot be offloaded (ngl would be 0 -> CPU) and loses to
        # the 7B that runs entirely on the GPU, even though both are rank-3 coders.
        self.assertEqual(registry.ram_choice(self.MODELS, 32 * GiB, vram_mib=7181), "qwen2.5-coder:7b")

    def test_without_a_gpu_the_biggest_capable_model_still_wins(self):
        # No GPU evidence -> the old, documented rule (biggest rank-3) must remain.
        self.assertEqual(registry.ram_choice(self.MODELS, 64 * GiB, vram_mib=0), "qwen2.5-coder:14b")

    def test_a_momentarily_busy_host_does_not_downgrade_the_brain(self):
        # The failure this guards: a 32 GB box reading 3 GiB "available" right after unloading a
        # 14B model picked the tiny brain. Half of installed RAM is the floor (16 GiB here), which
        # is enough to hold the 7B coder (5 GiB + factor/headroom = 10 GiB) that the 3 GiB reading
        # alone could not.
        with patch.object(registry, "_ram_floor_bytes", lambda: 16 * GiB):
            floored = registry.ram_choice(self.MODELS, 3 * GiB, vram_mib=0)
        with patch.object(registry, "_ram_floor_bytes", lambda: 0):
            unfloored = registry.ram_choice(self.MODELS, 3 * GiB, vram_mib=0)
        self.assertEqual(floored, "qwen2.5-coder:7b", "the floor must hold the brain size")
        self.assertEqual(unfloored, "qwen2.5:1.5b", "without the floor the momentary reading wins")

    def test_the_floor_never_lowers_a_healthy_reading(self):
        # max(available, floor): a small installed-RAM floor must not shrink a generous reading.
        with patch.object(registry, "_ram_floor_bytes", lambda: 1 * GiB):
            self.assertEqual(registry.ram_choice(self.MODELS, 12 * GiB, vram_mib=0), "qwen2.5-coder:7b")


if __name__ == "__main__":
    unittest.main()
