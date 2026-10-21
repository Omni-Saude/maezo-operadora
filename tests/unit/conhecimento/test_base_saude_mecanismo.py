"""O MECANISMO da base de conhecimento de saude (ADR-0064, DL-0118): schema, carregador, lista fechada de
fontes, termos proibidos, busca deterministica, classificacao restrita e montagem das respostas.

Usa a base de FIXTURE (`tests/fixtures/base_saude`, 3 arquivos minimos) e bases sinteticas montadas em
`tmp_path` — nunca a base real (essa e' conferida por `tests/unit/agents/test_helena_base_saude_conteudo.py`).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.conhecimento import base_saude as b

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "base_saude"
HOJE = date(2026, 10, 17)


def _base() -> b.BaseSaude:
    return b.carregar_base(FIXTURE)


def _assunto(**sobre: Any) -> dict[str, Any]:
    dados: dict[str, Any] = {
        "id": "con-teste",
        "categoria": "condicao",
        "titulo": "Condicao de teste",
        "sinonimos": ["condicao de teste"],
        "texto": "Texto sintetico de teste, curto e sem nada proibido nele.",
        "tipo_texto": "citacao_literal",
        "fontes": [
            {
                "nome": "Saude de A a Z (Ministerio da Saude)",
                "url": "https://www.gov.br/saude/pt-br/assuntos/saude-de-a-a-z/t/teste",
                "licenca": "CC BY-ND 3.0",
                "coletado_em": "2026-10-17",
            }
        ],
        "revisao": {
            "status": "rascunho_dono_aprovado_dev",
            "autor": "teste",
            "aprovado_por": "dono (17/10/2026, dev)",
            "revisao_medica": "pendente",
            "revisar_ate": "2027-04-17",
        },
    }
    dados.update(sobre)
    return dados


def _escrever(
    raiz: Path, dados: dict[str, Any], *, categoria: str | None = None, nome: str | None = None
) -> None:
    pasta = raiz / (categoria or dados["categoria"])
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / f"{nome or dados['id']}.yaml").write_text(
        yaml.safe_dump(dados, allow_unicode=True), encoding="utf-8"
    )


# --- carregador e schema --------------------------------------------------------------------------
def test_a_fixture_carrega_e_indexa_por_id_categoria_e_sinonimo() -> None:
    base = _base()
    assert set(base.assuntos) == {"med-dipirona", "exa-psa", "sin-dor-de-cabeca"}
    assert base.por_categoria["sintoma"] == ("sin-dor-de-cabeca",)
    assert base.por_sinonimo["novalgina"] == "med-dipirona"
    assert base.por_sinonimo["dor de cabeca"] == "sin-dor-de-cabeca"  # titulo normalizado (sem acento)
    for assunto in base.assuntos.values():
        assert b.problemas_de_politica(assunto, hoje=HOJE) == []


def test_diretorio_vazio_e_base_vazia_valida(tmp_path: Path) -> None:
    (tmp_path / "FONTES.md").write_text("lista", encoding="utf-8")
    assert len(b.carregar_base(tmp_path)) == 0


def test_diretorio_ausente_falha_fechado(tmp_path: Path) -> None:
    with pytest.raises(b.BaseSaudeInvalidaError):
        b.carregar_base(tmp_path / "nao-existe")


@pytest.mark.parametrize(
    ("sobre", "trecho"),
    [
        ({"id": "esp-teste"}, "prefixo"),
        ({"id": "Con_Teste"}, "kebab"),
        ({"texto": "x" * 701}, "texto"),
        ({"tipo_texto": "parafrase"}, "tipo_texto"),
        ({"fontes": []}, "fontes"),
        ({"sinonimos": ["Maiuscula"]}, "sinonimo"),
        ({"perguntas_alerta": ["Tem febre?"]}, "so' existem"),
        (
            {
                "revisao": {
                    "status": "aprovado",
                    "autor": "x",
                    "aprovado_por": "x",
                    "revisao_medica": "pendente",
                    "revisar_ate": "2027-01-01",
                }
            },
            "revisao",
        ),
        ({"campo_extra": 1}, "campo_extra"),
    ],
)
def test_schema_invalido_reprova_a_base_inteira(tmp_path: Path, sobre: dict[str, Any], trecho: str) -> None:
    _escrever(tmp_path, _assunto(**sobre), categoria="condicao", nome=str(sobre.get("id", "con-teste")))
    with pytest.raises(b.BaseSaudeInvalidaError) as erro:
        b.carregar_base(tmp_path)
    assert erro.value.erros


def test_fonte_sem_url_licenca_ou_data_reprova(tmp_path: Path) -> None:
    for campo in ("url", "licenca", "coletado_em"):
        raiz = tmp_path / campo
        fonte = dict(_assunto()["fontes"][0])
        del fonte[campo]
        _escrever(raiz, _assunto(fontes=[fonte]))
        with pytest.raises(b.BaseSaudeInvalidaError):
            b.carregar_base(raiz)


def test_sintoma_exige_perguntas_sinais_e_populacao(tmp_path: Path) -> None:
    base_sin = _assunto(id="sin-teste", categoria="sintoma", sinonimos=["sintoma de teste"])
    _escrever(tmp_path, base_sin)
    with pytest.raises(b.BaseSaudeInvalidaError):
        b.carregar_base(tmp_path)
    ok = tmp_path / "ok"
    _escrever(
        ok,
        {
            **base_sin,
            "perguntas_alerta": ["Tem febre alta?"],
            "sinais_alarme": ["febre alta"],
            "populacao": "adulto",
        },
    )
    assert "sin-teste" in b.carregar_base(ok).assuntos
    quatro = tmp_path / "quatro"
    _escrever(
        quatro,
        {
            **base_sin,
            "perguntas_alerta": ["A?", "B?", "C?", "D?"],
            "sinais_alarme": ["x"],
            "populacao": "adulto",
        },
    )
    with pytest.raises(b.BaseSaudeInvalidaError):
        b.carregar_base(quatro)


def test_arquivo_fora_do_lugar_ou_com_nome_diferente_do_id_reprova(tmp_path: Path) -> None:
    _escrever(tmp_path, _assunto(), categoria="exame")
    with pytest.raises(b.BaseSaudeInvalidaError):
        b.carregar_base(tmp_path)
    outro = tmp_path / "outro"
    _escrever(outro, _assunto(), nome="con-outro-nome")
    with pytest.raises(b.BaseSaudeInvalidaError):
        b.carregar_base(outro)


def test_sinonimo_duplicado_entre_ids_reprova(tmp_path: Path) -> None:
    _escrever(tmp_path, _assunto())
    _escrever(tmp_path, _assunto(id="con-teste-dois", titulo="Outra", sinonimos=["condição de teste"]))
    with pytest.raises(b.BaseSaudeInvalidaError) as erro:
        b.carregar_base(tmp_path)
    assert any("sinonimo" in e for e in erro.value.erros)


# --- politica: lista fechada de fontes, licenca, termos proibidos, revisao ------------------------
@pytest.mark.parametrize(
    ("nome", "url", "classe"),
    [
        (
            "Saude de A a Z",
            "https://www.gov.br/saude/pt-br/assuntos/saude-de-a-a-z/d/dengue",
            b.CLASSE_RESPONDER,
        ),
        ("INCA", "https://www.gov.br/inca/pt-br/assuntos/cancer/tipos/prostata", b.CLASSE_RESPONDER),
        ("Calendario", "https://www.gov.br/saude/pt-br/vacinacao/calendario", b.CLASSE_RESPONDER),
        (
            "ANS RN 566",
            "https://www.ans.gov.br/component/legislacao/?view=legislacao&id=4172",
            b.CLASSE_RESPONDER,
        ),
        ("Rol ANS", "https://www.gov.br/ans/pt-br/acesso-a-informacao/rol", b.CLASSE_RESPONDER),
        ("Bula", "https://consultas.anvisa.gov.br/#/bulario/", b.CLASSE_LINK),
        ("TUSS", "https://www.gov.br/ans/pt-br/assuntos/prestadores/tuss", b.CLASSE_VOCABULARIO),
        ("CID-11", "https://icd.who.int/browse/2024-01/mms/pt", b.CLASSE_VOCABULARIO),
        ("SIGTAP", "https://sigtap.datasus.gov.br/tabela-unificada", b.CLASSE_VOCABULARIO),
        ("BVS Dicas", "https://bvsms.saude.gov.br/dicas-em-saude/", b.CLASSE_PROIBIDA),
        ("Caderno de Atencao Basica 28", "https://www.gov.br/saude/pt-br/x", b.CLASSE_PROIBIDA),
        (
            "Cartilha INCA",
            "https://www.inca.gov.br/sites/ufu.sti.inca.local/files/media/document/x.pdf",
            b.CLASSE_PROIBIDA,
        ),
        ("INCA PDF", "https://www.gov.br/inca/pt-br/arquivos/x.pdf", b.CLASSE_PROIBIDA),
        ("OPAS", "https://www.paho.org/pt/topicos/dengue", b.CLASSE_PROIBIDA),
        ("OMS", "https://www.who.int/news-room/fact-sheets", b.CLASSE_PROIBIDA),
        ("Fiocruz", "https://portal.fiocruz.br/noticia/x", b.CLASSE_PROIBIDA),
        ("Manual MSD", "https://www.msdmanuals.com/pt/casa/x", b.CLASSE_PROIBIDA),
        ("DeCS", "https://decs.bvsalud.org/ths/resource", b.CLASSE_PROIBIDA),
        ("Um blog", "https://www.exemplo.com.br/saude", b.CLASSE_FORA_DA_LISTA),
        (
            "Resolucao CFM 2.380/2024",
            "https://sistemas.cfm.org.br/normas/arquivos/resolucoes/BR/2024/2380_2024.pdf",
            b.CLASSE_ATO_OFICIAL,
        ),
        ("Lei 8.234/1991", "https://www.planalto.gov.br/ccivil_03/leis/l8234.htm", b.CLASSE_ATO_OFICIAL),
        (
            "Tabela de alarme",
            "https://github.com/Omni-Saude/maezo-operadora/blob/main/spec/processes/dmn/triage_redflag_adult.dmn",
            b.CLASSE_INTERNA,
        ),
        ("Outro repo", "https://github.com/qualquer/coisa", b.CLASSE_FORA_DA_LISTA),
    ],
)
def test_a_lista_fechada_de_fontes(nome: str, url: str, classe: str) -> None:
    assert b.classificar_fonte(nome, url).classe == classe


def _problemas(**sobre: Any) -> list[str]:
    return b.problemas_de_politica(b.Assunto.model_validate(_assunto(**sobre)), hoje=HOJE)


def test_politica_reprova_revisao_vencida() -> None:
    revisao = {**_assunto()["revisao"], "revisar_ate": "2026-10-16"}
    assert any("revisar_ate vencido" in p for p in _problemas(revisao=revisao))


def test_politica_reprova_fonte_proibida_e_fonte_fora_da_lista() -> None:
    proibida = {**_assunto()["fontes"][0], "nome": "Manual MSD", "url": "https://www.msdmanuals.com/pt/x"}
    assert any("NAO USAR" in p for p in _problemas(fontes=[proibida]))
    fora = {**_assunto()["fontes"][0], "url": "https://www.exemplo.com.br/x"}
    assert any("fora da lista" in p for p in _problemas(fontes=[fora]))


def test_citacao_literal_so_de_cc_by_nd_ou_ato_oficial_e_nunca_da_bula() -> None:
    licenca_errada = {**_assunto()["fontes"][0], "licenca": "todos os direitos reservados"}
    assert any("licenca" in p for p in _problemas(fontes=[licenca_errada]))
    bula = {
        "nome": "Bula",
        "url": "https://consultas.anvisa.gov.br/#/bulario/",
        "licenca": "link",
        "coletado_em": "2026-10-17",
    }
    assert any("bula" in p.lower() for p in _problemas(fontes=[bula]))
    assert _problemas(fontes=[bula], tipo_texto="proprio") == []


def test_vocabulario_nunca_e_a_fonte_que_responde() -> None:
    tuss = {
        "nome": "TUSS",
        "url": "https://www.gov.br/ans/pt-br/assuntos/prestadores/tuss",
        "licenca": "ato oficial",
        "coletado_em": "2026-10-17",
    }
    assert any("nenhuma fonte de apoio" in p for p in _problemas(fontes=[tuss], tipo_texto="proprio"))


def test_ato_oficial_e_tabela_interna_so_apoiam_texto_proprio() -> None:
    cfm = {
        "nome": "Resolucao CFM 2.380/2024",
        "url": "https://sistemas.cfm.org.br/normas/arquivos/resolucoes/BR/2024/2380_2024.pdf",
        "licenca": "ato oficial",
        "coletado_em": "2026-10-17",
    }
    assert _problemas(fontes=[cfm], tipo_texto="proprio") == []
    assert any("so' apoio" in p for p in _problemas(fontes=[cfm]))
    interna = {
        "nome": "Tabela de alarme do adulto",
        "url": "https://github.com/Omni-Saude/maezo-operadora/blob/main/spec/processes/dmn/x.dmn",
        "licenca": "texto proprio da operadora",
        "coletado_em": "2026-10-17",
    }
    assert any("nenhuma fonte de apoio" in p for p in _problemas(fontes=[interna], tipo_texto="proprio"))
    sintoma = b.Assunto.model_validate(
        _assunto(
            id="sin-teste",
            categoria="sintoma",
            tipo_texto="proprio",
            fontes=[interna],
            perguntas_alerta=["Tem febre alta?"],
            sinais_alarme=["febre alta"],
            populacao="adulto",
        )
    )
    assert b.problemas_de_politica(sintoma, hoje=HOJE) == []
    assert b.linha_de_credito(sintoma) == "Texto da operadora."


def test_o_credito_e_curto_mesmo_com_nome_longo_de_fonte() -> None:
    longa = {**_assunto()["fontes"][0], "nome": "Saude de A a Z " + "palavra " * 15}
    assunto = b.Assunto.model_validate(_assunto(fontes=[longa]))
    assert b.linha_de_credito(assunto) == "Fonte: fonte oficial do Ministério da Saúde, do INCA ou da ANS."


@pytest.mark.parametrize(
    "texto",
    [
        "Use a dose indicada pelo medico para o tratamento.",
        "O comprimido de 500 mg alivia a dor nesses casos.",
        "Tome um comprimido quando tiver dor e febre.",
        "Voce pode tomar este remedio com agua sem problema.",
        "Na maioria das vezes isso nao e grave para ninguem.",
        "Sentir isso de vez em quando e normal na idade.",
        "Pelos sinais descritos voce tem uma infeccao comum.",
        "Este exame e coberto pelo plano em todas as situacoes.",
        "Use de 8 em 8 horas conforme a orientacao recebida.",
    ],
)
def test_termos_proibidos_no_texto_reprovam(texto: str) -> None:
    assert b.termos_proibidos_no_texto(texto)
    assert any("termo proibido" in p for p in _problemas(texto=texto))


def test_condicional_se_voce_tem_nao_e_diagnostico() -> None:
    assert b.termos_proibidos_no_texto("Se voce tem duvida sobre o exame, converse com o seu medico.") == []


def test_base_aplicavel_tira_o_assunto_vencido(tmp_path: Path) -> None:
    _escrever(tmp_path, _assunto())
    _escrever(
        tmp_path,
        _assunto(
            id="con-vencido",
            titulo="Vencido",
            sinonimos=["vencido"],
            revisao={**_assunto()["revisao"], "revisar_ate": "2026-01-01"},
        ),
    )
    base = b.carregar_base(tmp_path)
    aplicavel = b.base_aplicavel(base, hoje=HOJE)
    assert set(aplicavel.assuntos) == {"con-teste"}
    assert "vencido" not in aplicavel.por_sinonimo


# --- busca deterministica -------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("texto", "achado"),
    [
        ("Pra que serve dipirona?", "med-dipirona"),
        ("Para que serve a dipirona sódica?", "med-dipirona"),
        ("O que é PSA?", "exa-psa"),
        ("o que e psa", "exa-psa"),
        ("Meu PSA deu fora dos parâmetros, o que me sugere?", "exa-psa"),
        ("o que é dipiroma?", "med-dipirona"),  # erro leve de digitacao
        ("o que é enxaqueca?", "sin-dor-de-cabeca"),
    ],
)
def test_busca_deterministica(texto: str, achado: str) -> None:
    assert b.buscar(_base(), texto).achado == achado


@pytest.mark.parametrize("texto", ["o que é xyzabc?", "o que é PSB?", "o que e um pas?", ""])
def test_busca_nao_inventa_assunto(texto: str) -> None:
    busca = b.buscar(_base(), texto)
    assert busca.achado is None
    assert busca.candidatos == ()


def test_busca_ambigua_devolve_candidatos_e_restringe_por_categoria(tmp_path: Path) -> None:
    _escrever(tmp_path, _assunto(id="con-cistite", titulo="Cistite", sinonimos=["cistite"]))
    _escrever(tmp_path, _assunto(id="con-cistete", titulo="Cistete", sinonimos=["cistete"]))
    base = b.carregar_base(tmp_path)
    busca = b.buscar(base, "o que e cistote?")
    assert busca.achado is None
    assert set(busca.candidatos) == {"con-cistite", "con-cistete"}
    assert b.buscar(_base(), "estou com dor de cabeca", categorias=("sintoma",)).achado == "sin-dor-de-cabeca"
    assert b.buscar(_base(), "dipirona", categorias=("sintoma",)).achado is None


def test_classificacao_pelo_modelo_so_aceita_id_da_lista_fechada() -> None:
    candidatos = ("con-asma", "con-asmo")
    assert b.ler_classificacao('{"id": "con-asma"}', candidatos) == "con-asma"
    assert b.ler_classificacao('texto antes {"id": "con-asmo"} depois', candidatos) == "con-asmo"
    assert b.ler_classificacao('{"id": "nenhum"}', candidatos) is None
    assert b.ler_classificacao('{"id": "med-dipirona"}', candidatos) is None
    assert b.ler_classificacao("A asma e uma doenca...", candidatos) is None
    assert b.ler_classificacao("{quebrado", candidatos) is None
    prompt = b.prompt_de_classificacao(_base(), ("exa-psa",), "<mensagem>")
    assert '"exa-psa": PSA' in prompt and "Nao responda a pergunta" in prompt


# --- as respostas ---------------------------------------------------------------------------------
def test_resposta_informativa_tem_texto_credito_link_e_aviso() -> None:
    dipirona = _base().get("med-dipirona")
    assert dipirona is not None
    resposta = b.resposta_informativa(dipirona)
    assert dipirona.texto in resposta
    assert "Texto da operadora com base em Bula do paciente (ANVISA)." in resposta
    assert "Para ler mais: https://consultas.anvisa.gov.br/#/bulario/" in resposta
    assert resposta.endswith(b.AVISO_PADRAO)
    psa = _base().get("exa-psa")
    assert psa is not None
    assert "Fonte: INCA — Instituto Nacional de Câncer." in b.resposta_informativa(psa)


def test_resposta_de_resultado_pessoal_leva_ao_medico() -> None:
    psa = _base().get("exa-psa")
    assert psa is not None
    resposta = b.resposta_de_resultado_pessoal(psa)
    assert psa.texto in resposta and b.FRASE_LEVE_AO_MEDICO in resposta and b.AVISO_PADRAO in resposta
    assert b.e_texto_da_base(resposta, _base())
    assert not b.e_texto_da_base("Qualquer texto do modelo.", _base())
    assert b.e_texto_da_base(b.RESPOSTA_NAO_ENCONTRADO, _base())


def test_alarme_de_sintoma_da_base() -> None:
    base = _base()
    assert b.alarme_no_texto(base, "estou com dor de cabeça, a pior dor da vida")
    assert b.alarme_no_texto(base, "dor de cabeça e rigidez no pescoço")
    assert not b.alarme_no_texto(base, "estou com muita dor de cabeça desde ontem")
    # A lista e' a do arquivo DESTA queixa: sem arquivo citado, nenhum alarme da base (a tabela decide).
    assert [a.id for a in b.sintomas_citados(base, "minha enxaqueca voltou")] == ["sin-dor-de-cabeca"]
    assert not b.alarme_no_texto(base, "estou com dor nas costas e boca torta")


# --- Fase 5: rascunho escrito por modelo e referencia sem link ----------------------------------------
_REVISAO_FASE5: dict[str, Any] = {
    "status": "rascunho_pendente_revisao_medica",
    "autor": "Claude (assistente de IA)",
    "aprovado_por": "dono (18/10/2026, fase 5, dev)",
    "revisao_medica": "pendente",
    "revisar_ate": "2027-04-17",
    "escrito_por": "claude-sonnet-5.5 (rascunho)",
}
_SEM_LINK: dict[str, Any] = {
    "nome": "Sem pagina leiga oficial na lista fechada (FONTES.md) - conferir na revisao medica",
    "url": None,
    "licenca": "texto proprio da operadora",
    "coletado_em": "2026-10-17",
}


def test_rascunho_da_fase5_carrega_e_responde_como_os_outros(tmp_path: Path) -> None:
    _escrever(tmp_path, _assunto(tipo_texto="proprio", revisao=_REVISAO_FASE5))
    base = b.carregar_base(tmp_path)
    assunto = base.assuntos["con-teste"]
    assert b.problemas_de_politica(assunto, hoje=HOJE) == []
    assert b.base_aplicavel(base, hoje=HOJE) is base
    assert b.rascunhos_de_modelo(base) == (assunto,)
    assert b.pendentes_de_revisao_medica(base) == (assunto,)
    assert assunto.revisao.escrito_por == "claude-sonnet-5.5 (rascunho)"


@pytest.mark.parametrize(
    "troca",
    [
        {"escrito_por": None},
        {"revisao_medica": "aprovada"},
        {"aprovado_por": "dono (17/10/2026, dev)"},
    ],
)
def test_rascunho_da_fase5_sem_marca_completa_reprova_a_base(tmp_path: Path, troca: dict[str, Any]) -> None:
    _escrever(tmp_path, _assunto(tipo_texto="proprio", revisao={**_REVISAO_FASE5, **troca}))
    with pytest.raises(b.BaseSaudeInvalidaError):
        b.carregar_base(tmp_path)


def test_referencia_sem_link_so_vale_no_rascunho_de_texto_proprio(tmp_path: Path) -> None:
    rascunho = b.Assunto.model_validate(
        _assunto(tipo_texto="proprio", fontes=[_SEM_LINK], revisao=_REVISAO_FASE5)
    )
    assert b.problemas_de_politica(rascunho, hoje=HOJE) == []
    assert b.classificar_fonte(_SEM_LINK["nome"], None).classe == b.CLASSE_SEM_LINK
    assert b.fonte_creditada(rascunho) is None
    assert b.linha_de_credito(rascunho) == "Texto da operadora."
    # Fora do rascunho da fase 5 (ou em citacao literal), fonte sem url reprova.
    antigo = b.Assunto.model_validate(_assunto(tipo_texto="proprio", fontes=[_SEM_LINK]))
    assert any("fonte sem url" in p for p in b.problemas_de_politica(antigo, hoje=HOJE))
    literal = b.Assunto.model_validate(
        _assunto(tipo_texto="citacao_literal", fontes=[_SEM_LINK], revisao=_REVISAO_FASE5)
    )
    assert b.problemas_de_politica(literal, hoje=HOJE) != []


def test_url_presente_continua_exigindo_https() -> None:
    with pytest.raises(ValueError, match="https"):
        b.Fonte.model_validate({**_SEM_LINK, "url": "http://www.gov.br/saude/x"})
