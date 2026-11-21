"""O dicionario de entradas PINADO confere com o montador em toda decisao mapeada (PR-A, preparo do pin K1).

O gate do pin (`scripts/ci/verify_rco_rules_pin.py`) so usa a biblioteca padrao e nao carrega o dicionario de
entradas; o montador (`rco_montador.conferir_mapeamento`) recusa a decisao, em execucao, quando o dicionario
publicado diverge do que ele implementa. Este teste antecipa essa recusa para o CI do PR que muda o pin:

- pin `fixado`: o dicionario e lido de `config/integrations/rco/k1/` pelo MESMO caminho do runtime
  (`rco_pin.load_rco_rules_pin` e `rco_entradas.carregar_dicionario_entradas`, que confere o sha256 do
  arquivo, a versao, o `sha256_conteudo` e as entradas de cada decisao contra o manifesto);
- pin `sem_pacote` (hoje): o recorte real do K1 2.3.0 (`tests/fixtures/rco/k1-2.3.0-recorte/`, aed187d4), para
  que o teste rode hoje. O recorte e um corte de `entradas.json` (as entradas dos dominios do montador): o
  manifesto real e lido sem alteracao e so a linha do anexo `entradas` (nome e sha256 do ARQUIVO) e a lista de
  decisoes (as dos dominios mapeados) sao trocadas em memoria, para `carregar_dicionario_entradas` rodar sobre
  ele; versao, `sha256_conteudo` e entradas declaradas seguem as do manifesto real.

A troca e automatica: quando o lock passar a `fixado`, o mesmo teste le os arquivos pinados.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from types import MappingProxyType

import pytest

from maezo.platform import rco_manifesto as k1
from maezo.platform.rco_entradas import DicionarioEntradasK1, carregar_dicionario_entradas
from maezo.tools.workers import rco_montador as mt
from maezo.tools.workers import rco_pin
from tests.unit.platform import rco_k1_2_3_recorte as rec
from tests.unit.platform import rco_k1_sintetico as sint

_REPO_ROOT = Path(__file__).resolve().parents[4]
LOCK = _REPO_ROOT / rco_pin.RCO_RULES_PIN_RELATIVE_PATH
RULES = _REPO_ROOT / rco_pin.RCO_RULES_DIR
ENTRADAS_RECORTE = "entradas-recorte.json"


@dataclasses.dataclass(frozen=True)
class DicionarioDoPin:
    origem: str  # "pin" (arquivos pinados) ou "recorte" (sem pacote)
    manifesto: k1.ManifestoK1
    dicionario: DicionarioEntradasK1


def _recorte() -> tuple[k1.ManifestoK1, DicionarioEntradasK1]:
    manifesto = rec.manifesto()
    raw = (rec.RECORTE / ENTRADAS_RECORTE).read_bytes()
    anexo = k1.AnexoK1("entradas", ENTRADAS_RECORTE, k1.sha256_prefixado(raw))
    recortado = dataclasses.replace(
        manifesto,
        decisoes=MappingProxyType(
            {i: d for i, d in manifesto.decisoes.items() if d.dominio in mt.MAPEAMENTOS}
        ),
        anexos=MappingProxyType({**manifesto.anexos, "entradas": anexo}),
    )
    return recortado, carregar_dicionario_entradas(recortado, k1_dir=rec.RECORTE)


def dicionario_do_pin(lock_path: Path, rules_dir: Path) -> DicionarioDoPin:
    estado = json.loads(lock_path.read_text(encoding="utf-8"))["estado"]
    if estado == rco_pin.ESTADO_FIXADO:
        pin = rco_pin.load_rco_rules_pin(lock_path, rules_dir=rules_dir)
        assert pin.manifesto is not None and pin.k1_dir is not None
        return DicionarioDoPin(
            "pin", pin.manifesto, carregar_dicionario_entradas(pin.manifesto, k1_dir=pin.k1_dir)
        )
    assert estado == rco_pin.ESTADO_SEM_PACOTE, estado
    manifesto, dicionario = _recorte()
    return DicionarioDoPin("recorte", manifesto, dicionario)


def test_toda_decisao_mapeada_confere_com_o_dicionario_pinado() -> None:
    fonte = dicionario_do_pin(LOCK, RULES)
    decisoes = [d for d in fonte.manifesto.decisoes.values() if d.dominio in mt.MAPEAMENTOS]
    assert len(decisoes) > 2, fonte.origem
    assert {d.dominio for d in decisoes} == set(mt.MAPEAMENTOS), fonte.origem
    problemas = {d.id: mt.conferir_mapeamento(fonte.dicionario, d.dominio, d.entradas) for d in decisoes}
    assert {i: p for i, p in problemas.items() if p} == {}, fonte.origem


def test_o_pin_comitado_escolhe_a_fonte_pelo_estado() -> None:
    estado = json.loads(LOCK.read_text(encoding="utf-8"))["estado"]
    esperado = "pin" if estado == rco_pin.ESTADO_FIXADO else "recorte"
    assert dicionario_do_pin(LOCK, RULES).origem == esperado


def _pacote_com_dicionario_real(raiz: Path) -> None:
    """Pacote sintetico `fixado` cujo `entradas.json` e o recorte real (1.5.0) e cujas decisoes declaram uma
    entrada real (`dataAtendimento`), para provar que o pin `fixado` le o dicionario dos arquivos pinados."""
    manifesto = sint.montar(raiz)
    raw = (rec.RECORTE / ENTRADAS_RECORTE).read_bytes()
    dados = json.loads(raw)
    (raiz / sint.K1_DIR / "entradas.json").write_bytes(raw)
    manifesto["dicionario_entradas"] = {
        "path": "entradas.json",
        "sha256": dados["sha256_conteudo"],
        "sha256_arquivo": sint.sha(raw),
        "versao": dados["versao"],
    }
    data = next(e for e in dados["entradas"] if e["nome_dmn"] == "dataAtendimento")
    for decisao in manifesto["decisoes"]:
        decisao["entradas"] = [
            {
                "dominio_valores": None,
                "nome": "dataAtendimento",
                "nome_maezo": data["nome_maezo"],
                "nome_snake": data["nome_snake"],
                "origem": data["origem"],
                "typeRef": "string",
            }
        ]
    sint.regravar(raiz, manifesto)


def test_pin_fixado_le_o_dicionario_dos_arquivos_pinados(tmp_path: Path) -> None:
    _pacote_com_dicionario_real(tmp_path)
    fonte = dicionario_do_pin(tmp_path / sint.LOCK_REL, tmp_path / sint.RULES_DIR)
    assert fonte.origem == "pin"
    assert fonte.dicionario.versao == "1.5.0"
    assert fonte.manifesto.commit_sha == sint.COMMIT


def test_pin_fixado_com_dicionario_divergente_e_recusado(tmp_path: Path) -> None:
    """Fail-closed: `entradas.json` trocado depois do manifesto nao e lido (sha256 do arquivo diverge)."""
    _pacote_com_dicionario_real(tmp_path)
    caminho = tmp_path / sint.K1_DIR / "entradas.json"
    caminho.write_bytes(caminho.read_bytes() + b"\n")
    with pytest.raises((rco_pin.RcoRulesPinError, k1.ManifestoK1InvalidoError)):
        dicionario_do_pin(tmp_path / sint.LOCK_REL, tmp_path / sint.RULES_DIR)
