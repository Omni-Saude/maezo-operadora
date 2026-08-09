"""Privacy remediation flags and the log/key scrubbing they gate (DL-0043 leg (c)).

Two modules, deliberately separated:

- `phi_key_policy`: loads `spec/policies/privacy/phi-business-key-remediation.yaml` and resolves
  the EFFECTIVE mode. Fail-closed in the "does nothing" direction: every failure mode, and every
  unratified/DRAFT manifest, resolves to `PhiKeyMode.OFF` — today's behavior, byte-identical.
- `key_scrubber`: the structlog processor / value helpers that mode `scrub_only` (and above)
  installs. Inert unless the policy says otherwise.
"""

from maezo.platform.privacy.key_scrubber import (
    BUSINESS_KEY_LOG_FIELDS,
    PHI_KEY_ANCHOR_LOG_FIELDS,
    PSEUDONYMIZED_MESSAGE_KEY_FAMILIES,
    BusinessKeyScrubber,
    egress_message_key,
    egress_pseudonymizer,
    pseudonymize_message_key,
    reset_egress_pseudonymizer_cache,
    scrub_key_value,
)
from maezo.platform.privacy.phi_key_policy import (
    PHI_KEY_POLICY_PATH_ENV,
    PhiKeyMode,
    PhiKeyPolicy,
    load_phi_key_policy,
    phi_key_policy,
    reset_phi_key_policy_cache,
)

__all__ = [
    "BUSINESS_KEY_LOG_FIELDS",
    "PHI_KEY_ANCHOR_LOG_FIELDS",
    "PHI_KEY_POLICY_PATH_ENV",
    "PSEUDONYMIZED_MESSAGE_KEY_FAMILIES",
    "BusinessKeyScrubber",
    "PhiKeyMode",
    "PhiKeyPolicy",
    "egress_message_key",
    "egress_pseudonymizer",
    "load_phi_key_policy",
    "phi_key_policy",
    "pseudonymize_message_key",
    "reset_egress_pseudonymizer_cache",
    "reset_phi_key_policy_cache",
    "scrub_key_value",
]
