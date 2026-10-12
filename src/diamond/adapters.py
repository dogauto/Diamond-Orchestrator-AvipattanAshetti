import hashlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .domain import Provider
from .errors import ValidationError
from .sandbox import executable as sandbox_executable
from .validation import text


class OutputMode(str, Enum):
    JSON = "json"
    STREAM_JSON = "stream-json"
    TEXT = "text"


class PermissionMode(str, Enum):
    PLAN = "plan"
    DEFAULT = "default"


@dataclass(frozen=True)
class Invocation:
    provider: Provider
    argv: tuple[str, ...]
    output_mode: OutputMode
    requested_model: str | None
    permission_enforced: bool = False
    permission_mode: PermissionMode = PermissionMode.DEFAULT
    json_schema: str | None = None
    json_schema_path: str | None = None
    prompt_digest: str | None = None
    prompt_text: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.provider, Provider) or type(self.argv) is not tuple or not self.argv:
            raise ValidationError("invocation provider and argv are invalid")
        text(self.argv[0], "invocation executable", 4096)
        if len(self.argv) > 256:
            raise ValidationError("invocation has too many arguments")
        for argument in self.argv[1:]:
            if not isinstance(argument, str) or len(argument) > 65_536 or "\x00" in argument:
                raise ValidationError("invocation argument must be bounded text without NUL")
        if not isinstance(self.output_mode, OutputMode):
            raise ValidationError("invocation output mode is invalid")
        if self.requested_model is not None:
            text(self.requested_model, "requested model", 256)
        if type(self.permission_enforced) is not bool:
            raise ValidationError("invocation permission_enforced must be boolean")
        if not isinstance(self.permission_mode, PermissionMode):
            raise ValidationError("invocation permission_mode must be a PermissionMode value")
        if self.json_schema is not None:
            text(self.json_schema, "invocation JSON schema", 1_000_000)
        if self.json_schema_path is not None:
            text(self.json_schema_path, "invocation JSON schema path", 4096)
            if not Path(self.json_schema_path).is_absolute():
                raise ValidationError("invocation JSON schema path must be absolute")
        if self.prompt_digest is not None and (type(self.prompt_digest) is not str or len(self.prompt_digest) != 64
                or any(char not in "0123456789abcdef" for char in self.prompt_digest)):
            raise ValidationError("invocation prompt digest must be a lowercase SHA-256 digest")
        if self.prompt_text is not None:
            text(self.prompt_text, "invocation prompt", 1_000_000)
            if self.prompt_digest != hashlib.sha256(self.prompt_text.encode("utf-8")).hexdigest():
                raise ValidationError("invocation prompt digest does not match prompt text")
        if self.json_schema is not None and self.json_schema_path is not None:
            raise ValidationError("invocation JSON schema must use either inline or file-backed form")
        if self.provider is Provider.CODEX and self.json_schema is not None:
            raise ValidationError("Codex invocations require a file-backed JSON schema")
        if self.provider is not Provider.CODEX and self.json_schema_path is not None:
            raise ValidationError("only Codex invocations may use a file-backed JSON schema")


def build_invocation(provider: Provider, executable: str, prompt: str, *,
                     requested_model: str | None = None,
                     permission_mode: PermissionMode = PermissionMode.PLAN,
                      stream: bool = False,
                      json_schema: str | None = None,
                      json_schema_path: str | None = None,
                      provider_tools: tuple[str, ...] | None = None) -> Invocation:
    """Build provider-specific argv without invoking a provider."""
    if not isinstance(provider, Provider):
        raise ValidationError("provider must be a Provider enum")
    text(executable, "provider executable", 4096)
    text(prompt, "provider prompt", 1_000_000)
    if not isinstance(permission_mode, PermissionMode):
        raise ValidationError("permission mode is invalid")
    if type(stream) is not bool:
        raise ValidationError("stream must be boolean")
    if json_schema is not None:
        text(json_schema, "provider JSON schema", 1_000_000)
    if json_schema_path is not None:
        text(json_schema_path, "provider JSON schema path", 4096)
        if not Path(json_schema_path).is_absolute():
            raise ValidationError("provider JSON schema path must be absolute")
    if stream and provider is not Provider.ANTIGRAVITY:
        raise ValidationError("stream-json mode is currently supported only by Antigravity CLI")
    if json_schema is not None and provider not in {Provider.ANTIGRAVITY, Provider.CLAUDE}:
        raise ValidationError("structured JSON reports are supported only by Antigravity and Claude")
    if json_schema_path is not None and provider is not Provider.CODEX:
        raise ValidationError("file-backed JSON reports are supported only by Codex")
    if json_schema is not None and json_schema_path is not None:
        raise ValidationError("provider JSON schema must use either inline or file-backed form")
    if provider_tools is not None:
        if provider is not Provider.CLAUDE or type(provider_tools) is not tuple:
            raise ValidationError("exact provider-tool selection is supported only by Claude")
        for tool in provider_tools:
            text(tool, "provider tool", 128)
    if provider is Provider.GEMINI:
        argv = [executable, "-p", prompt, "--output-format", "json",
                "--approval-mode", permission_mode.value, "--skip-trust"]
        if requested_model:
            argv.extend(("--model", requested_model))
        return Invocation(provider, tuple(argv), OutputMode.JSON, requested_model,
                          permission_mode is PermissionMode.PLAN, permission_mode,
                          prompt_digest=hashlib.sha256(prompt.encode()).hexdigest(), prompt_text=prompt)
    if provider is Provider.ANTIGRAVITY:
        output_mode = OutputMode.STREAM_JSON if stream else OutputMode.JSON
        argv = [executable, "-p", prompt, "--output-format", output_mode.value,
                "--mode=plan" if permission_mode is PermissionMode.PLAN else "--mode=accept-edits", "--sandbox"]
        if json_schema is not None:
            argv.extend(("--json-schema", json_schema))
        if requested_model:
            argv.extend(("--model", requested_model))
        return Invocation(provider, tuple(argv), output_mode, requested_model,
                          permission_mode is PermissionMode.PLAN, permission_mode, json_schema,
                           prompt_digest=hashlib.sha256(prompt.encode()).hexdigest(), prompt_text=prompt)
    if provider is Provider.CODEX:
        sandbox = "read-only" if permission_mode is PermissionMode.PLAN else "workspace-write"
        argv = [executable, "exec", "--json", "--ignore-user-config", "--ignore-rules", "--sandbox", sandbox,
                "--skip-git-repo-check", "--ephemeral"]
        if requested_model:
            argv.extend(("--model", requested_model))
        if json_schema_path is not None:
            argv.extend(("--output-schema", json_schema_path))
        argv.append(prompt)
        return Invocation(provider, tuple(argv), OutputMode.JSON, requested_model,
                          permission_mode is PermissionMode.PLAN, permission_mode,
                           json_schema_path=json_schema_path, prompt_digest=hashlib.sha256(prompt.encode()).hexdigest(), prompt_text=prompt)
    if provider is Provider.OMNIRUSH:
        argv = [executable, "-p", prompt]
        if requested_model:
            argv.extend(("--model", requested_model))
        return Invocation(provider, tuple(argv), OutputMode.TEXT, requested_model,
                          permission_mode is PermissionMode.PLAN and sandbox_executable() is not None,
                          permission_mode, prompt_digest=hashlib.sha256(prompt.encode()).hexdigest(), prompt_text=prompt)
    if provider is Provider.COPILOT:
        argv = [executable, "-p", prompt, "--output-format", "json", "--no-ask-user"]
        if permission_mode is PermissionMode.PLAN:
            argv.extend(("--mode", "plan"))
        if requested_model:
            argv.extend(("--model", requested_model))
        return Invocation(provider, tuple(argv), OutputMode.JSON, requested_model,
                          permission_mode is PermissionMode.PLAN, permission_mode,
                          prompt_digest=hashlib.sha256(prompt.encode()).hexdigest(), prompt_text=prompt)
    argv = [executable, "-p", "--output-format", "json", "--no-session-persistence",
            "--permission-mode", "plan" if permission_mode is PermissionMode.PLAN else "dontAsk"]
    if provider_tools is not None:
        argv.extend(("--safe-mode", "--permission-prompts", "none", "--tools", " ".join(provider_tools),
                     "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'))
    if json_schema is not None:
        argv.extend(("--json-schema", json_schema))
    if requested_model:
        argv.extend(("--model", requested_model))
    argv.extend(("--", prompt))
    return Invocation(provider, tuple(argv), OutputMode.JSON, requested_model,
                      permission_mode is PermissionMode.PLAN, permission_mode, json_schema,
                       prompt_digest=hashlib.sha256(prompt.encode()).hexdigest(), prompt_text=prompt)


def validate_working_directory(path: Path) -> Path:
    if not isinstance(path, Path) or not path.is_dir():
        raise ValidationError(f"working directory does not exist: {path}")
    return path.resolve()
