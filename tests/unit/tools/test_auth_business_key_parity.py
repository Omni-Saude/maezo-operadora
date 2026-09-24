"""T1.11 (D-K): Python composer x Java engine parity over ONE shared vector.

The same JSON is read by `AuthBusinessKeysTest.java`; if either side drifts, one of the two
suites goes red. The guide-number half checks the signed `guide` DTO pattern on both sides.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from maezo.gateway.human.auth_profile import GUIDE_NUMBER_PATTERN, NumeroGuiaTiss
from maezo.tools.process_business_keys import BusinessKeyComponentError, auth_business_key

VECTORS = json.loads(
    (
        Path(__file__).parents[3]
        / "src/maezo/portal/engine/java/src/test/resources/auth-business-key-vectors.json"
    ).read_text(encoding="utf-8")
)
NUMBER = TypeAdapter(NumeroGuiaTiss)


@pytest.mark.parametrize("row", VECTORS["keys"])
def test_shared_vector_key(row: dict[str, str]) -> None:
    assert auth_business_key(tenant_id=row["tenant"], numero_guia_tiss=row["numero_guia_tiss"]) == row["key"]


@pytest.mark.parametrize("row", VECTORS["refused_keys"])
def test_shared_vector_refused(row: dict[str, str]) -> None:
    with pytest.raises(BusinessKeyComponentError):
        auth_business_key(tenant_id=row["tenant"], numero_guia_tiss=row["numero_guia_tiss"])


@pytest.mark.parametrize("number", VECTORS["guide_numbers_valid"])
def test_guide_number_valid(number: str) -> None:
    assert NUMBER.validate_python(number) == number


@pytest.mark.parametrize("number", VECTORS["guide_numbers_invalid"])
def test_guide_number_invalid(number: str) -> None:
    with pytest.raises(ValidationError):
        NUMBER.validate_python(number)


def test_java_pattern_is_the_python_pattern() -> None:
    java = (
        Path(__file__).parents[3]
        / "src/maezo/portal/engine/java/src/main/java/br/com/maezo/human/AuthModels.java"
    ).read_text(encoding="utf-8")
    found = re.search(r'GUIDE_NUMBER=java\.util\.regex\.Pattern\.compile\("([^"]+)"\)', java)
    assert found is not None
    assert f"^{found.group(1)}$" == GUIDE_NUMBER_PATTERN
