"""CLI command implementations for the Diamond interactive shell."""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from .chat import chat as _chat
from .shell_ui import _format_status, _render_banner, _show_catalog
from .shell_history import show_history


CATALOG: dict[str, dict[str, str]] = {
    "help":      {"desc": "Show this help", "group": "General"},
    "status":    {"desc": "Show project, provider, workflow and delivery status", "group": "Project"},
    "doctor":    {"desc": "Check installed provider CLIs (safe version probes only)", "group": "Project"},
    "validate":  {"desc": "Validate diamond.toml configuration", "group": "Project"},
    "---":       {"group": "Workflow"},
    "workflow create": {"desc": "Create a new Builder/Debugger workflow", "group": "Workflow"},
    "workflow list":   {"desc": "List all workflows", "group": "Workflow"},
    "workflow show":   {"desc": "Show a workflow by ID", "group": "Workflow"},
    "workflow step":   {"desc": "Run one workflow step (builder or debugger)", "group": "Workflow"},
    "workflow events": {"desc": "Show immutable workflow event history", "group": "Workflow"},
    "---":       {"group": "Task"},
    "task create":    {"desc": "Create a new task record", "group": "Task"},
    "task list":      {"desc": "List all tasks", "group": "Task"},
    "task show":      {"desc": "Show a task by ID", "group": "Task"},
    "task events":    {"desc": "Show task event history", "group": "Task"},
    "---":       {"group": "Provider"},
    "provider configure": {"desc": "Configure a provider URL or key env var", "group": "Provider"},
    "provider models":    {"desc": "List available models for a provider", "group": "Provider"},
    "smoke":              {"desc": "Run live provider acceptance tests", "group": "Provider"},
    "---":       {"group": "Self-Improvement"},
    "self-improve create": {"desc": "Create a gated self-improvement workflow", "group": "Self-Improve"},
    "self-improve run":    {"desc": "Run one self-improvement step", "group": "Self-Improve"},
    "self-improve status": {"desc": "Check self-improvement status", "group": "Self-Improve"},
    "---":       {"group": "System"},
    "dashboard": {"desc": "Open the web dashboard in your browser", "group": "System"},
    "quit":     {"desc": "Exit the shell", "group": "System"},
    "exit":     {"desc": "Exit the shell", "group": "System"},
    "clear":    {"desc": "Clear the screen", "group": "System"},
    "history":  {"desc": "Show command history", "group": "System"},
}


def cmd_help(shell: Any, args: list[str]) -> tuple[dict, bool]:
    if args:
        pattern = " ".join(args).lower()
        matches = {k: v for k, v in CATALOG.items() if k != "---" and pattern in k.lower()}
        if matches:
            for cmd, info in sorted(matches.items()):
                from .shell_ui import cyan
                print(f"  {cyan(cmd):28s} {info['desc']}")
            return {}, True
    _show_catalog(CATALOG)
    return {}, True


def cmd_status(shell: Any, args: list[str]) -> tuple[dict, bool]:
    return shell._run_cmd(["--json", "project", "status"])


def cmd_doctor(shell: Any, args: list[str]) -> tuple[dict, bool]:
    return shell._run_cmd(["--json", "doctor"])


def cmd_validate(shell: Any, args: list[str]) -> tuple[dict, bool]:
    return shell._run_cmd(["--json", "validate-config"])


def cmd_workflow_list(shell: Any, args: list[str]) -> tuple[dict, bool]:
    return shell._run_cmd(["--json", "workflow", "list"])


def cmd_workflow_show(shell: Any, args: list[str]) -> tuple[dict, bool]:
    if not args:
        from .shell_ui import red
        print(red("usage: workflow show <workflow-id>"))
        return {}, False
    return shell._run_cmd(["--json", "workflow", "show"] + args)


def cmd_workflow_step(shell: Any, args: list[str]) -> tuple[dict, bool]:
    if len(args) < 2:
        from .shell_ui import red
        print(red("usage: workflow step <workflow-id> --expected-revision <N>"))
        return {}, False
    rev = 1
    for i, a in enumerate(args):
        if a == "--expected-revision" and i + 1 < len(args):
            try:
                rev = int(args[i + 1])
            except ValueError:
                from .shell_ui import red
                print(red("expected-revision must be an integer"))
                return {}, False
    return shell._run_cmd(["--json", "workflow", "step"] + [args[0],
            "--expected-revision", str(rev)])


def cmd_workflow_events(shell: Any, args: list[str]) -> tuple[dict, bool]:
    if not args:
        from .shell_ui import red
        print(red("usage: workflow events <workflow-id>"))
        return {}, False
    return shell._run_cmd(["--json", "workflow", "events"] + args)


def cmd_workflow_create(shell: Any, args: list[str]) -> tuple[dict, bool]:
    from .shell_ui import yellow
    print(yellow("Use: ./scripts/diamond.sh --json workflow create --help for full syntax."))
    print("Example:")
    print("  ./scripts/diamond.sh --json workflow create \\\n"
          "    --objective \"Build a counter app\" \\\n"
          "    --workspace /tmp/my-app \\\n"
          "    --owned-path main.py \\\n"
          "    --test '{\"name\":\"test_main\",\"argv\":[\"/usr/bin/python3\",\"test_main.py\"],"
          "\"timeout_seconds\":30,\"output_limit\":65536}' \\\n"
          "    --command-id my-workflow-1")
    return {}, True


def cmd_task_list(shell: Any, args: list[str]) -> tuple[dict, bool]:
    return shell._run_cmd(["--json", "task", "list"])


def cmd_task_show(shell: Any, args: list[str]) -> tuple[dict, bool]:
    if not args:
        from .shell_ui import red
        print(red("usage: task show <task-id>"))
        return {}, False
    return shell._run_cmd(["--json", "task", "show"] + args)


def cmd_task_events(shell: Any, args: list[str]) -> tuple[dict, bool]:
    if not args:
        from .shell_ui import red
        print(red("usage: task events <task-id>"))
        return {}, False
    return shell._run_cmd(["--json", "task", "events"] + args)


def cmd_task_create(shell: Any, args: list[str]) -> tuple[dict, bool]:
    if not args:
        from .shell_ui import yellow
        print(yellow("usage: task create --objective \"...\" [--role builder] [--provider claude]"))
        print("Example:")
        print('  task create --objective "Build a greeting utility" --role builder')
        return {}, True
    return shell._run_cmd(["--json", "task", "create"] + args)


def cmd_provider_configure(shell: Any, args: list[str]) -> tuple[dict, bool]:
    if not args or args[0] != "configure":
        from .shell_ui import red
        print(red("usage: provider configure <name> --url <url>"))
        return {}, False
    return shell._run_cmd(["--json"] + args)


def cmd_provider_models(shell: Any, args: list[str]) -> tuple[dict, bool]:
    if not args:
        from .shell_ui import red
        print(red("usage: provider models <name>"))
        return {}, False
    return shell._run_cmd(["--json", "provider", "models"] + args)


def cmd_smoke(shell: Any, args: list[str]) -> tuple[dict, bool]:
    if not args:
        from .shell_ui import yellow
        print(yellow("usage: smoke --provider <name> [--provider <name2>] [--code-test]"))
        print("Available: gemini antigravity codex omnirush copilot claude")
        return {}, True
    return shell._run_cmd(["--json", "smoke"] + args)


def cmd_self_improve_create(shell: Any, args: list[str]) -> tuple[dict, bool]:
    from .shell_ui import yellow
    print(yellow("Use: ./scripts/diamond.sh --json self-improve create --help"))
    return {}, True


def cmd_self_improve_run(shell: Any, args: list[str]) -> tuple[dict, bool]:
    if len(args) < 2:
        from .shell_ui import red
        print(red("usage: self-improve run <workflow-id> --expected-revision <N>"))
        return {}, False
    return shell._run_cmd(["--json", "self-improve", "run"] + args)


def cmd_self_improve_status(shell: Any, args: list[str]) -> tuple[dict, bool]:
    if not args:
        from .shell_ui import red
        print(red("usage: self-improve status <workflow-id>"))
        return {}, False
    return shell._run_cmd(["--json", "self-improve", "status"] + args)


def cmd_dashboard(shell: Any, args: list[str]) -> tuple[dict, bool]:
    """Open the web dashboard in the user's default browser."""
    import subprocess
    import sys
    from .shell_ui import cyan, dim
    port = "8765"
    if args:
        port = args[0]
    url = f"http://127.0.0.1:{port}"
    print(f"\n{cyan('◆ Dashboard')}")
    print(f"{dim(url)}")
    print(f"{dim('Starting server...')}")
    try:
        from .dashboard import main as dashboard_main
        # Launch in background so the shell stays responsive
        proc = subprocess.Popen(
            [sys.executable, "-B", "-m", "diamond", "dashboard", "--port", port],
            env={"PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
                 **__import__('os').environ},
        )
        print(f"{dim('Dashboard PID: ' + str(proc.pid))}")
        print(f"{dim('Press Ctrl+C to stop.')}")
        proc.wait()
    except Exception as exc:
        from .shell_ui import red
        print(f"{red('ERROR')}: {exc}")
    return {}, True


def cmd_quit(shell: Any, args: list[str]) -> tuple[dict, bool]:
    raise SystemExit(0)


def cmd_exit(shell: Any, args: list[str]) -> tuple[dict, bool]:
    raise SystemExit(0)


def cmd_clear(shell: Any, args: list[str]) -> None:
    os.system("clear")


def cmd_history(shell: Any, args: list[str]) -> tuple[dict, bool]:
    show_history(args)
    return {}, True


def cmd_chat(shell: Any, args: list[str]) -> tuple[dict, bool]:
    """Interactive AI chat via FreeLLMAPI."""
    from .shell_ui import cyan, dim, magenta
    from .shell_history import save_history

    print(f"\n{cyan('◆ AI Chat')}")
    print(f"{dim('Type your message or /quit to leave, /help for options')}\n")

    history: list[dict[str, Any]] = []
    model = "auto"
    from .chat import state_snapshot
    live_state = state_snapshot(shell.project_root)
    system_prompt = (
        "You are a Diamond Orchestrator AI assistant for Flawless Diamond Piece. "
        "You help users build, manage, and verify AI coding workflows. "
        "Be concise. Use project facts. Don't fabricate.\n\n"
        "## Ground truth — read this before answering any question about project state\n"
        f"{live_state}\n\n"
        "## Hard rules\n"
        "- Only state facts that appear in the ground truth above. If something is not listed "
        "(no workflows, no tasks, no deliveries), say so plainly.\n"
        "- NEVER invent workflow names, task IDs, provider assignments, progress percentages, "
        "time estimates, or system metrics. Diamond does not track those things.\n"
        "- If the user asks about a workflow or task that does not exist in the ground truth, "
        "tell them it doesn't exist and offer to create one.\n"
        "- If asked to build something, you can describe what to do — but the actual execution "
        "happens through the Diamond CLI, not inside this chat."
    )

    while True:
        try:
            user_input = input(f"\n{dim('You')}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{dim('(Chat ended)')}")
            break
        if not user_input:
            continue
        if user_input.lower() == "/quit" or user_input.lower() == "/exit":
            print(f"\n{dim('(Chat ended)')}")
            break
        if user_input.lower() == "/help":
            print(f"\n  {cyan('/model <name>')}:  Switch AI model")
            print(f"  {cyan('/clear')}:     Clear chat history")
            print(f"  {cyan('/status')}:   Show project status")
            print(f"  {cyan('/quit')}:    Exit chat")
            continue
        if user_input.lower() == "/clear":
            history.clear()
            print(f"\n{dim('(Chat history cleared)')}")
            continue
        if user_input.lower().startswith("/model"):
            parts = user_input.split()
            if len(parts) > 1:
                model = " ".join(parts[1:])
                print(f"\n{dim(f'Model set to: {model}')}")
            else:
                print(f"\n{dim(f'Current model: {model}')}")
            continue
        if user_input.lower() == "/status":
            result, ok = shell._run_cmd(["--json", "status"])
            shell._display(result, ok, "status")
            continue

        try:
            reply, used_model, usage = _chat(user_input, history, system_prompt, model)
        except RuntimeError as exc:
            from .shell_ui import red, dim
            print(f"\n{red('ERROR')}: {exc}")
            print(f"{dim('Set FREELLMAPI_API_KEY environment variable')}")
            break

        print(f"\n{magenta('AI')}{dim(f' ({used_model})')}")
        print(reply)
        if usage:
            print(f"\n{dim(f'Tokens: {usage}')}")

        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": reply})

        # Keep history bounded (last 20 messages)
        if len(history) > 40:
            history = history[-40:]

        save_history()

    return {}, True


_COMMAND_MAP: dict[str, Any] = {
    "dashboard":   cmd_dashboard,
    "chat":        cmd_chat,
    "help":        cmd_help,
    "status":      cmd_status,
    "doctor":      cmd_doctor,
    "validate":    cmd_validate,
    "workflow create":  cmd_workflow_create,
    "workflow list":    cmd_workflow_list,
    "workflow show":    cmd_workflow_show,
    "workflow step":    cmd_workflow_step,
    "workflow events":  cmd_workflow_events,
    "task create":    cmd_task_create,
    "task list":      cmd_task_list,
    "task show":      cmd_task_show,
    "task events":    cmd_task_events,
    "provider configure": cmd_provider_configure,
    "provider models":  cmd_provider_models,
    "smoke":      cmd_smoke,
    "self-improve create": cmd_self_improve_create,
    "self-improve run":    cmd_self_improve_run,
    "self-improve status": cmd_self_improve_status,
    "quit":       cmd_quit,
    "exit":       cmd_exit,
    "clear":      cmd_clear,
    "history":    cmd_history,
}


def _dispatch(shell: Any, raw: str) -> None:
    """Parse raw input and dispatch to the right handler."""
    stripped = raw.strip()
    if not stripped or stripped.startswith("#"):
        return

    parts = shlex.split(stripped)
    if not parts:
        return

    cmd = parts[0]
    args = parts[1:]

    handler = _COMMAND_MAP.get(cmd)
    if handler is None:
        found = False
        for key in sorted(_COMMAND_MAP, key=len, reverse=True):
            if stripped.startswith(key + " ") or stripped == key:
                handler = _COMMAND_MAP[key]
                remaining = stripped[len(key):].strip()
                args = shlex.split(remaining) if remaining else []
                found = True
                break
        if not found:
            from .shell_ui import red, dim
            print(f"{red('? Unknown command:')} {dim(cmd)}. Type 'help' for available commands.")
            return

    try:
        result, ok = handler(shell, args)
        if ok or result:
            shell._display(result, ok, stripped)
    except SystemExit:
        raise
    except Exception as exc:
        from .shell_ui import red
        print(f"{red('ERROR')}: {exc}")