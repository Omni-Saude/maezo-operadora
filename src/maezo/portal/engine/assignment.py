"""E03 immutable number-free assignment command. Legacy v1 bytes are unchanged."""

from dataclasses import asdict, dataclass
from typing import Literal

from .profile import HumanCommand, ProfileError

GOVERNED_FIELDS = (
    "binding_ref",
    "binding_version",
    "binding_digest",
    "policy_ref",
    "policy_version",
    "policy_digest",
    "source_revision",
    "generation_digest",
    "target_ref",
    "target_membership_revision",
)


@dataclass(frozen=True, slots=True, kw_only=True)
class HumanAssignmentCommand(HumanCommand):
    operation: Literal["claim", "release", "reassign"]  # type: ignore[assignment]  # frozen distinct wire discriminator
    schema: Literal["human-assignment.v2"] = "human-assignment.v2"  # type: ignore[assignment]  # frozen distinct wire discriminator
    binding_ref: str
    binding_version: str
    binding_digest: str
    policy_ref: str
    policy_version: str
    policy_digest: str
    source_revision: str
    generation_digest: str
    target_ref: str | None
    target_membership_revision: str | None

    def __post_init__(self) -> None:
        import re

        values = asdict(self)
        old = {key: value for key, value in values.items() if key not in GOVERNED_FIELDS}
        old.update(schema="human-command.v1", operation="claim", outcome=None)
        HumanCommand(**old)
        if (
            self.schema != "human-assignment.v2"
            or self.operation not in ("claim", "release", "reassign")
            or self.outcome is not None
        ):
            raise ProfileError("unsupported governed command")
        for key in ("binding_ref", "policy_ref", "target_ref"):
            value = values[key]
            if value is None and key == "target_ref":
                continue
            if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}", value):
                raise ProfileError("invalid governed reference")
        for key in ("binding_digest", "policy_digest", "generation_digest"):
            if type(values[key]) is not str or not re.fullmatch(r"[0-9a-f]{64}", values[key]):
                raise ProfileError("invalid governed digest")
        for key in (
            "process_definition_version",
            "form_version",
            "task_revision",
            "authority_revision",
            "membership_revision",
            "evidence_revision",
            "binding_version",
            "policy_version",
            "source_revision",
            "target_membership_revision",
        ):
            value = values[key]
            if value is None and key == "target_membership_revision":
                continue
            if (
                type(value) is not str
                or not re.fullmatch(r"0|[1-9][0-9]*", value)
                or len(value) > 19
                or int(value) >= 2**63
                or key.endswith("version")
                and int(value) == 0
            ):
                raise ProfileError("invalid governed revision")
        if self.operation == "reassign":
            if self.target_ref is None or self.target_membership_revision is None:
                raise ProfileError("governed target required")
        elif self.target_ref is not None or self.target_membership_revision is not None:
            raise ProfileError("unexpected governed target")
