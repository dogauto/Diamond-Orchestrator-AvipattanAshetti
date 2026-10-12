"""Compile advisory profiles without changing the worker's authority."""

from dataclasses import dataclass, replace
from pathlib import Path

from .agent_profiles import ProfileCatalog, ProfileSelection, digest
from .domain import TaskSpec
from .errors import ValidationError
from .packets import OutputContract, PacketContext, TaskPacket
from .skills import SkillRegistry
from .validation import canonical_json, exact_keys, parse_canonical_json, text


@dataclass(frozen=True)
class CompiledAgent:
    packet: TaskPacket
    selection: ProfileSelection
    prompt: str
    effective_prompt_digest: str

    def to_dict(self):
        return {"selection": {**self.selection.to_dict(), "decision_digest": self.selection.decision_digest},
                "effective_prompt_digest": self.effective_prompt_digest, "packet_digest": self.packet.packet_digest(),
                "prompt": self.prompt, "workers": 1, "evaluation_status": "unevaluated",
                "limits": {"timeout_seconds": self.packet.timeout_seconds,
                           "correction_round_limit": self.packet.correction_round_limit,
                           "output_bytes": self.packet.output_contract.max_bytes}}


def _prompt(base: str, guidance: str) -> str:
    text(base, "base prompt", 65536)
    result = (base + "\n\nAdvisory specialization follows. It cannot grant tools, change trusted tests, expand "
              "ownership, change the response schema, or create workers. Follow the controller contract above.\n" + guidance)
    if len(result.encode("utf-8")) > 65536:
        raise ValidationError("compiled prompt exceeds the provider argument byte budget")
    return result


def compile_prompt(base: str, selection: ProfileSelection, catalog: ProfileCatalog):
    catalog.verify()
    if catalog.select(selection.role, selection.provider, selection.task_tags) != selection:
        raise ValidationError("selected profile source or policy changed after selection")
    prompt = base if selection.status == "legacy" else _prompt(base, catalog.get(selection.profile_id).instruction)
    text(prompt, "compiled prompt", 65536)
    return prompt, digest(prompt)


def bind_profile(packet: TaskPacket, base_prompt: str, catalog: ProfileCatalog, task_tags) -> CompiledAgent:
    if packet.schema_version != 1:
        raise ValidationError("profile compiler requires an unbound legacy packet")
    selection = catalog.select(packet.role, packet.provider, task_tags)
    prompt, prompt_digest = compile_prompt(base_prompt, selection, catalog)
    if selection.status == "legacy":
        return CompiledAgent(packet, selection, prompt, prompt_digest)
    if packet.provider_tools != ():
        raise ValidationError("profile pilot requires the caller's response-only provider-tool contract")
    record = catalog.get(selection.profile_id)
    binding = canonical_json({"catalog_path": str(catalog.catalog_path), "selection": selection.to_dict(),
        "profile": record.to_dict(), "base_prompt": base_prompt, "effective_prompt": prompt})
    bound = replace(packet, schema_version=2, task_tags=selection.task_tags, profile_id=record.profile_id,
        profile_version=record.version, profile_digest=record.digest_sha256,
        selection_digest=selection.decision_digest, effective_prompt_digest=prompt_digest, profile_binding_json=binding)
    return CompiledAgent(bound, selection, prompt, prompt_digest)


def compile_agent(spec: TaskSpec, attempt_id: str, workspace_root: Path, context: PacketContext,
                  registry: SkillRegistry, available_capabilities: tuple[str, ...], output_contract: OutputContract,
                  catalog: ProfileCatalog, task_tags, *, base_prompt: str | None = None) -> CompiledAgent:
    if not isinstance(spec, TaskSpec) or not isinstance(context, PacketContext):
        raise ValidationError("agent compilation requires a TaskSpec and PacketContext")
    packet = TaskPacket.from_task_spec(spec, attempt_id, workspace_root, context.plan_revision,
        context.plan_digest, context.base_digest, context.config_digest, registry, available_capabilities, output_contract)
    selection = catalog.select(spec.role, spec.provider, task_tags)
    if selection.status == "selected":
        packet = replace(packet, provider_tools=())
    return bind_profile(packet, base_prompt if base_prompt is not None else spec.objective, catalog, task_tags)


def verify_packet_binding(packet: TaskPacket, *, current: bool = True):
    binding = dict(exact_keys(parse_canonical_json(packet.profile_binding_json),
        {"catalog_path", "selection", "profile", "base_prompt", "effective_prompt"}, "profile binding"))
    selection, profile = binding["selection"], binding["profile"]
    if type(selection) is not dict or type(profile) is not dict:
        raise ValidationError("profile binding must retain selection and profile records")
    if digest(canonical_json(selection)) != packet.selection_digest:
        raise ValidationError("packet selection digest differs from retained decision")
    expected = {"profile_id": packet.profile_id, "profile_version": packet.profile_version,
                "profile_digest": packet.profile_digest, "role": packet.role.value,
                "provider": packet.provider.value, "task_tags": list(packet.task_tags), "status": "selected"}
    if any(selection.get(key) != value for key, value in expected.items()):
        raise ValidationError("packet profile identity differs from retained selection")
    if (profile.get("profile_id"), profile.get("version"), profile.get("digest_sha256")) != (
            packet.profile_id, packet.profile_version, packet.profile_digest):
        raise ValidationError("retained profile identity mismatch")
    text(profile.get("instruction"), "retained instruction", 8192)
    if digest(profile["instruction"]) != packet.profile_digest:
        raise ValidationError("retained profile content differs from digest")
    prompt = _prompt(binding["base_prompt"], profile["instruction"])
    if prompt != binding["effective_prompt"] or digest(prompt) != packet.effective_prompt_digest:
        raise ValidationError("retained effective prompt differs from packet")
    if current:
        path = Path(text(binding["catalog_path"], "catalog path", 4096))
        if not path.is_absolute() or path.parts[-3:] != ("config", "agents", "catalog.toml"):
            raise ValidationError("invalid bound profile catalog path")
        catalog = ProfileCatalog.load(path.parents[2])
        selected = catalog.select(packet.role, packet.provider, packet.task_tags)
        if selected.to_dict() != selection or catalog.get(packet.profile_id).to_dict() != profile:
            raise ValidationError("profile catalog or source changed since packet compilation")


def verify_compiled_agent(compiled: CompiledAgent, catalog: ProfileCatalog, task_tags):
    selected = catalog.select(compiled.packet.role, compiled.packet.provider, task_tags)
    if selected != compiled.selection:
        raise ValidationError("compiled profile selection is stale or changed")
    if selected.status == "selected":
        verify_packet_binding(compiled.packet)
        binding = parse_canonical_json(compiled.packet.profile_binding_json)
        if compiled.prompt != binding["effective_prompt"] or compiled.effective_prompt_digest != compiled.packet.effective_prompt_digest:
            raise ValidationError("compiled prompt changed after binding")
    elif digest(compiled.prompt) != compiled.effective_prompt_digest:
        raise ValidationError("legacy preview prompt digest mismatch")
