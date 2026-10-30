"""The tasking watchdog: the queue must be able to say a job was ABANDONED, and the watchdog must return.

Written after a MEASURED failure on 2026-10-16: a cron's job was queued inside a sweep process, started,
and the process exited mid-job. `task_list` reported it `stalled` for ever (state was only ever DERIVED
on read), nothing retried the scheduled run, and the watchdog written to fix that then deadlocked -
reconcile() held tasking._lock and appended, and _append() takes the same non-reentrant lock. No unit
test caught it because no test ever called the watchdog with work to do; `task_recover` simply hung.
"""
import pathlib
import sys
import tempfile
import threading
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        import tasking

        self.t = tasking
        self.tmp = tempfile.TemporaryDirectory()
        self.real_log = tasking.LOG
        tasking.LOG = pathlib.Path(self.tmp.name) / "tasks.jsonl"
        tasking._worker = None

    def tearDown(self):
        self.t.LOG = self.real_log
        self.t._worker = None
        self.tmp.cleanup()

    def _abandoned_cron_job(self, tid: str = "tabandoned") -> str:
        """The measured shape: add -> run, then the runner is gone and the heartbeat stopped."""
        t = self.t
        with t._lock:
            t._append({"id": tid, "event": "add", "t": t._now(), "when": t._stamp(), "state": "queued",
                       "target": "self_check", "args": {}, "deadline": 900.0, "note": "",
                       "origin": "cron:healthcheck", "created": t._now(), "beats": 0})
            t._append({"id": tid, "event": "run", "t": t._now(), "when": t._stamp(), "state": "running",
                       "started": t._now() - (t.STALL_AFTER + 30), "beat": t._now() - (t.STALL_AFTER + 30)})
        return tid

    def test_the_watchdog_returns_even_with_work_to_do(self):
        """The regression: this call deadlocked for over 240 s on the first build."""
        self._abandoned_cron_job()
        out: dict = {}
        th = threading.Thread(target=lambda: out.update(self.t.watch()), daemon=True)
        th.start()
        th.join(timeout=20)
        self.assertFalse(th.is_alive(),
                         "the watchdog must return - a deadlock froze the queue's own health check")
        self.assertGreaterEqual(out.get("abandoned", 0), 1)

    def test_an_abandoned_cron_job_goes_back_on_the_queue_exactly_once(self):
        tid = self._abandoned_cron_job()
        first = self.t.watch()
        self.assertGreaterEqual(first.get("abandoned", 0), 1)
        state = {x["id"]: x for x in self.t.fold()}[tid]
        self.assertEqual(state.get("state"), "queued", "a missed scheduled run must be a LATE run")
        self.assertEqual(int(state.get("recovered") or 0), 1)
        second = self.t.watch()
        self.assertEqual(second.get("abandoned"), 0, "the retry must not become a loop")
        self.assertEqual(int({x["id"]: x for x in self.t.fold()}[tid].get("recovered") or 0), 1)

    def test_an_abandoned_agent_job_is_recorded_not_retried(self):
        t = self.t
        with t._lock:
            t._append({"id": "tplain", "event": "add", "t": t._now(), "when": t._stamp(), "state": "running",
                       "target": "self_check", "args": {}, "deadline": 900.0, "note": "", "origin": "agent",
                       "created": t._now(), "started": t._now() - (t.STALL_AFTER + 30),
                       "beat": t._now() - (t.STALL_AFTER + 30), "beats": 0})
        self.t.watch()
        state = {x["id"]: x for x in self.t.fold()}["tplain"]
        self.assertEqual(state.get("state"), "interrupted",
                         "work the operator asked for once is recorded, not silently repeated")

    def test_health_names_the_stuck_job(self):
        tid = self._abandoned_cron_job()
        h = self.t.health()
        self.assertIn(tid, h.get("stalled_ids") or [], "a stuck job must be nameable, not just countable")

    def test_the_cores_no_longer_withhold_capabilities(self):
        """The steward removed the caps on 2026-10-16 - what the brain can call is the point."""
        import tools

        for name in ("task_health", "task_recover", "subagent_run", "subagent_pool", "keeper_status"):
            self.assertIn(name, tuple(tools.CORE_NAMES), f"{name} must reach the on-box brain")

    def test_the_console_seeds_its_own_tasking_watch_and_keeps_the_operators_jobs(self):
        import crons

        store: list = [{"name": "healthcheck", "limb": "self_check", "every": 1800, "at": "", "enabled": True}]
        real_load, real_save = crons._load, crons._save
        crons._load = lambda: store
        crons._save = lambda jobs: store.__setitem__(slice(None), list(jobs))
        try:
            first = crons.ensure_defaults()
            names = [j.get("name") for j in store]
            self.assertIn("tasking-watch", names)
            self.assertIn("healthcheck", names, "seeding must never drop the operator's own jobs")
            self.assertTrue(any(s["name"] == "tasking-watch" for s in first.get("seeded") or []))
            again = crons.ensure_defaults()
            self.assertEqual(again.get("seeded"), [], "seeding twice must add nothing")
            self.assertIn("tasking-watch", again.get("already_there") or [])
        finally:
            crons._load, crons._save = real_load, real_save


if __name__ == "__main__":
    unittest.main()
