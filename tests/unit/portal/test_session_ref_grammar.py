"""`session_ref` nasce na gramatica `Ref` (C1: `/cases` 503 intermitente com ref iniciado em `-`/`_`)."""

from __future__ import annotations

import re

from maezo.gateway import oidc
from maezo.portal.api.auth import opaque_ref

_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}$")


def test_opaque_ref_descarta_sorteio_com_primeiro_caractere_fora_da_gramatica(monkeypatch):
    draws = iter(["-" + "a" * 42, "_" + "b" * 42, "C" + "c" * 42])
    monkeypatch.setattr(oidc.secrets, "token_urlsafe", lambda n: next(draws))
    assert opaque_ref() == "C" + "c" * 42


def test_opaque_ref_sempre_casa_a_gramatica_ref():
    values = [opaque_ref() for _ in range(5000)]
    assert all(_REF.fullmatch(v) and len(v) == 43 for v in values)
    assert len(set(values)) == len(values)


def test_login_grava_session_ref_pela_gramatica_ref():
    from maezo.portal.api import session

    assert session.opaque_ref is opaque_ref
