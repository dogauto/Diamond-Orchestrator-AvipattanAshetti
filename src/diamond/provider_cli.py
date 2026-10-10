"""Human-operated gateway diagnostics, separate from workflow execution."""

import argparse
import os
from typing import Any

from .config import DiamondConfig
from .errors import ValidationError
from .freellmapi import (FreeLLMAPIClient, FreeLLMAPIConfig, SMOKE_PROMPT, configured_probe,
                        load_gateway_config, reject_config_secret, save_gateway_config)
from .paths import ProjectPaths


def register(commands: Any) -> None:
    providers = commands.add_parser("provider", help="inspect local providers and opt-in gateway diagnostics")
    actions = providers.add_subparsers(dest="provider_command", required=True)
    listing = actions.add_parser("list", help="list configuration without invoking providers or the network")
    configure = actions.add_parser("configure", help="save non-secret FreeLLMAPI diagnostic configuration")
    configure.add_argument("name", choices=("freellmapi",))
    configure.add_argument("--url", required=True)
    configure.add_argument("--api-key-env", default="FREELLMAPI_API_KEY")
    configure.add_argument("--model", help="explicit model ID; no automatic routing aliases")
    configure.add_argument("--force", action="store_true", help="replace changed gateway configuration")
    models = actions.add_parser("models", help="bounded authenticated GET /v1/models")
    models.add_argument("name", choices=("freellmapi",))
    test = actions.add_parser("test", help="discover models, or explicitly confirm one completion request")
    test.add_argument("name", choices=("freellmapi",))
    test.add_argument("--model")
    test.add_argument("--prompt", help="optional diagnostic prompt; never sends project files")
    test.add_argument("--confirm", action="store_true", help="allow one live model request, possibly billable")
    for command in (models, test):
        command.add_argument("--timeout", type=float, default=10.0, help="wall-clock request deadline, up to 30 seconds")
    for command in (listing, configure, models, test):
        command.add_argument("--json", action="store_true", default=argparse.SUPPRESS)


def provider_command(args: argparse.Namespace, paths: ProjectPaths, config: DiamondConfig) -> tuple[dict[str, Any], int]:
    gateway_path = paths.state / "freellmapi.json"
    if args.provider_command == "list":
        local = [{"provider": name.value, "status": "enabled" if entry.enabled else "disabled",
                  "version": None, "executable": entry.executable, "auth_status": "NOT_CHECKED"}
                 for name, entry in config.providers.items()]
        gateway: dict[str, Any] = {"provider": "freellmapi", "configured": False,
                                   "auth_status": "NOT_CHECKED", "workflow_execution": "UNAVAILABLE"}
        if gateway_path.exists() or gateway_path.is_symlink():
            gateway_config = load_gateway_config(gateway_path)
            reject_config_secret(gateway_config, os.environ)
            gateway.update(gateway_config.to_dict(), configured=True,
                           auth_status=FreeLLMAPIClient(gateway_config).auth_status)
        return {"providers": local + [gateway]}, 0
    if args.provider_command == "configure":
        gateway_config = FreeLLMAPIConfig(args.url, args.api_key_env, args.model)
        reject_config_secret(gateway_config, os.environ)
        if gateway_path.exists() or gateway_path.is_symlink():
            previous = load_gateway_config(gateway_path)
            if previous != gateway_config and not args.force:
                raise ValidationError("FreeLLMAPI configuration already exists; use --force to replace it")
        save_gateway_config(gateway_path, gateway_config)
        return {"provider": "freellmapi", "configured": True, "config": str(gateway_path),
                **gateway_config.to_dict(), "workflow_execution": "UNAVAILABLE"}, 0
    if not gateway_path.exists() and not gateway_path.is_symlink():
        raise ValidationError("FreeLLMAPI is not configured; run provider configure freellmapi first")
    gateway_config = load_gateway_config(gateway_path)
    client = FreeLLMAPIClient(gateway_config, timeout_seconds=args.timeout)
    if args.provider_command == "models":
        return configured_probe(gateway_config, client).to_dict(), 0
    if args.model is None and args.prompt is None and not args.confirm:
        return {**configured_probe(gateway_config, client).to_dict(), "live_request": False}, 0
    if not args.confirm:
        raise ValidationError("provider test model requests require --confirm")
    selected_model = args.model or gateway_config.default_model
    if selected_model is None:
        raise ValidationError("provider test requires --model or a configured model; catalog order never selects a route")
    result = client.smoke(selected_model, args.prompt if args.prompt is not None else SMOKE_PROMPT)
    return {**result.to_dict(), "live_request": True}, 0
