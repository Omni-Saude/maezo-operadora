"""Protocolos clinicos como DADOS (decisao do dono, 25/10/2026): o validador ESTRITO recusa cada regra
violada, um arquivo ruim e' excluido sem derrubar os outros, e todos os arquivos reais carregam."""

from __future__ import annotations

import copy
import functools
from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.conhecimento import protocolos as p

_RAIZ = Path(__file__).resolve().parents[3]
_PROTOCOLOS = _RAIZ / "spec" / "agents" / "helena" / "pacote" / "protocolos"
_ESP_IDS = frozenset(
    x.stem for x in (_RAIZ / "spec" / "conhecimento" / "saude" / "especialidade").glob("*.yaml")
)

#: As exclusoes CONHECIDAS do mapa (especialidade sem nome leigo em `textos.yaml`): dados a completar pelo
#: dono/medico, nao erro de digitacao. A lista e' exata: entrada nova invalida reprova o teste.
_EXCLUIDOS_CONHECIDOS = {
    "mapa/esp-regra-crianca",  # "Cirurgia pediatrica" sem nome leigo
    "mapa/esp-queixa-dor-cronica",  # "Anestesiologia" sem nome leigo
}


def _valido() -> dict[str, Any]:
    return {
        "id": "tri-adulto-exemplo",
        "tema": "triagem_adultos",
        "titulo": "Exemplo de queixa",
        "publico": "adulto",
        "como_a_pessoa_fala": ["estou com um exemplo", "tenho exemplo"],
        "perguntas_de_alerta": [
            {
                "pergunta": "Você está com muita falta de ar agora?",
                "sinal": "falta_de_ar",
                "destino_se_sim": "LIGAR_192_AGORA",
                "fonte": "Caderno de Atenção Básica 28",
            },
            {
                "pergunta": "Está com febre alta há vários dias?",
                "sinal": "febre",
                "destino_se_sim": "ATENDIMENTO_HOJE",
                "fonte": "Caderno de Atenção Básica 28",
            },
        ],
        "destino_se_nenhum_sinal": "CONSULTA_EM_BREVE",
        "especialista": {"primeiro": "Clínico geral", "depois": "Cardiologia", "fonte": "CFM"},
        "orientacao_final": "Procure uma consulta. Se piorar, procure um pronto-atendimento ou ligue 192.",
        "fontes": [{"nome": "Caderno de Atenção Básica nº 28", "secao": "§2"}],
        "lacuna": False,
        "revisao_medica": "pendente",
        "licenca_atencao": "pendente jurídico",
    }


def _validar(dados: dict[str, Any]) -> Any:
    return p.validar_protocolo(dados, tema=dados.get("tema", "triagem_adultos"), nome_do_arquivo=dados["id"])


def test_o_exemplo_valido_passa_como_rascunho() -> None:
    protocolo = _validar(_valido())
    assert isinstance(protocolo, p.Protocolo) and protocolo.rascunho


def _com(caminho: tuple[Any, ...], valor: Any) -> dict[str, Any]:
    dados = copy.deepcopy(_valido())
    alvo: Any = dados
    for chave in caminho[:-1]:
        alvo = alvo[chave]
    alvo[caminho[-1]] = valor
    return dados


@pytest.mark.parametrize(
    ("caminho", "valor"),
    [
        (("perguntas_de_alerta", 0, "destino_se_sim"), "IR_AO_POSTO"),
        (("perguntas_de_alerta", 0, "destino_se_sim"), "ligar_192_agora"),
        (("destino_se_nenhum_sinal",), "AGENDAR"),
        (("destino_se_nenhum_sinal",), None),
    ],
)
def test_destino_fora_do_conjunto_fechado_e_recusado(caminho: tuple[Any, ...], valor: Any) -> None:
    with pytest.raises(p.ProtocoloInvalidoError):
        _validar(_com(caminho, valor))


def test_mais_de_quatro_perguntas_e_recusado() -> None:
    dados = _valido()
    base = dados["perguntas_de_alerta"][1]
    dados["perguntas_de_alerta"] = [
        dados["perguntas_de_alerta"][0],
        *({**base, "sinal": f"s{i}"} for i in range(4)),
    ]
    with pytest.raises(p.ProtocoloInvalidoError, match="perguntas_de_alerta"):
        _validar(dados)


@pytest.mark.parametrize("campo", ["sinal", "destino_se_sim", "fonte"])
def test_pergunta_sem_sinal_destino_ou_fonte_e_recusada(campo: str) -> None:
    dados = _valido()
    del dados["perguntas_de_alerta"][0][campo]
    with pytest.raises(p.ProtocoloInvalidoError):
        _validar(dados)


def test_sem_pergunta_de_alerta_e_recusado() -> None:
    with pytest.raises(p.ProtocoloInvalidoError):
        _validar(_com(("perguntas_de_alerta",), []))


@pytest.mark.parametrize("publico", ["idoso", "crianca", "todas_as_idades", "Adulto"])
def test_publico_fora_do_conjunto_fechado_e_recusado(publico: str) -> None:
    with pytest.raises(p.ProtocoloInvalidoError):
        _validar(_com(("publico",), publico))


@pytest.mark.parametrize(
    ("caminho", "texto"),
    [
        (("orientacao_final",), "Veja mais em https://exemplo.org/pagina. Se piorar, ligue 192."),
        (("orientacao_final",), "Leia em www.saude.gov.br antes. Se piorar, ligue 192."),
        (("perguntas_de_alerta", 0, "pergunta"), "Você leu o site saude.gov.br sobre falta de ar?"),
        (("fontes", 0, "nome"), "Caderno 28 em https://bvsms.saude.gov.br/caderno.pdf"),
    ],
)
def test_link_e_recusado(caminho: tuple[Any, ...], texto: str) -> None:
    with pytest.raises(p.ProtocoloInvalidoError, match="(link|url)"):
        _validar(_com(caminho, texto))


@pytest.mark.parametrize(
    "texto",
    [
        "Tome 500 mg de dipirona e procure consulta. Se piorar, ligue 192.",
        "Pode usar 20 gotas do remédio. Se piorar, ligue 192.",
        "Tome 1 comprimido de 8 em 8 horas. Se piorar, ligue 192.",
    ],
)
def test_dose_e_recusada(texto: str) -> None:
    with pytest.raises(p.ProtocoloInvalidoError, match="dose"):
        _validar(_com(("orientacao_final",), texto))


@pytest.mark.parametrize(
    "texto",
    [
        "A consulta sai em até 7 dias úteis. Se piorar, ligue 192.",
        "Pelo prazo da ANS você é atendido logo. Se piorar, ligue 192.",
        "O plano tem 21 dias uteis para marcar. Se piorar, ligue 192.",
    ],
)
def test_prazo_da_ans_e_recusado(texto: str) -> None:
    with pytest.raises(p.ProtocoloInvalidoError, match="prazo_ans"):
        _validar(_com(("orientacao_final",), texto))


@pytest.mark.parametrize(
    "texto",
    [
        "Não tome dose dobrada nem mude o remédio. Se piorar, ligue 192.",
        "Se a pressão der 18 por 12, procure atendimento. Se piorar, ligue 192.",
        "Procure consulta nos próximos dias. Se piorar, ligue 192.",
    ],
)
def test_controle_texto_sem_dose_link_nem_prazo_passa(texto: str) -> None:
    _validar(_com(("orientacao_final",), texto))


@pytest.mark.parametrize("revisao", ["pendente", "aprovada"])
def test_revisao_medica_pendente_ou_aprovada_passa(revisao: str) -> None:
    assert _validar(_com(("revisao_medica",), revisao)).rascunho is (revisao == "pendente")


def test_revisao_medica_fora_do_vocabulario_e_recusada() -> None:
    with pytest.raises(p.ProtocoloInvalidoError):
        _validar(_com(("revisao_medica",), "talvez"))


@pytest.mark.parametrize(
    ("campo", "valor"),
    [("id", "Tri Adulto"), ("id", "gest-no-tema-errado"), ("tema", "cardiologia"), ("extra", "x")],
)
def test_id_tema_e_chave_desconhecida_sao_recusados(campo: str, valor: str) -> None:
    dados = _com((campo,), valor)
    with pytest.raises(p.ProtocoloInvalidoError):
        p.validar_protocolo(dados, tema="triagem_adultos", nome_do_arquivo=str(dados["id"]))


def test_nome_do_arquivo_diferente_do_id_e_recusado() -> None:
    with pytest.raises(p.ProtocoloInvalidoError, match="nome do arquivo"):
        p.validar_protocolo(_valido(), tema="triagem_adultos", nome_do_arquivo="outro")


def test_sinal_repetido_e_recusado() -> None:
    dados = _valido()
    dados["perguntas_de_alerta"][1]["sinal"] = dados["perguntas_de_alerta"][0]["sinal"]
    with pytest.raises(p.ProtocoloInvalidoError, match="sinal repetido"):
        _validar(dados)


def _escrever(raiz: Path, tema: str, dados: Any, nome: str | None = None) -> None:
    pasta = raiz / tema
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / f"{nome or dados['id']}.yaml").write_text(
        yaml.safe_dump(dados, allow_unicode=True), encoding="utf-8"
    )


def test_falha_fechada_por_arquivo_os_outros_carregam(tmp_path: Path) -> None:
    _escrever(tmp_path, "triagem_adultos", _valido())
    _escrever(tmp_path, "triagem_adultos", _com(("id",), "tri-adulto-ruim") | {"publico": "idoso"})
    _escrever(tmp_path, "triagem_adultos", {"id": "x"}, nome="tri-adulto-quebrado")
    (tmp_path / "triagem_adultos" / "tri-adulto-ilegivel.yaml").write_text("a: [", encoding="utf-8")
    catalogo = p.carregar(tmp_path)
    assert set(catalogo.protocolos) == {"tri-adulto-exemplo"}
    assert set(catalogo.excluidos) >= {
        "triagem_adultos/tri-adulto-ruim.yaml",
        "triagem_adultos/tri-adulto-quebrado.yaml",
        "triagem_adultos/tri-adulto-ilegivel.yaml",
    }
    # O motivo e' rotulo: nunca o texto do arquivo.
    assert all("falta de ar" not in m for m in catalogo.excluidos.values())


def test_id_duplicado_entre_temas_e_excluido(tmp_path: Path) -> None:
    _escrever(tmp_path, "triagem_adultos", _valido() | {"id": "uni-alerta-universal"})
    _escrever(
        tmp_path, "triagem_criancas", _valido() | {"id": "uni-alerta-universal", "tema": "triagem_criancas"}
    )
    catalogo = p.carregar(tmp_path)
    assert list(catalogo.protocolos) == ["uni-alerta-universal"]
    assert catalogo.excluidos["triagem_criancas/uni-alerta-universal.yaml"] == "id duplicado"


def test_diretorio_ausente_falha(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        p.carregar(tmp_path / "nao-existe")


# --- O mapa: especialidades conhecidas -----------------------------------------------------------------
def _textos() -> p.TextosDoEspecialista:
    return p.validar_textos(yaml.safe_load((_PROTOCOLOS / "especialista" / "textos.yaml").read_text("utf-8")))


def _entrada(**extra: Any) -> dict[str, Any]:
    return {
        "id": "esp-queixa-exemplo",
        "queixa": "dor de exemplo",
        "como_a_pessoa_fala": ["dor de exemplo"],
        "especialista_primeiro": {"nome_leigo": "Clínico geral", "esp_id": ["esp-clinica-medica"]},
        "especialista_depois": [{"cfm": "Neurologia", "esp_id": "esp-neurologia"}],
        "lacuna": False,
    } | extra


def test_entrada_do_mapa_valida_passa() -> None:
    assert p.validar_entrada_do_mapa(_entrada(), _textos(), _ESP_IDS).id == "esp-queixa-exemplo"


@pytest.mark.parametrize(
    "depois",
    [
        [{"cfm": "Neurologia quantica", "esp_id": "esp-neurologia"}],
        [{"cfm": "Neurologia", "esp_id": "esp-nao-existe"}],
        [{"esp_id": "esp-neurologia"}],
    ],
)
def test_entrada_do_mapa_com_especialidade_desconhecida_e_recusada(depois: list[Any]) -> None:
    with pytest.raises(p.ProtocoloInvalidoError):
        p.validar_entrada_do_mapa(_entrada(especialista_depois=depois), _textos(), _ESP_IDS)


@pytest.mark.parametrize(
    "extra",
    [
        {"queixa": "dor que o plano atende em 7 dias úteis"},
        {"como_a_pessoa_fala": ["vi no site www.exemplo.com"]},
        {"fonte": "https://exemplo.org"},
    ],
)
def test_entrada_do_mapa_com_link_ou_prazo_e_recusada(extra: dict[str, Any]) -> None:
    with pytest.raises(p.ProtocoloInvalidoError, match="regras de texto"):
        p.validar_entrada_do_mapa(_entrada(**extra), _textos(), _ESP_IDS)


# --- Os arquivos REAIS ----------------------------------------------------------------------------------
@functools.cache
def _real() -> p.Catalogo:
    return p.carregar(_PROTOCOLOS, esp_ids=_ESP_IDS)


def test_todos_os_protocolos_reais_carregam() -> None:
    catalogo = _real()
    arquivos = {
        f"{c.parent.name}/{c.name}" for c in _PROTOCOLOS.glob("*/*.yaml") if c.parent.name != "especialista"
    }
    assert len(arquivos) == 90
    invalidos = {k: v for k, v in catalogo.excluidos.items() if not k.startswith("mapa/")}
    assert invalidos == {}
    assert len(catalogo.protocolos) == 88 and len(catalogo.referencias) == 2
    assert catalogo.textos is not None
    assert set(k for k in catalogo.excluidos if k.startswith("mapa/")) == _EXCLUIDOS_CONHECIDOS
    assert len(catalogo.mapa) >= 85 and {"esp-regra-idoso", "esp-regra-gestante"} <= set(catalogo.regras)
    assert all(pr.rascunho for pr in catalogo.protocolos.values())
    assert catalogo.universal("adulto") is not None and catalogo.universal("crianca_0_2m") is not None


def test_protocolos_reais_tem_as_faixas_e_os_publicos() -> None:
    catalogo = _real()
    publicos = {pr.publico for pr in catalogo.protocolos.values()}
    assert publicos == set(p.PUBLICOS)
    assert "tri-adulto-dor-ao-urinar" in catalogo.protocolos


def test_catalogo_do_ambiente_respeita_a_chave_de_desligar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(p.CHAVE_DO_AMBIENTE, "0")
    assert p.catalogo_do_ambiente() is None
    monkeypatch.setenv(p.CHAVE_DO_AMBIENTE, "1")
    p._CACHE.clear()
    catalogo = p.catalogo_do_ambiente()
    assert catalogo is not None and len(catalogo.protocolos) == 88
