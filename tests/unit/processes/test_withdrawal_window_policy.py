"""EP-REG-03a: politica parametrizada da janela de arrependimento (portao RL-REG-03 fechado)."""

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.platform.saida_a_pedido.arrependimento import (
    DeclaracaoDestacada,
    PedidoArrependimento,
    PedidoRecusado,
    pedido_de_payload,
    receber_pedido,
)
from maezo.platform.saida_a_pedido.janela import AncoraDeFonte, AncoraNaoAutenticada, avaliar_janela
from maezo.platform.saida_a_pedido.politica import (
    PoliticaInvalida,
    carregar_politica_janela,
    lint_copy,
)

RAIZ = Path(__file__).resolve().parents[3]
JANELA_YAML = RAIZ / "spec" / "policies" / "saida-a-pedido" / "janela-arrependimento.yaml"
JURIDICO_COMPLETO: dict[str, Any] = {
    "aprovado": True,
    "aprovador": "Fulana",
    "data": "2026-11-16",
    "evidencia_ref": "ata-teste",
}


def carregar(path: Path) -> dict[str, Any]:
    dados = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(dados, dict)
    return dados


def gravar(tmp_path: Path, dados: dict[str, Any], nome: str = "politica.yaml") -> Path:
    destino = tmp_path / nome
    destino.write_text(yaml.safe_dump(dados, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return destino


D0 = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)


def _dia(n: int) -> datetime:
    return D0 + timedelta(days=n)


def _ratificada(tmp_path: Path, variante: str) -> Path:
    dados = carregar(JANELA_YAML)
    dados["status"] = "RATIFICADO"
    dados["variante_ativa"] = variante
    dados["ratificacao"]["juridico"] = dict(JURIDICO_COMPLETO)
    return gravar(tmp_path, dados)


def _pedido() -> PedidoArrependimento:
    return PedidoArrependimento(
        pedido_ref="p-1",
        contrato_ref="c-1",
        abertura_ref="clique-abertura",
        envio_ref="clique-envio",
        assinatura_ref="assin-1",
        declaracao=DeclaracaoDestacada(
            tipo_do_ato="declaracao_art8_par1",
            ato_ref="marcacao-1",
            estado_inicial_renderizado="desmarcada",
            marcada_em=_dia(0),
            texto_digest="digest-nao-ratificado",
            origem="sessao_autenticada",
        ),
    )


def _vigencia(instante: datetime) -> AncoraDeFonte:
    return AncoraDeFonte(nome="vigencia_start", instante=instante, receipt_ref="rv-1", origem="fonte")


def _assinatura(instante: datetime) -> AncoraDeFonte:
    return AncoraDeFonte(nome="signed_at", instante=instante, receipt_ref="ra-1", origem="fonte")


def test_withdrawal_never_autodenies(tmp_path: Path) -> None:
    politica = carregar_politica_janela(_ratificada(tmp_path, "A"))
    ancoras = [_vigencia(_dia(0))]
    janela = avaliar_janela(politica, ancoras, _dia(9))
    assert janela.situacao == "FORA"
    assert janela.encaminhamento == "route_to_human"
    recebimento = receber_pedido(_pedido(), ancoras=ancoras, recebido_em=_dia(9), politica=politica)
    assert recebimento.protocolo.startswith("ARR-")
    assert recebimento.situacao_janela == "FORA"
    assert recebimento.encaminhamento == "route_to_human"
    for valor in (*asdict(janela).values(), *asdict(recebimento).values()):
        assert "refus" not in str(valor).lower()
        assert "recus" not in str(valor).lower()


def test_auto_refusal_rule_rejected_by_loader(tmp_path: Path) -> None:
    dados = carregar(JANELA_YAML)
    dados["fora_da_janela"] = "refused"
    with pytest.raises(PoliticaInvalida) as exc:
        carregar_politica_janela(gravar(tmp_path, dados))
    assert exc.value.code == "AUTO_REFUSAL_FORBIDDEN_PENDING_Q3"


def test_withdrawal_anchor_requires_source_receipt() -> None:
    with pytest.raises(AncoraNaoAutenticada) as exc:
        AncoraDeFonte(nome="signed_at", instante=_dia(0), receipt_ref="r", origem="navegador")
    assert exc.value.code == "anchor_unauthenticated"
    with pytest.raises(PedidoRecusado) as exc2:
        pedido_de_payload(
            {
                "pedido_ref": "p",
                "contrato_ref": "c",
                "abertura_ref": "a",
                "envio_ref": "e",
                "assinatura_ref": "s",
                "signed_at": "2026-11-10T10:00:00-03:00",
            }
        )
    assert exc2.value.code == "anchor_unauthenticated"
    politica = carregar_politica_janela()
    sem_ancora = avaliar_janela(politica, [], _dia(3))
    assert sem_ancora.situacao == "INDETERMINADA"
    assert sem_ancora.encaminhamento == "route_to_human"


def test_numeric_promise_hidden_until_ratified(tmp_path: Path) -> None:
    codigos = lint_copy(
        "Arrependa-se em até 7 dias, em 1 clique",
        promessa_ratificada=False,
        prazo_cancelamento_ratificado=False,
    )
    assert "WITHDRAWAL_PROMISE_BEFORE_Q3" in codigos
    dados = carregar(JANELA_YAML)
    dados["display_numeric_promise"] = True
    with pytest.raises(PoliticaInvalida) as exc:
        carregar_politica_janela(gravar(tmp_path, dados, "b.yaml"))
    assert exc.value.code == "NUMERIC_PROMISE_REQUIRES_RATIFICATION"
    dados = carregar(JANELA_YAML)
    dados["textos"]["existencia_do_direito"] = "Você pode desistir em 7 dias."
    with pytest.raises(PoliticaInvalida) as exc2:
        carregar_politica_janela(gravar(tmp_path, dados, "c.yaml"))
    assert exc2.value.code == "NUMERIC_PROMISE_REQUIRES_RATIFICATION"
    for texto in carregar(JANELA_YAML)["textos"].values():
        assert lint_copy(texto, promessa_ratificada=False, prazo_cancelamento_ratificado=False) == ()


@pytest.mark.parametrize(
    ("variante", "esperado"),
    [
        ("A", {"vigencia_start": 27}),
        ("B", {"signed_at": 7}),
        ("UNRATIFIED_UNION", {"signed_at": 7, "vigencia_start": 27}),
        ("D", {"signed_at": 7, "vigencia_start": 27}),
    ],
)
def test_policy_returns_window_for_each_variant(
    tmp_path: Path, variante: str, esperado: dict[str, int]
) -> None:
    politica = carregar_politica_janela(_ratificada(tmp_path, variante))
    janela = avaliar_janela(politica, [_assinatura(_dia(0)), _vigencia(_dia(20))], _dia(1))
    assert janela.variante == variante
    assert dict(janela.termos_finais) == {nome: _dia(dias) for nome, dias in esperado.items()}
    assert janela.encaminhamento == "route_to_human"


def test_variant_d_reports_two_open_regimes(tmp_path: Path) -> None:
    # No desenho: assinatura D0 e vigencia D20 com pedido no dia 21 -> nao ha dois regimes abertos
    # (a assinatura fecha no dia 7). Para provar a sobreposicao usa-se vigencia D3 e pedido D5.
    politica = carregar_politica_janela(_ratificada(tmp_path, "D"))
    janela = avaliar_janela(politica, [_assinatura(_dia(0)), _vigencia(_dia(3))], _dia(5))
    assert janela.situacao == "DENTRO"
    assert janela.regimes_abertos == {"CDC-art49", "RN413-art8"}
    apos = avaliar_janela(politica, [_assinatura(_dia(0)), _vigencia(_dia(20))], _dia(21))
    assert apos.regimes_abertos == {"RN413-art8"}


def test_union_is_contiguous_and_lone_anchor_is_never_fora() -> None:
    politica = carregar_politica_janela()
    contigua = avaliar_janela(politica, [_assinatura(_dia(0)), _vigencia(_dia(20))], _dia(15))
    assert contigua.situacao == "DENTRO"
    so_vigencia_fora = avaliar_janela(politica, [_vigencia(_dia(0))], _dia(30))
    assert so_vigencia_fora.situacao == "INDETERMINADA"


def test_default_is_unratified_union_without_numeric_promise() -> None:
    politica = carregar_politica_janela()
    assert politica.variante_ativa == "UNRATIFIED_UNION"
    assert politica.display_numeric_promise is False
    assert politica.ratificada is False


def test_variant_switch_is_data_only(tmp_path: Path) -> None:
    politica = carregar_politica_janela(_ratificada(tmp_path, "B"))
    assert politica.variante_ativa == "B"
    assert politica.ratificada is True
    assert [a.nome for a in politica.ancoras_da_variante] == ["signed_at"]
    dados = carregar(JANELA_YAML)
    dados["variante_ativa"] = "B"
    with pytest.raises(PoliticaInvalida) as exc:
        carregar_politica_janela(gravar(tmp_path, dados, "sem-bloco.yaml"))
    assert exc.value.code == "VARIANT_REQUIRES_RATIFICATION"


def test_unknown_key_refused(tmp_path: Path) -> None:
    dados: dict[str, Any] = carregar(JANELA_YAML)
    dados["prazo_dias"] = 7
    with pytest.raises(PoliticaInvalida) as exc:
        carregar_politica_janela(gravar(tmp_path, dados))
    assert exc.value.code == "UNKNOWN_KEY"


def test_loader_fails_closed_on_missing_or_malformed(tmp_path: Path) -> None:
    with pytest.raises(PoliticaInvalida) as exc:
        carregar_politica_janela(tmp_path / "nao-existe.yaml")
    assert exc.value.code == "POLICY_MISSING"
    quebrado = tmp_path / "q.yaml"
    quebrado.write_text("- lista\n", encoding="utf-8")
    with pytest.raises(PoliticaInvalida) as exc2:
        carregar_politica_janela(quebrado)
    assert exc2.value.code == "POLICY_MALFORMED"
    dados = carregar(JANELA_YAML)
    dados["ancoras"]["signed_at"]["duracao_iso"] = "7"
    with pytest.raises(PoliticaInvalida) as exc3:
        carregar_politica_janela(gravar(tmp_path, dados, "d.yaml"))
    assert exc3.value.code == "DURATION_MALFORMED"
