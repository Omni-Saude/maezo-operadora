"""E-SF-18 permanent guards of the vendor lane (G-35 collective arc, SC-04 OP16).

Variant without gate: the A10 vendor channel stays `refused`; these tests keep
the v1 surface from growing the collective arc or the OP16 submission by
accident. The runtime refusal of the collective-arc seat (`acceptance.record`)
is already pinned by `test_commercial_admission_a10.py` (PURPOSE_DENIED, zero
effect); what is added here is the structural tie to the legitimacy bound set.
"""

from __future__ import annotations

import subprocess
import sys
from typing import get_args

from maezo.gateway.capabilities.models import OperationName
from maezo.gateway.capabilities.vendor_journey import VENDOR_OPERATIONS
from maezo.gateway.commercial_legitimacy_guard import COLLECTIVE_BOUND_SET

# Collective-arc commands (RN 659/2025 legitimacy bound set) that are also nucleo operations a source
# could prepare on the vendor journey. Dotted names outside the nucleo vocabulary cannot be prepared at all.
_NUCLEO_OPERATIONS = set(get_args(OperationName))
_COLLECTIVE_NUCLEO_OPERATIONS = tuple(sorted(COLLECTIVE_BOUND_SET & _NUCLEO_OPERATIONS))


def test_the_vendor_allowlist_never_intersects_the_collective_bound_set() -> None:
    assert COLLECTIVE_BOUND_SET.isdisjoint(VENDOR_OPERATIONS)
    # The seat the collective arc ends on is among the nucleo operations: the guard below has teeth.
    assert "acceptance.record" in _COLLECTIVE_NUCLEO_OPERATIONS


# SC-04 (ADD-SALES-CHANNEL): OP16 `channel.submission.submit` is outside the vendor v1 surface.
_OP16_OPERATION = "channel.submission.submit"


def test_op16_is_not_a_vendor_v1_operation_nor_a_nucleo_operation() -> None:
    assert _OP16_OPERATION not in VENDOR_OPERATIONS
    assert _OP16_OPERATION not in _NUCLEO_OPERATIONS


def test_importing_the_vendor_v1_surface_never_loads_the_op16_machine() -> None:
    # Import graph, not text: a clean interpreter imports the JR4 surface and must not have
    # pulled the OP16 submission machine (or its portal wire contract) in transitively.
    code = (
        "import sys\n"
        "import maezo.gateway.capabilities.vendor_journey\n"
        "banned = ('maezo.gateway.vendor_submissions', 'maezo.portal.contracts.vendor_submissions',"
        " 'maezo.portal.api.vendor_submissions')\n"
        "loaded = [m for m in banned if m in sys.modules]\n"
        "sys.exit(', '.join(loaded) if loaded else 0)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False, timeout=60
    )
    assert done.returncode == 0, done.stderr
