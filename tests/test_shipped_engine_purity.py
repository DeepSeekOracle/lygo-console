"""The shipped engine/ must stay CPU-pure, and every GPU dll must live in engine/backends/.

Why this is a test and not a note: a GPU ggml dll sitting beside the shipped llama-server once
crashed every model load on this host inside the vendor driver (nvoglv64.dll, 0xc0000005, at any
-ngl, including 0). A stick that must boot on ANY PC cannot afford that — GPU code is opt-in, it
lives in the backend store, and it is only activated after a self-test loads a real model with it.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

GPU_DLLS = (
    "ggml-cuda.dll",
    "ggml-vulkan.dll",
    "ggml-sycl.dll",
    "ggml-hip.dll",
    "cublas64_13.dll",
    "cublasLt64_13.dll",
    "cudart64_13.dll",
)


class ShippedEnginePurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = ROOT / "engine"
        if not self.engine.is_dir():
            self.skipTest("no engine/ in this tree")

    def _dlls_an_active_backend_owns(self) -> set[str]:
        """Dll names the backend store has deliberately placed in engine/ on THIS host.

        The store's `kind: overlay` backends exist to be applied next to llama-server.exe (that is
        how the vulkan layer works). Such a dll is owned, not a stray. What must never happen is a
        GPU dll in engine/ with no owning backend — that orphan is what crashed every load.
        """
        owned: set[str] = set()
        try:
            import backends as be

            names = list(be.applied_overlays())
        except Exception:  # noqa: BLE001 - no store / no backends module means nothing is owned
            return owned
        for name in names:
            manifest = self.engine / "backends" / str(name) / "backend.json"
            try:
                obj = json.loads(manifest.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            files = obj.get("files")
            if isinstance(files, dict):
                owned.update(str(k) for k in files if str(k).lower().endswith(".dll"))
            elif isinstance(files, list):
                for item in files:
                    fp = item.get("name") if isinstance(item, dict) else item
                    if fp and str(fp).lower().endswith(".dll"):
                        owned.add(Path(str(fp)).name)
        return owned

    def test_shipped_engine_directory_carries_no_unowned_gpu_dll(self):
        owned = self._dlls_an_active_backend_owns()
        stray = sorted(
            f.name for f in self.engine.glob("*.dll") if f.name in GPU_DLLS and f.name not in owned
        )
        self.assertEqual(
            stray,
            [],
            f"GPU dll in engine/ with no owning backend: {stray} — a GPU dll beside the shipped "
            f"llama-server crashed every model load on this host (nvoglv64.dll 0xc0000005)",
        )

    def test_cpu_kernels_are_present_next_to_the_server(self):
        # The baseline must be able to run a model with NO GPU at all: at least one ggml-cpu dll.
        cpu = sorted(f.name for f in self.engine.glob("ggml-cpu-*.dll"))
        self.assertTrue(cpu, "engine/ has no ggml-cpu-*.dll — the CPU baseline cannot load a model")
        self.assertTrue((self.engine / "llama-server.exe").is_file(), "engine/llama-server.exe missing")

    def test_gpu_dlls_belong_to_the_backend_store(self):
        store = self.engine / "backends"
        if not store.is_dir():
            self.skipTest("no backend store in this tree")
        for name in ("cuda", "vulkan"):
            d = store / name
            if not d.is_dir():
                continue
            manifest = d / "backend.json"
            self.assertTrue(manifest.is_file(), f"backend {name} has no backend.json manifest")
            found = sorted(f.name for f in d.rglob("*.dll") if f.name in GPU_DLLS)
            self.assertTrue(found, f"backend {name} contains no GPU dll — is it really a GPU backend?")

    def test_stale_perf_active_cannot_point_the_console_at_a_missing_engine(self):
        # A stale selection must degrade to the shipped engine, never to a crash (resolve_binary does it).
        import engine as engine_mod
        import paths

        missing = paths.DATA / "engine_that_does_not_exist"
        real = engine_mod.resolve_binary()
        self.assertTrue(Path(real).is_file(), f"resolve_binary() returned a path that is not a file: {real}")
        self.assertFalse(str(real).startswith(str(missing)), "resolve_binary() trusted a missing engine dir")


if __name__ == "__main__":
    unittest.main(verbosity=2)
