"""Tasking, cron and keeper: the console's ability to work past the turn that asked for it.

Every test runs against a temp directory, never the live kit's data files, and every supervision
probe is injected - the policy is what is under test, not the ports.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import crons  # noqa: E402
import keeper  # noqa: E402
import tasking  # noqa: E402


class TempKit(unittest.TestCase):
    """Point the three modules at a scratch directory for the duration of a test."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        d = Path(self._dir.name)
        self._saved = (tasking.LOG, tasking.DATA, crons.JOBS, crons.DATA,
                       keeper.STATE, keeper.LOCK, keeper.DATA, keeper.LOGDIR)
        tasking.DATA = d
        tasking.LOG = d / "tasks.jsonl"
        crons.DATA = d
        crons.JOBS = d / "crons.json"
        keeper.DATA = d
        keeper.STATE = d / "keeper.json"
        keeper.LOCK = d / "keeper.lock"
        keeper.LOGDIR = d / "logs"

    def tearDown(self):
        (tasking.LOG, tasking.DATA, crons.JOBS, crons.DATA,
         keeper.STATE, keeper.LOCK, keeper.DATA, keeper.LOGDIR) = self._saved
        self._dir.cleanup()

    def wait_for(self, tid: str, states=("done", "failed"), seconds: float = 20.0):
        deadline = time.time() + seconds
        while time.time() < deadline:
            for t in tasking.fold():
                if t.get("id") == tid and t.get("state") in states:
                    return t
            time.sleep(0.2)
        return None


class TaskQueue(TempKit):
    def test_add_returns_at_once_with_an_id(self):
        got = tasking.add("now", {})
        self.assertTrue(got.get("ok"), got)
        self.assertTrue(got["id"].startswith("t"))
        self.assertEqual(got["state"], "queued")

    def test_the_worker_actually_runs_it_and_records_the_result(self):
        got = tasking.add("now", {})
        done = self.wait_for(got["id"])
        self.assertIsNotNone(done, "the worker never finished a one-line limb call")
        self.assertEqual(done["state"], "done")
        self.assertIn("result", done)

    def test_a_failing_limb_is_recorded_as_failed_not_as_done(self):
        got = tasking.add("definitely_not_a_limb", {})
        done = self.wait_for(got["id"])
        self.assertIsNotNone(done)
        self.assertEqual(done["state"], "failed")

    def test_a_task_that_stopped_beating_is_reported_stalled(self):
        """A stalled job must be visible as stalled - not indistinguishable from a slow one."""
        tasking.LOG.write_text(json.dumps({
            "id": "tstalled", "event": "run", "state": "running", "t": time.time() - 600,
            "started": time.time() - 600, "beat": time.time() - 600, "target": "self_test",
            "created": time.time() - 600, "deadline": 300}) + "\n", encoding="utf-8")
        got = tasking.status()
        self.assertEqual(got["counts"].get("stalled"), 1)

    def test_cancel_before_it_runs_and_refusal_after(self):
        a = tasking.add("now", {})
        self.wait_for(a["id"])
        after = tasking.cancel(a["id"])
        self.assertFalse(after.get("ok"))
        self.assertEqual(after.get("error"), "already_finished")

    def test_status_reports_the_worker_and_the_counts(self):
        tasking.add("now", {})
        s = tasking.status()
        self.assertTrue(s["ok"])
        self.assertIn("counts", s)
        self.assertIn("worker", s)
        self.assertLessEqual(s["stall_after_s"], 300)


class CronSchedule(TempKit):
    def test_it_needs_a_name_a_limb_and_a_schedule(self):
        self.assertFalse(crons.add_job("", "self_test", every=60).get("ok"))
        self.assertFalse(crons.add_job("x", "", every=60).get("ok"))
        self.assertFalse(crons.add_job("x", "self_test").get("ok"))

    def test_a_bad_time_is_refused(self):
        got = crons.add_job("nightly", "self_test", at="25:99")
        self.assertFalse(got.get("ok"))
        self.assertEqual(got.get("error"), "bad_time")

    def test_every_job_becomes_due_then_waits_its_turn(self):
        crons.add_job("often", "now", every=60)
        self.assertEqual(len(crons.due()), 1, "a brand new job is due at once")
        started = crons.run_due(enqueue=lambda *a, **k: {"ok": True, "id": "tfake"})
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0]["task"], "tfake")
        self.assertEqual(crons.due(), [], "after running, it waits out its interval")
        self.assertEqual(crons.list_jobs()["jobs"][0]["runs"], 1)

    def test_a_disabled_job_is_never_due(self):
        crons.add_job("off", "now", every=1, enabled=False)
        self.assertEqual(crons.due(), [])

    def test_remove_and_enable(self):
        crons.add_job("temp", "now", every=30)
        self.assertTrue(crons.set_enabled("temp", False)["ok"])
        self.assertFalse(crons.due())
        self.assertTrue(crons.remove_job("temp")["ok"])
        self.assertFalse(crons.remove_job("temp")["ok"])


class KeeperSupervision(TempKit):
    def test_a_dead_console_is_rung_back_and_the_ring_is_recorded(self):
        rung = []
        got = keeper.sweep(console=lambda: {"up": False, "port": 9641},
                           doorbell=lambda: {"up": True, "port": 9640},
                           ring=lambda: (rung.append(1), {"ok": True})[1],
                           run_due=lambda now: [], now=time.time())
        self.assertEqual(len(rung), 1, "the keeper must ring the doorbell once")
        self.assertTrue(any(a.startswith("rung_doorbell") for a in got["actions"]), got["actions"])
        self.assertTrue(keeper.load_state().get("last_ring"), "the ring must be recorded for the cooldown")

    def test_it_does_not_ring_twice_inside_the_cooldown(self):
        rung = []
        probe = dict(console=lambda: {"up": False, "port": 9641},
                     doorbell=lambda: {"up": True, "port": 9640},
                     ring=lambda: (rung.append(1), {"ok": True})[1],
                     run_due=lambda now: [])
        keeper.sweep(now=time.time(), **probe)
        got = keeper.sweep(now=time.time() + 5, **probe)
        self.assertEqual(len(rung), 1, "a crash loop must not become a relaunch storm")
        self.assertTrue(any(a.startswith("restart_on_cooldown") for a in got["actions"]), got["actions"])

    def test_a_dead_doorbell_is_started(self):
        started = []
        got = keeper.sweep(console=lambda: {"up": True, "port": 9641},
                           doorbell=lambda: {"up": False, "port": 9640},
                           start_db=lambda: (started.append(1), {"ok": True})[1],
                           run_due=lambda now: [], now=time.time())
        self.assertEqual(len(started), 1)
        self.assertIn("doorbell_started", got["actions"])

    def test_due_crons_are_started_by_the_sweep(self):
        crons.add_job("nightly", "now", every=1)
        got = keeper.sweep(console=lambda: {"up": True}, doorbell=lambda: {"up": True},
                           run_due=lambda now: crons.run_due(now, enqueue=lambda *a, **k: {"ok": True, "id": "tk"}),
                           now=time.time() + 5)
        self.assertEqual([c["name"] for c in got["crons_run"]], ["nightly"])

    def test_every_sweep_is_recorded(self):
        keeper.sweep(console=lambda: {"up": True}, doorbell=lambda: {"up": True},
                     run_due=lambda now: [], now=time.time())
        state = keeper.load_state()
        self.assertEqual(state.get("sweeps"), 1)
        self.assertTrue(state.get("last_sweep_human"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
