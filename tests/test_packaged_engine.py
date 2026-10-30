"""The packaged picture engine: an all-in-one install must find its OWN engine with no config.

The console now ships stable-diffusion.cpp inside the package (tools/sd-cpu), which is only true of a
build that carries it — so these tests build a fake kit on disk and prove the resolution rules rather
than trusting the machine they run on:

- a machine with no media root at all still gets a working engine (the kit IS the media root);
- a machine that declares a real media root keeps it, packaged engine or not (never an override);
- the fallback names the engine it found, so media_status can say which route ran.

Hermetic by construction: nothing here reads the real C:/D:/I: layout or a real config file.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import media_tools as mt  # noqa: E402


def _fake_kit(base: Path, *, with_engine: bool = True) -> Path:
    kit = base / "kit"
    if with_engine:
        engine = kit / "tools" / "sd-cpu"
        engine.mkdir(parents=True)
        (engine / "sd-cli.exe").write_bytes(b"MZ")
    else:
        kit.mkdir(parents=True)
    return kit


class PackagedEngineTests(unittest.TestCase):
    def test_the_kits_own_engine_is_found_when_the_machine_has_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            kit = _fake_kit(base)
            nowhere = base / "no-such-media-root"
            with mock.patch.object(mt, "KIT_ROOT", kit), mock.patch.object(
                mt, "_cfg", lambda: {"media_root": str(nowhere)}
            ):
                self.assertEqual(mt.media_root(), kit)
                self.assertEqual(mt.sd_cpu_exe(), kit / "tools" / "sd-cpu" / "sd-cli.exe")

    def test_a_machine_that_declares_its_own_media_root_keeps_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            media = base / "LYGO_MEDIA"
            (media / "tools" / "sd").mkdir(parents=True)
            kit = _fake_kit(base)
            with mock.patch.object(mt, "KIT_ROOT", kit), mock.patch.object(
                mt, "_cfg", lambda: {"media_root": str(media)}
            ):
                self.assertEqual(mt.media_root(), media)

    def test_a_kit_without_the_engine_falls_back_to_nothing_not_to_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            kit = _fake_kit(base, with_engine=False)
            nowhere = base / "no-such-media-root"
            with mock.patch.object(mt, "KIT_ROOT", kit), mock.patch.object(
                mt, "_cfg", lambda: {"media_root": str(nowhere)}
            ):
                self.assertEqual(mt.media_root(), nowhere)
                self.assertIsNone(mt.sd_cpu_exe())

    def test_the_checkpoint_of_a_packaged_engine_is_its_own_models_sd_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            kit = _fake_kit(base)
            (kit / "models" / "sd").mkdir(parents=True)
            checkpoint = kit / "models" / "sd" / "sd_xl_turbo_1.0_fp16.safetensors"
            checkpoint.write_bytes(b"\x00" * 16)
            nowhere = base / "no-such-media-root"
            with mock.patch.object(mt, "KIT_ROOT", kit), mock.patch.object(
                mt, "_cfg", lambda: {"media_root": str(nowhere)}
            ):
                self.assertEqual(mt.sd_model(), checkpoint)


if __name__ == "__main__":
    unittest.main()
