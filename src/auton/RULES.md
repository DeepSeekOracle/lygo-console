# The Ten Non-Negotiable Rules of the Auton Runtime

Restated here, at the top of the codebase, because every module below is a consequence of one of
them. Source: Engineering Guide §13.

1. The model's prose is never evidence of completion. Only the Judge may return `done`.
2. Every state transition writes to the store before the next action.
3. The Dispatcher never calls the model. The Worker never polls other tasks. The Watcher never
   mutates tasks.
4. Every side-effecting tool call carries a deterministic idempotency key.
5. A turn with no tool call is a no-op; count it toward the budget; do not treat it as progress.
6. Budgets are checked before every turn; exhaustion yields `blocked`, never silent continuation.
7. The continuation prompt must name the failing checks and demand a concrete action. It must not
   restate the plan.
8. Illegal state transitions raise, never coerce.
9. Logs are structured and carry `goal_id`, `task_id`, `worker_id`, `seq`.
10. Terminal tasks are archived, not deleted, so failures remain debuggable.

## How the modules map onto the rules

| Rule | Enforced by |
|------|-------------|
| 1 | `judge.evaluate` is the only producer of `done`; `worker` exposes no `task.complete` tool |
| 2 | `store.transition` is compare-and-set and every worker step calls it before moving on |
| 3 | `dispatcher` has no turn adapter, `worker` runs one task, `watcher` only writes notifications |
| 4 | `tools_bridge.idempotency_key` + `task_steps.idempotency_key UNIQUE` |
| 5 | `worker` counts every turn and tracks a consecutive-no-op streak |
| 6 | `worker._budget_reason` runs before each turn |
| 7 | `judge.render_continuation` (fixed text, from §5.4) |
| 8 | `store.TASK_TRANSITIONS` + `IllegalTransition` |
| 9 | `log_ctx` structured log lines (`module`, `goal_id`, `task_id`, `worker_id`, `seq`) |
| 10 | `store.archive_terminal` copies to `tasks_archive`, deletes nothing else |
