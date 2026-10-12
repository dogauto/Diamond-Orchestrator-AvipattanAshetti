"""Provider routing policy for Diamond orchestrator.

Implements role-based provider selection with:
- Primary + ordered fallback providers per role
- Rate-limit awareness (skip if remaining < threshold)
- Quarantine awareness (skip quarantined providers)
- Cooldown enforcement (min time between switches)
"""

from dataclasses import dataclass, field
from typing import Any, Mapping

from .config import DiamondConfig
from .diagnostics import RateLimitInfo
from .domain import Provider, Role
from .errors import ValidationError
from .provider_health import ProviderHealth, ProviderHealthStore
from .validation import bounded_int


@dataclass(frozen=True)
class RoutingRule:
    """Routing rule for a single role."""
    role: Role
    primary: Provider
    fallbacks: tuple[Provider, ...]
    min_remaining: int
    require_healthy: bool
    cooldown_seconds: float


@dataclass(frozen=True)
class RoutingPolicy:
    """Complete routing policy for all roles."""
    rules: Mapping[Role, RoutingRule]
    default_min_remaining: int = 1
    default_cooldown: float = 300.0


# Default routing rules when not configured in TOML
_DEFAULT_RULES: dict[Role, tuple[Provider, tuple[Provider, ...]]] = {
    Role.BUILDER: (Provider.CLAUDE, (Provider.CODEX, Provider.OMNIRUSH, Provider.COPILOT, Provider.ANTIGRAVITY, Provider.GEMINI)),
    Role.DEBUGGER: (Provider.CODEX, (Provider.CLAUDE, Provider.OMNIRUSH, Provider.COPILOT, Provider.ANTIGRAVITY, Provider.GEMINI)),
    Role.INTEGRATOR: (Provider.CLAUDE, (Provider.CODEX, Provider.OMNIRUSH, Provider.COPILOT)),
    Role.QA: (Provider.COPILOT, (Provider.CODEX, Provider.CLAUDE, Provider.OMNIRUSH)),
    Role.REVIEWER: (Provider.CODEX, (Provider.CLAUDE, Provider.OMNIRUSH, Provider.COPILOT)),
    Role.RELEASE: (Provider.COPILOT, (Provider.CODEX, Provider.CLAUDE, Provider.OMNIRUSH)),
    Role.PLANNER: (Provider.CLAUDE, (Provider.CODEX, Provider.OMNIRUSH, Provider.COPILOT)),
    Role.RESEARCHER: (Provider.CLAUDE, (Provider.CODEX, Provider.OMNIRUSH, Provider.COPILOT)),
    Role.ARCHITECT: (Provider.CLAUDE, (Provider.CODEX, Provider.OMNIRUSH, Provider.COPILOT)),
}


def load_routing_policy(config: DiamondConfig) -> RoutingPolicy:
    """Load routing policy from config. Uses defaults for missing entries."""
    # TODO: parse [routing] section from TOML config
    # For now, use sensible defaults based on role
    rules: dict[Role, RoutingRule] = {}
    for role, (primary, fallbacks) in _DEFAULT_RULES.items():
        rules[role] = RoutingRule(
            role=role,
            primary=primary,
            fallbacks=fallbacks,
            min_remaining=1,
            require_healthy=True,
            cooldown_seconds=300.0,
        )
    return RoutingPolicy(rules=rules)


def select_provider(
    policy: RoutingPolicy,
    health_store: ProviderHealthStore,
    role: Role,
    exclude: set[Provider] | None = None,
    min_remaining: int | None = None,
    now: float | None = None,
) -> Provider:
    """Select the best available provider for a role.

    Args:
        policy: The routing policy to use
        health_store: Provider health store for querying state
        role: The role requiring a provider
        exclude: Providers to explicitly exclude
        min_remaining: Override minimum rate limit (uses rule's if None)
        now: Current monotonic timestamp (default: now)

    Returns:
        The selected Provider enum value

    Raises:
        ValidationError: If no provider is available for the role
    """
    if now is None:
        import time
        now = time.monotonic()
    if exclude is None:
        exclude = set()

    rule = policy.rules.get(role)
    if rule is None:
        raise ValidationError(f"no routing rule for role {role.value}")

    candidates: list[Provider] = [rule.primary] + list(rule.fallbacks)
    min_rem = min_remaining if min_remaining is not None else rule.min_remaining

    for provider in candidates:
        if provider in exclude:
            continue

        # Check that provider is enabled in config
        if provider not in config.providers or not config.providers[provider].enabled:
            continue

        # Get health state
        health = health_store.get_health(provider)

        # Skip if quarantined and not expired
        if rule.require_healthy and health.is_quarantined:
            continue

        # Check rate limit
        if health.rate_limit is not None:
            if health.rate_limit.remaining is not None and health.rate_limit.remaining < min_rem:
                continue

        # Check cooldown (last successful dispatch was too recent)
        if rule.cooldown_seconds > 0 and health.last_successful_dispatch_ts is not None:
            elapsed = now - health.last_successful_dispatch_ts
            if elapsed < rule.cooldown_seconds:
                continue

        # Provider is healthy and available
        return provider

    raise ValidationError(f"no healthy provider available for role {role.value}")


def list_available_providers(
    policy: RoutingPolicy,
    health_store: ProviderHealthStore,
    role: Role,
) -> list[dict[str, Any]]:
    """List all providers that could serve a role, in priority order."""
    rule = policy.rules.get(role)
    if rule is None:
        return []

    all_providers = [rule.primary] + list(rule.fallbacks)
    result = []
    for provider in all_providers:
        health = health_store.get_health(provider)
        available = health.is_healthy(rule.min_remaining)
        result.append({
            "provider": provider.value,
            "available": available,
            "health": health.to_dict(),
        })
    return result


def print_routing_status(policy: RoutingPolicy, health_store: ProviderHealthStore) -> None:
    """Print routing status to stdout (for CLI use)."""
    print("\nProvider Routing Status")
    print("=" * 80)
    for role in sorted(policy.rules.keys()):
        rule = policy.rules[role]
        candidates = [rule.primary] + list(rule.fallbacks)
        print(f"\n{role.value}:")
        print(f"  Primary: {rule.primary.value}")
        print(f"  Fallbacks: {', '.join(p.value for p in rule.fallbacks)}")
        print(f"  Min remaining: {rule.min_remaining}")
        print(f"  Cooldown: {rule.cooldown_seconds}s")
        for provider in candidates:
            health = health_store.get_health(provider)
            status = "✓" if health.is_healthy() else "✗"
            quota = f" quota={health.rate_limit.remaining}" if health.rate_limit else ""
            quar = " QUARANTINED" if health.is_quarantined else ""
            print(f"    {status} {provider.value}: last_probe={health.last_probe_ts:.0f}{quota}{quar}")
    print()