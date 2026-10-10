"""FreeLLMAPI streaming client for the interactive chat shell."""
import json
import os
import urllib.request
from pathlib import Path
from typing import Any, Generator

API_KEY_ENV = "FREELLMAPI_API_KEY"
DEFAULT_URL = "http://127.0.0.1:31415"
DEFAULT_MODEL = "auto"
TIMEOUT = 120


def state_snapshot(project_root: str | None = None) -> str:
    """Query the live Diamond store and return a compact, honest status summary.

    The chat AI receives this as ground truth. If a field is absent or empty,
    say so — never invent workflows, tasks, providers, or progress.
    """
    from .config import load_config
    from .diagnostics import doctor
    from .paths import resolve_paths
    from .store import EventStore
    from .workflow_store import WorkflowStore
    from .delivery_store import DeliveryStore

    root = project_root or str(Path(__file__).resolve().parents[2])
    paths = resolve_paths(root, None, None)
    config = load_config(paths.config)
    store = EventStore(paths.database)
    workflows = WorkflowStore(store).list()
    tasks = store.list()
    deliveries = DeliveryStore(store).list()

    lines = ["## Diamond Live State (ground truth — do not invent anything not listed here)", ""]
    lines.append(f"**Project:** {paths.project.name}")
    lines.append("")

    lines.append("### Providers")
    try:
        for p in doctor(config):
            lines.append(f"- `{p.provider.value}`: {p.status} version={p.version or 'UNKNOWN'} auth={p.auth_status}")
    except Exception as exc:
        lines.append(f"- probe failed: {exc}")
    lines.append("")

    lines.append("### Workflows")
    if not workflows:
        lines.append("- (none)")
    for w in workflows:
        lines.append(f"- `{w.spec.workflow_id[:12]}` [{w.status.value}] {w.spec.objective} "
                     f"(builder={w.spec.builder_provider.value}, debugger={w.spec.debugger_provider.value}, "
                     f"revision={w.revision})")
    lines.append("")

    lines.append("### Tasks")
    if not tasks:
        lines.append("- (none)")
    for t in tasks:
        lines.append(f"- `{t.spec.task_id[:12]}` [{t.status.value}] {t.spec.objective[:80]} "
                     f"(role={t.spec.role.value}, provider={t.spec.provider.value})")
    lines.append("")

    lines.append("### Deliveries")
    if not deliveries:
        lines.append("- (none — delivery pipeline has never been exercised)")
    for d in deliveries:
        lines.append(f"- `{d.spec.delivery_id[:12]}` [{d.status.value}] {d.spec.objective[:80]}")
    lines.append("")

    lines.append("### What Diamond does NOT track")
    lines.append("- No progress percentages, no time estimates, no bundle sizes, no memory/CPU metrics")
    lines.append("- No provider-to-provider communication — orchestrator is the hub, providers are spokes")
    lines.append("- No 'active' workflows — a workflow is either CREATED, BUILDING, DEBUGGING, BLOCKED, or FINISHED")
    lines.append("- If you are asked about something not in the lists above, say you don't know rather than guessing")
    return "\n".join(lines)


def _api_key() -> str:
    return os.environ.get(API_KEY_ENV, "")


def _fetch_sse(url: str, payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Stream SSE chunks and return them as parsed dicts."""
    data = json.dumps(payload, separators=(",", ":")).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={
            "x-api-key": key,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": "diamond-chat/1",
        },
    )
    resp = urllib.request.urlopen(req, timeout=TIMEOUT)
    chunks: list[dict[str, Any]] = []
    for line in resp:
        raw = line.decode("utf-8").strip()
        if not raw or not raw.startswith("data: "):
            continue
        try:
            obj = json.loads(raw[6:])
            if obj.get("object") == "chat.completion.chunk":
                chunks.append(obj)
            elif obj.get("object") == "chat.completion":
                chunks.append(obj)
        except (json.JSONDecodeError, KeyError):
            pass
    return chunks


def _extract_text(chunks: list[dict[str, Any]]) -> str:
    parts = []
    for c in chunks:
        choices = c.get("choices", [])
        if not choices:
            continue
        delta = choices[0].get("delta", {})
        msg = delta.get("content")
        if msg:
            parts.append(msg)
    return "".join(parts)


def _extract_usage(chunks: list[dict[str, Any]]) -> str:
    for c in reversed(chunks):
        u = c.get("usage")
        if u:
            return f"{u.get('prompt_tokens', 0)}in / {u.get('completion_tokens', 0)}out"
    return ""


def _extract_model(chunks: list[dict[str, Any]]) -> str:
    for c in chunks:
        m = c.get("model")
        if m:
            return m
    return DEFAULT_MODEL


def chat(
    user_msg: str,
    history: list[dict[str, Any]],
    system_prompt: str,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 2048,
) -> tuple[str, str, str]:
    """Send a chat message via FreeLLMAPI streaming.

    Returns (response_text, model_name, usage_string).
    """
    key = _api_key()
    if not key:
        raise RuntimeError(f"Set {API_KEY_ENV} environment variable")

    msgs = [{"role": "system", "content": system_prompt}] + history + [{"role": "user", "content": user_msg}]
    payload: dict[str, Any] = {
        "model": model,
        "messages": msgs,
        "stream": True,
        "max_tokens": max_tokens,
    }
    chunks = _fetch_sse(f"{DEFAULT_URL}/v1/chat/completions", payload, key)
    return _extract_text(chunks), _extract_model(chunks), _extract_usage(chunks)
