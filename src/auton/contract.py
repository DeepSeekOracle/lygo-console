"""Completion contracts and the Goal Manager (§5.1).

A contract is the only thing between "the model says it is finished" and the runtime believing it.
So it is a small, closed vocabulary of *observable* checks plus one escape hatch (a rubric) that is
never allowed to be the sole authority unless a human is told about it.

Two rules from §5.1 are enforced here rather than documented:

* the goal manager asks the model once, validates, retries once, then **falls back** to a single
  `llm_rubric` check - it never proceeds with an invalid contract;
* if the objective cannot be expressed with observable checks, the contract is forced to
  `requires_human_review: true` and the runtime will not auto-complete it.

`jsonschema` is used because it is already installed in the kit; nothing here adds a dependency.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Mapping

import jsonschema

log = logging.getLogger("auton.contract")

#: §5.1: "using only these check types".
CHECK_TYPES: tuple[str, ...] = (
    "file_exists",
    "file_min_bytes",
    "command_exit_zero",
    "json_schema",
    "http_status",
    "llm_rubric",
    "custom_verifier",
)

#: Checks a machine can verify without asking another model. If a contract has none of these, its
#: completion is a judgment call and a human has to see it (§5.1 rule).
OBSERVABLE_TYPES: frozenset[str] = frozenset(
    {"file_exists", "file_min_bytes", "command_exit_zero", "json_schema", "http_status", "custom_verifier"}
)

CHECK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["type"],
    "properties": {
        "type": {"enum": list(CHECK_TYPES)},
        "path": {"type": "string"},
        "min": {"type": "integer", "minimum": 0},
        "cmd": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
        "schema": {"type": "object"},
        "schema_path": {"type": "string"},
        "url": {"type": "string"},
        "expect": {"type": "integer"},
        "rubric": {"type": "string"},
        "max_score": {"type": "integer", "minimum": 1},
        "min_score": {"type": "integer", "minimum": 0},
        "name": {"type": "string"},
        "func": {"type": "string"},
        "args": {"type": "object"},
        "timeout_s": {"type": "number", "exclusiveMinimum": 0},
        "description": {"type": "string"},
    },
    "additionalProperties": True,
}

CONTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["objective", "checks"],
    "properties": {
        "objective": {"type": "string", "minLength": 1},
        "checks": {"type": "array", "minItems": 1, "items": CHECK_SCHEMA},
        "requires_human_review": {"type": "boolean"},
        "notes": {"type": "string"},
    },
    "additionalProperties": True,
}

#: Per-type required fields, as §5.5 defines each check's semantics.
_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "file_exists": ("path",),
    "file_min_bytes": ("path", "min"),
    "command_exit_zero": ("cmd",),
    "json_schema": ("path",),
    "http_status": ("url",),
    "llm_rubric": ("rubric", "min_score"),
    "custom_verifier": ("name",),
}

BUILD_PROMPT = (
    "Given this user request, emit a JSON completion contract using only these check types: "
    "file_exists, file_min_bytes, command_exit_zero, json_schema, http_status, llm_rubric, "
    "custom_verifier.\n\n"
    "The contract must have the shape "
    '{"objective": "<one sentence>", "checks": [{"type": "...", ...}], '
    '"requires_human_review": <bool>}.\n'
    "Every check must be verifiable from the filesystem, a command's exit code, a URL, or a JSON "
    "schema - never from the model's own narration. Reply with the JSON object only.\n\n"
    "User request:\n"
)


class ContractError(ValueError):
    """A contract that cannot be executed as written."""


def validate_contract(contract: Any) -> list[str]:
    """Return every problem with a contract. Empty list means it is runnable.

    Two layers on purpose: jsonschema catches shape errors, and `_semantic_errors` catches the
    per-type requirements (`file_min_bytes` without `min` is valid JSON and useless as a check).
    """
    errors: list[str] = []
    if isinstance(contract, str):
        try:
            contract = json.loads(contract)
        except ValueError as exc:
            return [f"contract is not JSON: {exc}"]
    if not isinstance(contract, Mapping):
        return [f"contract must be an object, got {type(contract).__name__}"]
    validator = jsonschema.Draft202012Validator(CONTRACT_SCHEMA)
    for err in sorted(validator.iter_errors(dict(contract)), key=lambda e: list(e.path)):
        where = "/".join(str(p) for p in err.path) or "<root>"
        errors.append(f"{where}: {err.message}")
    checks = contract.get("checks")
    if isinstance(checks, list):
        for i, check in enumerate(checks):
            if not isinstance(check, Mapping):
                errors.append(f"checks/{i}: must be an object")
                continue
            ctype = check.get("type")
            for fname in _REQUIRED_FIELDS.get(str(ctype), ()):
                if check.get(fname) in (None, ""):
                    errors.append(f"checks/{i}: {ctype} requires {fname!r}")
            if ctype == "json_schema" and "schema" not in check and "schema_path" not in check:
                errors.append(f"checks/{i}: json_schema requires 'schema' or 'schema_path'")
            if ctype == "llm_rubric":
                lo = check.get("min_score")
                hi = check.get("max_score", 5)
                if isinstance(lo, int) and isinstance(hi, int) and lo > hi:
                    errors.append(f"checks/{i}: min_score {lo} exceeds max_score {hi}")
    return errors


def is_observable(contract: Mapping[str, Any]) -> bool:
    """True when at least one check can be settled without an LLM's opinion."""
    checks = contract.get("checks") or []
    return any(str(c.get("type")) in OBSERVABLE_TYPES for c in checks if isinstance(c, Mapping))


def apply_review_rule(contract: dict[str, Any]) -> dict[str, Any]:
    """§5.1: if it cannot be expressed with observable checks, a human must review it.

    Returns the same dict, mutated - the contract travels straight into the goal row.
    """
    if not is_observable(contract):
        contract["requires_human_review"] = True
    contract.setdefault("requires_human_review", False)
    return contract


def validate_or_raise(contract: Any) -> dict[str, Any]:
    errors = validate_contract(contract)
    if errors:
        raise ContractError("; ".join(errors[:8]))
    out = dict(contract)
    apply_review_rule(out)
    return out


def fallback_contract(request: str, reason: str = "") -> dict[str, Any]:
    """§5.1's fallback: one llm_rubric check, flagged for human review."""
    note = f" Goal manager fell back to a rubric-only contract ({reason})." if reason else ""
    return {
        "objective": request.strip(),
        "checks": [
            {
                "type": "llm_rubric",
                "rubric": f"The work visibly satisfies the request: {request.strip()}",
                "max_score": 5,
                "min_score": 4,
            }
        ],
        "requires_human_review": True,
        "notes": ("Rubric-only contract: no observable check could be derived." + note).strip(),
    }


def extract_json(text: str) -> Any:
    """Pull the first JSON object out of a model reply (fenced or bare)."""
    if isinstance(text, (dict, list)):
        return text
    raw = str(text or "").strip()
    if not raw:
        raise ContractError("empty contract reply")
    for fence in ("```json", "```"):
        i = raw.find(fence)
        if i >= 0:
            body = raw[i + len(fence) :]
            j = body.find("```")
            if j >= 0:
                raw = body[:j].strip()
                break
    try:
        return json.loads(raw)
    except ValueError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        return json.loads(raw[start : end + 1])
    raise ContractError("no JSON object found in contract reply")


def _reply_text(reply: Any) -> str:
    """The prose out of whatever a turn adapter returned (dict, Turn, or plain string).

    Adapters are allowed to hand back `auton.model.Turn`; the contract builder only needs the text,
    and treating a Turn as an opaque string would parse its repr instead of its content.
    """
    if isinstance(reply, Mapping):
        return str(reply.get("text") or "")
    text = getattr(reply, "text", None)
    if isinstance(text, str):
        return text
    return str(reply or "")


def build_contract(
    request: str,
    turn_fn: Callable[[str], Any],
    max_retries: int = 1,
) -> tuple[dict[str, Any], list[str]]:
    """§5.1 construction protocol: one structured call, validate, retry once, then fall back.

    `turn_fn(prompt)` returns a model turn (`{"text": ...}` or a plain string). Returns
    `(contract, history)` where history is the list of problems met on the way, which the caller
    logs and stores next to the goal.
    """
    history: list[str] = []
    prompt = BUILD_PROMPT + f"{request}\n"
    attempts = 1 + max(1, int(max_retries))
    last_reason = ""
    for attempt in range(attempts):
        try:
            reply = turn_fn(prompt)
        except Exception as exc:  # noqa: BLE001 - a broken adapter must not lose the user's request
            last_reason = f"{type(exc).__name__}: {exc}"
            history.append(f"attempt {attempt + 1}: model call failed: {last_reason}")
            log.warning("contract model call failed: %s", last_reason)
            continue
        text = _reply_text(reply)
        try:
            candidate = extract_json(text)
        except ContractError as exc:
            last_reason = str(exc)
            history.append(f"attempt {attempt + 1}: {last_reason}")
            continue
        errors = validate_contract(candidate)
        if errors:
            last_reason = "; ".join(errors[:6])
            history.append(f"attempt {attempt + 1}: invalid contract: {last_reason}")
            continue
        out = dict(candidate)
        apply_review_rule(out)
        history.append(f"attempt {attempt + 1}: accepted")
        return out, history
    history.append(f"fallback: rubric-only contract ({last_reason})")
    return fallback_contract(request, last_reason), history


def create_goal(
    store,
    text: str,
    contract: Mapping[str, Any],
    budget: Mapping[str, Any] | None = None,
    payload: Mapping[str, Any] | None = None,
    defaults: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    """Persist a validated contract as a goal plus its first task, in one transaction (§5.1)."""
    contract = validate_or_raise(dict(contract))
    merged = dict(defaults or {})
    merged.update(dict(budget or {}))
    max_attempts = int(merged.pop("max_attempts", 3) or 3)
    goal_id, task_id = store.create_goal_and_task(
        text=str(text),
        contract=contract,
        budget=merged,
        payload=dict(payload or {}),
        max_attempts=max_attempts,
    )
    log.info("auton.contract goal=%s task=%s created checks=%s", goal_id, task_id, len(contract["checks"]))
    return goal_id, task_id


def summarize(contract: Mapping[str, Any]) -> str:
    """One-line human-readable contract summary used in the continuation prompt (§5.4)."""
    bits: list[str] = []
    for check in contract.get("checks") or []:
        ctype = str(check.get("type"))
        if ctype in {"file_exists", "file_min_bytes"}:
            extra = f" (>= {check.get('min')} bytes)" if ctype == "file_min_bytes" else ""
            bits.append(f"{check.get('path')} exists{extra}")
        elif ctype == "command_exit_zero":
            bits.append(f"`{check.get('cmd')}` exits 0")
        elif ctype == "json_schema":
            bits.append(f"{check.get('path')} matches its JSON schema")
        elif ctype == "http_status":
            bits.append(f"{check.get('url')} returns HTTP {check.get('expect', 200)}")
        elif ctype == "llm_rubric":
            bits.append(f"rubric scored >= {check.get('min_score')}")
        elif ctype == "custom_verifier":
            bits.append(f"verifier {check.get('name')} returns True")
        else:
            bits.append(ctype)
    return "; ".join(bits) if bits else "(no checks)"
