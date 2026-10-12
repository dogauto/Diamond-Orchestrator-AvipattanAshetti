"""Structural preregistration validation; this does not authorize a live trial."""

import json
from pathlib import Path

from .agent_profiles import ProfileCatalog, digest, read_source
from .errors import ValidationError
from .validation import bounded_int, canonical_json, exact_keys, text


TEXT_FIELDS = {"trial_id", "hypothesis", "workload", "task_lineage_split", "holdout_policy", "assignment_scheme",
               "blinded_acceptance_procedure", "useful_outcome", "evaluator_version", "all_assignment_accounting",
               "unknown_outcome_handling", "privacy_provider_eligibility", "stopping_rule", "economic_hurdle",
               "maintenance_owner", "evaluation_owner"}
LIMIT_FIELDS = {"max_assignments", "max_provider_calls", "max_wall_clock_seconds", "max_cost_microunits",
                "outcome_maturation_hours"}
IDENTITY_FIELDS = {"schema_version", "role", "provider", "catalog_digest", "profile_ids", "blinded_acceptance"}


def trial_template(catalog: ProfileCatalog) -> dict:
    return {**{name: "" for name in sorted(TEXT_FIELDS)}, **{name: None for name in sorted(LIMIT_FIELDS)},
            "schema_version": 1, "role": "builder", "provider": "claude", "catalog_digest": catalog.catalog_digest,
            "profile_ids": [item.profile_id for item in catalog.records], "blinded_acceptance": False}


def validate_trial_design(value: object, catalog: ProfileCatalog) -> dict:
    data = dict(exact_keys(value, TEXT_FIELDS | LIMIT_FIELDS | IDENTITY_FIELDS, "trial design"))
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValidationError("trial design requires schema version 1")
    for field in TEXT_FIELDS:
        text(data[field], field, 4096)
        if data[field].strip().lower() in {"todo", "tbd", "unknown", "placeholder"}:
            raise ValidationError(f"trial design {field} remains unspecified")
    for field in LIMIT_FIELDS:
        bounded_int(data[field], field, 1, 10**15 if field == "max_cost_microunits" else 1_000_000)
    if data["max_provider_calls"] < data["max_assignments"]:
        raise ValidationError("provider-call budget cannot cover the planned assignments")
    if data["blinded_acceptance"] is not True:
        raise ValidationError("live M2 trial requires blinded acceptance")
    if (data["role"], data["provider"]) != ("builder", "claude"):
        raise ValidationError("first M2 trial must keep the Claude Builder route fixed")
    catalog.verify()
    if data["catalog_digest"] != catalog.catalog_digest:
        raise ValidationError("trial profile catalog digest is stale")
    if (type(data["profile_ids"]) is not list or len(data["profile_ids"]) != 2 or
            any(type(item) is not str for item in data["profile_ids"]) or
            set(data["profile_ids"]) != {record.profile_id for record in catalog.records}):
        raise ValidationError("trial must name the two catalog profiles")
    return {"structurally_valid": True, "trial_design_digest": digest(canonical_json(data)), "design": data,
            "live_execution_authorized": False,
            "remaining_gates": ["operator/evaluation approval of the design and real workload",
                                "persisted assignments, shared budgets and blinded outcome accounting",
                                "versioned workflow profile opt-in and dispatch acceptance"],
            "execution": {"provider_launch": False, "source_publication": False}}


def load_trial_design(path: Path, catalog: ProfileCatalog) -> dict:
    try:
        source = read_source(path.absolute(), path.absolute().parent, 65536)

        def distinct_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValidationError("trial design contains duplicate keys")
                result[key] = value
            return result

        value = json.loads(source, object_pairs_hook=distinct_keys)
        return validate_trial_design(value, catalog)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValidationError("trial design must be bounded valid JSON") from exc
