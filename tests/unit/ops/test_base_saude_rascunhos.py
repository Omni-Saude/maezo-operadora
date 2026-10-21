"""Os rascunhos da base de saude da fase 5 (`scripts/base_saude_rascunhos/`) sem rede: a lista de assuntos,
as fontes (lista fechada), a checagem que aceita/reprova um rascunho e a montagem dos YAML."""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

from scripts.base_saude_rascunhos import comum, gerar, montar

from maezo.conhecimento import base_saude as b

_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "base_saude"
_HOJE = date(2026, 10, 17)


def test_a_lista_de_assuntos_e_valida_e_nunca_tem_sintoma() -> None:
    assuntos = comum.carregar_assuntos()
    assert 60 <= len(assuntos) <= 110
    assert len({a.id for a in assuntos}) == len(assuntos)
    for a in assuntos:
        assert not a.id.startswith("sin-"), "rascunho de modelo nunca cria sintoma (alarme so' das tabelas)"
        assert a.id.startswith(b.CATEGORIAS[a.categoria])
        assert a.fontes, a.id


def test_toda_fonte_com_url_esta_na_lista_fechada_e_a_sem_link_nao_tem_url() -> None:
    for a in comum.carregar_assuntos():
        for f in a.fontes:
            classe = b.classificar_fonte(f.nome, f.url).classe
            if f.url is None:
                assert classe == b.CLASSE_SEM_LINK and f.nome == comum.NOME_SEM_LINK
            else:
                assert classe == b.CLASSE_RESPONDER, (a.id, f.url, classe)
        if a.categoria == "vacina":
            assert any(f.url == comum.URL_CALENDARIO for f in a.fontes), a.id


def test_a_checagem_aceita_texto_informativo_e_reprova_conduta() -> None:
    assunto = next(a for a in comum.carregar_assuntos() if a.id == "con-gota")
    bom = (
        "A gota é uma doença das articulações causada pelo acúmulo de ácido úrico. Costuma causar dor e "
        "inchaço, muitas vezes no dedão do pé. O tratamento é definido pelo médico e pode envolver mudança "
        "de hábitos e remédios. Quem costuma cuidar é o reumatologista."
    )
    assert gerar.checar(bom, assunto) == []
    ruim = (
        "Você deve tomar o remédio todo ano. Em caso de dor muito forte, vá ao pronto-socorro ou ligue 192."
    )
    regras = gerar.checar(ruim, assunto)
    for esperada in ("alarme_ou_pronto_socorro", "dor_muito_forte", "frequencia", "voce_deve"):
        assert esperada in regras
    assert any(r.startswith("base:") for r in regras)
    vacina = next(a for a in comum.carregar_assuntos() if a.id == "vac-hpv")
    assert "idade_ou_esquema_de_vacina" in gerar.checar("A vacina é aplicada aos 9 anos.", vacina)


def test_o_trecho_de_pagina_geral_e_recortado_pelos_termos() -> None:
    assunto = next(a for a in comum.carregar_assuntos() if a.id == "con-climaterio-e-menopausa")
    pagina = {assunto.pagina: {"texto": "Linha sobre outra coisa\nO climatério é a transição\nFim\nNada"}}
    trecho = gerar.trecho_da_pagina(assunto, pagina)
    assert "climatério" in trecho and "Nada" not in trecho


def _registro(aid: str, texto: str, sinonimos: list[str], desfecho: str = "primeira") -> dict[str, object]:
    a = next(x for x in comum.carregar_assuntos() if x.id == aid)
    return {
        "id": a.id,
        "titulo": a.titulo,
        "categoria": a.categoria,
        "fontes": [{"nome": f.nome, "url": f.url} for f in a.fontes],
        "sinonimos": sinonimos,
        "texto_final": texto if desfecho != "reprovado" else "",
        "desfecho": desfecho,
        "violacoes1": [],
        "violacoes2": [],
    }


def test_montar_escreve_yaml_valido_tira_sinonimo_ocupado_e_pula_reprovado(tmp_path: Path) -> None:
    raiz = tmp_path / "saude"
    shutil.copytree(_FIXTURE, raiz)
    rascunhos = tmp_path / "rascunhos.jsonl"
    linhas = [
        _registro(
            "con-gota",
            "A gota é uma doença das articulações ligada ao ácido úrico. "
            "O tratamento é definido pelo médico.",
            ["gota", "artrite gotosa", "novalgina", "dor"],
        ),
        _registro("con-artrose", "", [], desfecho="reprovado"),
    ]
    rascunhos.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in linhas), encoding="utf-8")
    paginas = tmp_path / "paginas.json"
    paginas.write_text("{}", encoding="utf-8")
    relatorio = tmp_path / "montagem.json"
    assert (
        montar.main(
            [
                "--rascunhos",
                str(rascunhos),
                "--paginas",
                str(paginas),
                "--raiz",
                str(raiz),
                "--relatorio",
                str(relatorio),
                "--hoje",
                _HOJE.isoformat(),
            ]
        )
        == 0
    )
    rel = json.loads(relatorio.read_text(encoding="utf-8"))
    assert rel["escritos"] == ["con-gota"]
    assert [r["id"] for r in rel["reprovados_na_geracao"]] == ["con-artrose"]
    # "novalgina" ja' e' da dipirona (fixture) e "dor" e' generico: saem.
    assert set(rel["sinonimos_tirados"]["con-gota"]) == {"novalgina", "dor"}
    base = b.carregar_base(raiz)
    gota = base.assuntos["con-gota"]
    assert gota.revisao.status == b.STATUS_RASCUNHO_PENDENTE
    assert gota.revisao.escrito_por == "claude-sonnet-5.5 (rascunho)"
    assert gota.fontes[0].url is None and b.linha_de_credito(gota) == "Texto da operadora."
    assert b.problemas_de_politica(gota, hoje=_HOJE) == []
    corpo = (raiz / "condicao" / "con-gota.yaml").read_bytes()
    assert b"\r\n" not in corpo and corpo.startswith(b"# Base de conhecimento")
