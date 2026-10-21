"""`restaurar`: remonta o que o `gerar` lia do volume local a partir dos segredos vivos (DL-0113).

A prova contra os segredos REAIS e o modo `verificar` da task `staff-material` (dry-run, so hashes);
aqui ficam as recusas que nao precisam de segredo e a leitura do truststore Java do segredo nativo.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, pkcs12
from cryptography.x509.oid import NameOID
from tools.staff_materials.restaurar import Restaurado, Segredos, _ca_de_clientes_nativa, _exige, restaurar
from tools.staff_materials.secure_io import MaterialError


def _ca(nome: str) -> x509.Certificate:
    chave = ec.generate_private_key(ec.SECP256R1())
    assunto = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nome)])
    agora = datetime.now(UTC)
    return (
        x509.CertificateBuilder()
        .subject_name(assunto)
        .issuer_name(assunto)
        .public_key(chave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(agora)
        .not_valid_after(agora + timedelta(days=1))
        .sign(chave, hashes.SHA256())
    )


def _truststore(*pares: tuple[x509.Certificate, bytes]) -> bytes:
    return pkcs12.serialize_java_truststore(
        [pkcs12.PKCS12Certificate(c, alias) for c, alias in pares], serialization.NoEncryption()
    )


def test_a_ca_de_clientes_nativa_sai_do_truststore_pelo_alias() -> None:
    nativa, humana = _ca("native"), _ca("human")
    p12 = _truststore((nativa, b"maezo-native-client-ca"), (humana, b"maezo-human-client-ca"))
    assert _ca_de_clientes_nativa(p12) == nativa.public_bytes(Encoding.PEM)


def test_truststore_sem_a_ca_nativa_e_recusado() -> None:
    p12 = _truststore((_ca("human"), b"maezo-human-client-ca"))
    with pytest.raises(MaterialError):
        _ca_de_clientes_nativa(p12)


def test_truststore_ilegivel_e_recusado() -> None:
    with pytest.raises(MaterialError):
        _ca_de_clientes_nativa(b"nao e pkcs12")


def test_conferencia_que_falha_levanta_e_a_que_passa_fica_registrada() -> None:
    r = Restaurado()
    _exige(True, "a == a", r)
    assert r.conferencias == {"a == a": True}
    with pytest.raises(MaterialError, match="conferencia por hash falhou: b == c"):
        _exige(False, "b == c", r)


def test_segredo_staff_fora_do_formato_e_recusado_sem_ler_mais_nada() -> None:
    vazio: dict[str, object] = {}
    segredos = Segredos(native=vazio, staff=vazio, job=vazio, rows=vazio, assignment=vazio)  # type: ignore[arg-type]
    with pytest.raises(MaterialError, match="portal-staff-secret-bundle"):
        restaurar(segredos)
