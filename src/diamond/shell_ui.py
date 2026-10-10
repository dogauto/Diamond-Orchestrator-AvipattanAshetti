"""UI utilities for the Diamond interactive shell."""
import os
from pathlib import Path


def _c(codes: tuple[int, ...]) -> str:
    return "\033[" + ";".join(str(c) for c in codes) + "m"


def bold(t: str) -> str: return _c((1,)) + t + _c((0,))
def dim(t: str) -> str: return _c((2,)) + t + _c((0,))
def green(t: str) -> str: return _c((32,)) + t + _c((0,))
def red(t: str) -> str: return _c((31,)) + t + _c((0,))
def yellow(t: str) -> str: return _c((33,)) + t + _c((0,))
def cyan(t: str) -> str: return _c((36,)) + t + _c((0,))
def magenta(t: str) -> str: return _c((35,)) + t + _c((0,))
def fg_white(t: str) -> str: return _c((37,)) + t + _c((0,))

_OK = green("✓")
_FAIL = red("✗")
_INFO = cyan("ℹ")
_WARN = yellow("⚠")


def _format_status(result: dict[str, Any], ok: bool) -> str:
    """Render a compact human-readable status from the CLI result dict."""
    if not ok:
        err = result.get("error", {})
        if err:
            return red(f"{err.get('code', 'ERROR')}: {err.get('message', '')}")
        r = result
    else:
        if "result" in result:
            r = result["result"]
        else:
            r = result
    lines: list[str] = []

    if "providers" in r:
        lines.append(bold("Providers"))
        for p in r["providers"]:
            status = p.get("status", "?")
            ver = p.get("version", "") or "UNKNOWN"
            auth = p.get("auth_status", "")
            icon = _OK if status == "available" else (_FAIL if status == "unavailable" else _INFO)
            lines.append(f"  {icon} {p['provider']:14s}  {ver:20s}  auth={auth}")
        lines.append("")

    if "workflows" in r:
        lines.append(bold("Workflows"))
        for w in r["workflows"]:
            st = w.get("status", "?")
            oid = w.get("spec", {}).get("objective", "")[:50]
            wid = w.get("workflow_id", "?")[:12]
            icon = _OK if st == "VERIFIED" else (_FAIL if st in ("EXHAUSTED", "BLOCKED") else _INFO)
            lines.append(f"  {icon} [{wid}] {st:12s}  {oid}")
        lines.append("")

    if "deliveries" in r and r["deliveries"]:
        lines.append(bold("Deliveries"))
        for d in r["deliveries"]:
            st = d.get("status", "?")
            lines.append(f"  {_INFO} [{d.get('delivery_id', '?')[:12]}] {st}")
        lines.append("")

    if "tasks" in r:
        lines.append(bold("Tasks"))
        for t in r["tasks"]:
            st = t.get("status", "?")
            obj = t.get("spec", {}).get("objective", "")[:50]
            tid = t.get("task_id", t.get("spec", {}).get("task_id", "?"))[:12]
            icon = _OK if st == "SUCCEEDED" else (_FAIL if st == "FAILED" else _INFO)
            lines.append(f"  {icon} [{tid}] {st:12s}  {obj}")
        lines.append("")

    if r.get("ok", False):
        lines.append(green("Command succeeded."))
    return "\n".join(lines)


def _render_banner(proj: str) -> str:
    from .shell_ui import bold, cyan, dim
    return f"""
{bold(cyan("◆ Diamond Shell — Flawless Diamond Piece"))}
{dim("A local orchestrator for Gemini, Antigravity, Codex, Omnirush, Copilot, and Claude Code.")}
{dim("Project: ")}{bold(proj)}
{dim("Type ")}{cyan("help")}{dim(" for commands, ")}{cyan("chat")}{dim(" for AI, ")}{cyan("clear")}{dim(" to reset, ")}{cyan("quit")}{dim(" to exit.")}
"""


def _show_catalog(catalog: dict[str, dict[str, str]]) -> None:
    from .shell_ui import bold, cyan
    group: str | None = None
    for cmd, info in catalog.items():
        if cmd == "---":
            group = info["group"]
            print(f"\n  {bold(group)}")
            continue
        lines = [f"    {cyan(cmd):28s} {info['desc']}"]
        print("\n".join(lines))