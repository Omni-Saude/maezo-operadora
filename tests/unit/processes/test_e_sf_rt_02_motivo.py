"""E-SF-RT-02 (RT-05 item 5): motivo de cancelamento em lista fechada, sem saúde, sem vínculo ao titular.

Variante executável (E35 = B): a pergunta é opcional e vem DEPOIS do protocolo; só códigos da lista;
texto livre recusado; a resposta só entra numa contagem agregada por código, que não guarda pessoa,
contrato, pedido nem protocolo, e nada é emitido (nenhuma métrica, evento ou gravação).
"""

from __future__ import annotations

import ast
import dataclasses
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_args

import pytest

from maezo.gateway.communications.whatsapp_templates import contem_dado_de_saude
from maezo.platform.saida_a_pedido import cancelamento
from maezo.platform.saida_a_pedido.cancelamento import (
    CODIGOS_MOTIVO,
    CodigoMotivoCancelamento,
    ContagemDeMotivos,
    Elemento,
    MotivoForaDaListaError,
    SubmissaoCancelamento,
    codigo_do_motivo,
    montar_tela_pedido,
    receber_pedido_cancelamento,
    rotulos_do_motivo,
    submissao_de_payload,
    verificar_lista_de_guarda,
)
from maezo.platform.saida_a_pedido.politica import carregar_politica_cancelamento

AGORA = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)

#: Os seis códigos do RT-05 item 5, nesta ordem (lista fechada; mudar exige decisão).
_ESPERADOS = (
    "valor_alto",
    "mudanca_de_cidade",
    "outro_vinculo",
    "rede_nao_atende",
    "atendimento_servico",
    "prefiro_nao_dizer",
)
#: Rótulos VERBATIM do RT-05 item 5 (CD-202), na mesma ordem dos códigos.
_ROTULOS_RT05 = (
    "o valor ficou alto",
    "mudei de cidade",
    "vou usar o plano da empresa/de outro vínculo",
    "a rede não atende onde eu preciso",
    "atendimento/serviço",
    "prefiro não dizer",
)
#: Os nomes do bloco RT-02; nenhum outro módulo de `src/` pode lê-los (CD-202: nenhum uso para retenção,
#: oferta, segmentação ou mensagem).
_NOMES_DO_MOTIVO = frozenset(
    {
        "CODIGOS_MOTIVO",
        "CodigoMotivoCancelamento",
        "ContagemDeMotivos",
        "codigo_do_motivo",
        "rotulos_do_motivo",
        "MotivoForaDaListaError",
    }
)
#: Termos de saúde a mais que a cerca da E-SF-13 (defesa em profundidade sobre códigos e rótulos).
_SAUDE = re.compile(
    r"(?i)sa[uú]de|doen[cç]|tratament|cirurg|exame|consulta|m[eé]dic|hospital|interna|gravid|gesta|"
    r"diagn|cid\b|cpt|car[eê]ncia|preexist|terapi|rem[eé]dio|sintoma|psic"
)
_PESSOA = re.compile(
    r"(?i)titular|benefici|contrato|pedido|protocolo|tenant|pessoa|cpf|matricula|_ref$|_id$|instante|data"
)


def _submissao() -> SubmissaoCancelamento:
    return submissao_de_payload(
        {
            "pedido_ref": "pedido-1",
            "contrato_ref": "contrato-opaco-1",
            "bloco_art15_incisos_apresentados": ["I", "II", "III", "IV", "V", "VI"],
            "bloco_art15_versao_politica": 1,
            "toques": ["toque_1_pedido", "toque_2_confirmacao"],
        }
    )


def test_a_lista_fechada_e_a_do_rt05() -> None:
    assert CODIGOS_MOTIVO == _ESPERADOS
    assert set(get_args(CodigoMotivoCancelamento)) == set(_ESPERADOS)
    politica = carregar_politica_cancelamento()
    assert [c for c, _ in rotulos_do_motivo(politica)] == list(_ESPERADOS)
    assert tuple(r for _, r in rotulos_do_motivo(politica)) == _ROTULOS_RT05
    assert all(rotulo.strip() for _, rotulo in rotulos_do_motivo(politica))


def test_motivo_nao_bloqueia_protocolo() -> None:
    politica = carregar_politica_cancelamento()
    tela = montar_tela_pedido(politica, janela=None)
    # A pergunta existe, é opcional e está só depois do protocolo; a lista de guarda não acusa nada.
    ate = [e for p in tela.passos_ate_protocolo for e in p.elementos]
    pos = [e for p in tela.passos_pos_protocolo for e in p.elementos]
    assert not [e for e in ate if e.tipo == "pergunta_motivo"]
    assert [e for e in pos if e.tipo == "pergunta_motivo"] == [
        Elemento(tipo="pergunta_motivo", obrigatorio=False)
    ]
    assert verificar_lista_de_guarda(tela, politica) == ()
    # O pedido é admitido (e o CANCEL-001 recebe as mesmas variáveis) sem qualquer resposta de motivo;
    # a submissão nem tem campo para ele.
    admitido = receber_pedido_cancelamento(_submissao(), politica, recebido_em=AGORA, tenant_id="t-1")
    assert not [k for k in admitido.variaveis_de_inicio_cancel_001 if "motivo" in k]
    assert "motivo" not in {f.name for f in dataclasses.fields(SubmissaoCancelamento)}
    # Pular é aceito e não conta nada.
    assert codigo_do_motivo(None) is None
    assert ContagemDeMotivos().somar(None) == ContagemDeMotivos()
    # Controle: a guarda acusa a pergunta obrigatória (a verificação acima não é vácua).
    obrigatoria = dataclasses.replace(
        tela,
        passos_pos_protocolo=(
            dataclasses.replace(
                tela.passos_pos_protocolo[0], elementos=(Elemento(tipo="pergunta_motivo", obrigatorio=True),)
            ),
        ),
    )
    assert "GUARD_REQUIRES_REASON" in verificar_lista_de_guarda(obrigatoria, politica)


def test_o_motivo_nao_entra_no_payload_do_pedido() -> None:
    with pytest.raises(cancelamento.PedidoCancelamentoRecusado) as exc:
        submissao_de_payload({**dataclasses.asdict(_submissao()), "motivo": "valor_alto"})
    assert exc.value.code == "unknown_field"


@pytest.mark.parametrize(
    "resposta",
    [
        "o valor ficou alto",  # o rótulo, não o código
        "VALOR_ALTO",
        "valor_alto ",
        "estou em tratamento e a rede não cobre",
        "outro",
        "",
        1,
        True,
        ["valor_alto"],
        {"codigo": "valor_alto"},
    ],
)
def test_motivo_texto_livre_recusado(resposta: Any) -> None:
    with pytest.raises(MotivoForaDaListaError) as exc:
        codigo_do_motivo(resposta)
    assert exc.value.code == "reason_not_in_closed_list"
    assert exc.value.args == ("reason_not_in_closed_list",)  # nunca ecoa o valor recebido
    with pytest.raises(MotivoForaDaListaError):
        ContagemDeMotivos().somar(resposta)


@pytest.mark.parametrize("codigo", _ESPERADOS)
def test_cada_codigo_da_lista_e_aceito(codigo: str) -> None:
    assert codigo_do_motivo(codigo) == codigo


def test_motivo_sem_codigo_saude() -> None:
    politica = carregar_politica_cancelamento()
    textos = [
        *CODIGOS_MOTIVO,
        *(rotulo for _, rotulo in rotulos_do_motivo(politica)),
        politica.textos["pergunta_motivo"],
        politica.textos["botao_pular_motivo"],
    ]
    for texto in textos:
        assert not contem_dado_de_saude(texto), texto
        assert not _SAUDE.search(texto.replace("_", " ")), texto
    # Controle: os dois detectores reconhecem um motivo de saúde.
    assert _SAUDE.search("estou em tratamento")
    assert contem_dado_de_saude("cobertura parcial temporaria")
    assert _SAUDE.search("cpt")


def test_contagem_e_agregada_sem_vinculo_ao_titular() -> None:
    campos = {f.name for f in dataclasses.fields(ContagemDeMotivos)}
    assert campos == {"por_codigo"}
    assert not [c for c in campos if _PESSOA.search(c)]
    contagem = (
        ContagemDeMotivos().somar("valor_alto").somar("valor_alto").somar("mudanca_de_cidade").somar(None)
    )
    assert contagem.total("valor_alto") == 2
    assert contagem.total("mudanca_de_cidade") == 1
    assert sum(n for _, n in contagem.por_codigo) == 3
    assert dict(contagem.por_codigo).keys() == set(_ESPERADOS)
    assert dataclasses.fields(ContagemDeMotivos) and ContagemDeMotivos.__dataclass_params__.frozen


def test_nada_do_motivo_e_emitido_nem_gravado() -> None:
    """O bloco RT-02 é puro: sem métrica, evento, banco, relógio ou envio."""
    fonte = Path(cancelamento.__file__).read_text(encoding="utf-8")
    bloco = fonte[fonte.index("# E-SF-RT-02 (RT-05 item 5)") :]
    arvore = ast.parse(bloco)
    nomes = {n.id for n in ast.walk(arvore) if isinstance(n, ast.Name)}
    atributos = {n.attr for n in ast.walk(arvore) if isinstance(n, ast.Attribute)}
    proibidos = {
        "Counter",
        "Gauge",
        "metrics",
        "publish",
        "emit",
        "execute",
        "commit",
        "insert",
        "datetime",
        "now",
    }
    assert not (nomes | atributos) & proibidos
    assert not [
        n for n in ast.walk(ast.parse(fonte)) if isinstance(n, ast.Import | ast.ImportFrom) and _emissor(n)
    ]
    # Controle: o detector reconhece um import de emissor nas duas formas.
    for codigo in ("from maezo.runtime import metrics", "import prometheus_client"):
        importado = ast.parse(codigo).body[0]
        assert isinstance(importado, ast.Import | ast.ImportFrom)
        assert _emissor(importado), codigo


def _emissor(no: ast.Import | ast.ImportFrom) -> bool:
    if isinstance(no, ast.Import):
        modulos = [a.name for a in no.names]
    else:
        modulos = [no.module or "", *(f"{no.module}.{a.name}" for a in no.names)]
    return any(re.search(r"metrics|prometheus|kafka|events|sqlalchemy|psycopg|httpx", m) for m in modulos)


def test_nenhum_outro_modulo_de_src_le_o_motivo() -> None:
    """O motivo não alimenta retenção, oferta, segmentação nem mensagem: fora do próprio módulo, nenhum
    arquivo de `src/` importa ou cita os nomes do bloco RT-02."""
    raiz = Path(cancelamento.__file__).resolve().parents[3]
    proprio = Path(cancelamento.__file__).resolve()
    leitores = []
    for path in sorted((raiz / "maezo").rglob("*.py")):
        if path.resolve() == proprio:
            continue
        arvore = ast.parse(path.read_text(encoding="utf-8"))
        nomes = {n.id for n in ast.walk(arvore) if isinstance(n, ast.Name)}
        nomes |= {n.attr for n in ast.walk(arvore) if isinstance(n, ast.Attribute)}
        nomes |= {a.name for n in ast.walk(arvore) if isinstance(n, ast.ImportFrom) for a in n.names}
        if nomes & _NOMES_DO_MOTIVO:
            leitores.append(path.relative_to(raiz).as_posix())
    assert not leitores
    # Controle: o próprio módulo usa os nomes (a varredura não é vácua).
    proprios = {
        n.id for n in ast.walk(ast.parse(proprio.read_text(encoding="utf-8"))) if isinstance(n, ast.Name)
    }
    assert proprios & _NOMES_DO_MOTIVO


class _StrEnganosa(str):
    def __eq__(self, outro: object) -> bool:
        return True

    __hash__ = str.__hash__


def test_subclasse_de_str_nao_passa_e_o_codigo_e_canonico() -> None:
    with pytest.raises(MotivoForaDaListaError):
        codigo_do_motivo(_StrEnganosa("meu texto livre"))
    devolvido = codigo_do_motivo("valor_alto")
    assert type(devolvido) is str and devolvido is CODIGOS_MOTIVO[0]


@pytest.mark.parametrize(
    "por_codigo",
    [
        (("cpf-123", 7),),
        tuple((c, -1) for c in _ESPERADOS),
        tuple((c, True) for c in _ESPERADOS),
        tuple((c, 0) for c in reversed(_ESPERADOS)),
    ],
)
def test_contagem_so_aceita_a_lista_fechada_com_inteiros(por_codigo: Any) -> None:
    with pytest.raises(MotivoForaDaListaError):
        ContagemDeMotivos(por_codigo=por_codigo)
