"""Frente 8.1 — a conversa real vira caso de teste, e o rotulo continua sendo humano.

O QUE ESTE ARQUIVO DEFENDE, e a ordem e' a da gravidade:

  1. O ROTULO NASCE VAZIO. E' a decisao central do modulo e a contraintuitiva: copiar a extracao
     observada para o rotulo faria o corpus AFIRMAR que "dor de cabeca" e' `cefaleia_subita_intensa`
     — porque foi isso que o modelo fez em 13/09 — e a medicao passaria a dar nota alta para o
     defeito. Um conjunto de testes gerado a partir do comportamento observado nao mede correcao:
     mede estabilidade, e abencoa o erro no dia em que ele acontece.
  2. O TEXTO DO BENEFICIARIO E' PSEUDONIMIZADO antes de tocar qualquer saida — id incluido.
  3. Um caso gerado e NAO rotulado nao consegue entrar na medicao em silencio: a cerca do corpus o
     reprova.
  4. A resposta da HELENA nunca vira caso. Rotular a saida do sistema seria medi-lo contra ele
     mesmo.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from maezo.tools.conversa_para_caso import (
    _ESQUELETO_DO_ROTULO,
    carregar_transcricao,
    chaves_do_corpus,
    gerar_casos,
    main,
    observado_de,
)


def _turnos(*mensagens: str) -> list[dict[str, Any]]:
    return [{"mensagem": m, "item": {"mensagem": m}} for m in mensagens]


# =================================================================================================
# 1. O rotulo nasce vazio
# =================================================================================================


def test_o_rotulo_nasce_vazio_mesmo_quando_a_extracao_observada_esta_ali() -> None:
    """O caso de 13/09, exatamente.

    A transcricao traz `sintoma_codigo="cefaleia_subita_intensa"` — o que o modelo REALMENTE
    produziu para "to com dor de cabeca". Se o comando copiasse isso para `esperado`, o corpus
    passaria a cobrar o defeito como se fosse o certo.
    """
    turnos = [
        {
            "mensagem": "to com dor de cabeca desde ontem",
            "item": {
                "mensagem": "to com dor de cabeca desde ontem",
                "sintoma_codigo": "cefaleia_subita_intensa",
                "intensidade": "grave",
                "population": "adult",
                "intent": "symptom",
            },
        }
    ]
    casos, _ = gerar_casos(turnos, origem="t", ja_existentes=set())

    assert len(casos) == 1
    assert casos[0]["esperado"] == _ESQUELETO_DO_ROTULO, (
        "o rotulo foi preenchido com a extracao observada — e' o unico jeito de este comando "
        "transformar um defeito em gabarito"
    )
    assert all(valor is None for valor in casos[0]["esperado"].values())


def test_a_extracao_observada_e_preservada_num_campo_proprio() -> None:
    """Ela nao e' descartada: e' a metade da conversa com quem assina a regua.

    O que se quer discutir e' a DIFERENCA entre o que o modelo fez e o que deveria fazer — e
    jogar o observado fora apagaria justamente essa diferenca.
    """
    turnos = [
        {
            "mensagem": "to com dor de cabeca",
            "item": {"mensagem": "to com dor de cabeca", "sintoma_codigo": "cefaleia_subita_intensa"},
        }
    ]
    casos, _ = gerar_casos(turnos, origem="t", ja_existentes=set())
    assert casos[0]["observado"]["sintoma_codigo"] == "cefaleia_subita_intensa"


def test_o_caso_gerado_se_declara_pendente_de_rotulo() -> None:
    casos, _ = gerar_casos(_turnos("estou com febre"), origem="t", ja_existentes=set())
    assert casos[0]["precisa_de_rotulo"] is True
    assert "regua" in casos[0]["porque"].lower()


def test_um_caso_gerado_e_nao_rotulado_nao_passa_pela_cerca_do_corpus() -> None:
    """A prova de que o item 3 nao depende de disciplina de ninguem.

    Colar um caso gerado direto no corpus deixa a suite VERMELHA, porque `intent=None` nao esta no
    vocabulario fechado. E' o que impede o atalho "gerei cem casos, o corpus cresceu" — crescer sem
    rotular seria diluir a medicao, nao amplia-la.
    """
    from tests.unit.evals.test_corpus_de_extracao import _INTENTS, _POPULACOES

    casos, _ = gerar_casos(_turnos("estou com febre"), origem="t", ja_existentes=set())
    esperado = casos[0]["esperado"]
    assert esperado["intent"] not in _INTENTS
    assert esperado["population"] not in _POPULACOES


# =================================================================================================
# 2. Pseudonimizacao
# =================================================================================================


def test_o_texto_do_beneficiario_e_pseudonimizado() -> None:
    """A MESMA rede que protege o `resumo_contexto` no start de processo, nao uma copia.

    Duas redes divergem, e a que fica para tras e' sempre a menos usada.
    """
    turnos = _turnos("meu cpf e 529.982.247-25 e meu email e joao@exemplo.com, estou com febre")
    casos, _ = gerar_casos(turnos, origem="t", ja_existentes=set())
    mensagem = casos[0]["mensagem"]
    assert "529.982.247-25" not in mensagem
    assert "joao@exemplo.com" not in mensagem
    assert "febre" in mensagem, "a rede levou junto o conteudo clinico — o caso ficaria inutil"


def test_o_identificador_deriva_do_texto_ja_pseudonimizado() -> None:
    """O id vai para o nome do caso, para o log e para a conversa do time.

    Um id derivado do texto CRU levaria o identificador do beneficiario para todos esses lugares —
    um vazamento pela porta que ninguem olha.
    """
    casos, _ = gerar_casos(
        _turnos("meu cpf e 529.982.247-25 e to com febre"), origem="t", ja_existentes=set()
    )
    assert "529" not in casos[0]["id"]


# =================================================================================================
# 3. Deduplicacao e forma da transcricao
# =================================================================================================


def test_conversa_ja_no_corpus_nao_vira_caso_novo() -> None:
    """Rodar o comando duas vezes sobre a mesma bateria dobraria o peso daquelas mensagens na nota.

    E' um erro silencioso: o corpus cresce, a cobertura nao.
    """
    from maezo.tools.conversa_para_caso import _normalizar

    ja = {_normalizar("Estou com Febre!")}
    casos, descartados = gerar_casos(_turnos("estou com febre"), origem="t", ja_existentes=ja)
    assert casos == []
    assert descartados == 1


def test_a_deduplicacao_ignora_acento_caixa_e_pontuacao() -> None:
    """Duas transcricoes da mesma conversa quase nunca sao byte a byte iguais.

    Uma veio do log, outra da tela. Deduplicar por igualdade exata deixaria passar exatamente a
    duplicata que importa.
    """
    casos, descartados = gerar_casos(
        _turnos("Estou com febre!", "estou com   FEBRE"), origem="t", ja_existentes=set()
    )
    assert len(casos) == 1
    assert descartados == 1


def test_a_resposta_da_helena_nunca_vira_caso() -> None:
    """Rotular a saida do sistema seria medi-lo contra ele mesmo.

    O eco do receptor (`/receptor/simular`) devolve `resposta` junto do turno — um comando que a
    tratasse como mensagem geraria casos cujo "beneficiario" e' a propria Helena.
    """
    turnos = carregar_transcricao_de(
        [{"resposta": "Ola, como posso ajudar?", "conversation_id": "wa:amh:hk1_x"}]
    )
    assert turnos == []


def test_aceita_as_duas_formas_de_transcricao(tmp_path: Path) -> None:
    """Lista de turnos e o envelope `{"turnos": [...]}` — as duas que este repo produz.

    Obrigar uma conversao a mao antes de usar o comando seria recriar o procedimento manual que o
    comando existe para eliminar.
    """
    assert len(carregar_transcricao_de([{"mensagem": "estou com febre"}])) == 1
    assert len(carregar_transcricao_de({"turnos": [{"message_body": "estou com febre"}]})) == 1


def carregar_transcricao_de(conteudo: Any) -> list[dict[str, Any]]:
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(conteudo, fh, ensure_ascii=False)
        caminho = Path(fh.name)
    try:
        return carregar_transcricao(caminho)
    finally:
        caminho.unlink(missing_ok=True)


def test_observado_so_carrega_os_campos_da_extracao() -> None:
    """Um `observado` que copiasse o item inteiro traria estado do turno (business key, erro,
    telefone) para dentro de um arquivo de teste versionado."""
    observado = observado_de(
        {
            "mensagem": "x",
            "sintoma_codigo": "febre",
            "escalation_business_key": "ESC-amh-wa:amh:hk1_deadbeef",
            "error": "algo",
        }
    )
    assert observado == {"sintoma_codigo": "febre"}


# =================================================================================================
# 4. O comando de ponta a ponta
# =================================================================================================


def test_o_comando_roda_e_avisa_que_o_proximo_passo_e_humano(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Um comando que gera casos e nao diz que falta rotular produz um corpus mudo.

    A mensagem final e' parte da entrega, nao cortesia.
    """
    transcricao = tmp_path / "conversa.json"
    transcricao.write_text(json.dumps([{"mensagem": "to com uma dor estranha no joelho"}]), encoding="utf-8")
    saida = tmp_path / "novos.json"
    corpus_vazio = tmp_path / "corpus.json"

    codigo = main([str(transcricao), "--saida", str(saida), "--corpus", str(corpus_vazio)])

    assert codigo == 0
    gerados = json.loads(saida.read_text(encoding="utf-8"))["casos"]
    assert len(gerados) == 1
    erro = capsys.readouterr().err
    assert "PROXIMO PASSO" in erro and "humano" in erro


def test_transcricao_sem_turno_de_beneficiario_falha_alto(tmp_path: Path) -> None:
    """Silencio aqui seria pior que erro: quem roda o comando concluiria que a conversa nao tinha
    nada a aprender, quando o que houve foi um formato que o leitor nao entendeu."""
    transcricao = tmp_path / "vazia.json"
    transcricao.write_text(json.dumps([{"resposta": "so a Helena falou"}]), encoding="utf-8")
    assert main([str(transcricao), "--corpus", str(tmp_path / "c.json")]) == 1


def test_o_corpus_de_verdade_e_lido_como_base_de_deduplicacao() -> None:
    """Prova de nao-vacuidade da deduplicacao: o default aponta para o corpus REAL.

    Um default para um caminho inexistente faria `chaves_do_corpus` devolver conjunto vazio e a
    deduplicacao nunca descartar nada — verde, e inutil.
    """
    from maezo.tools.conversa_para_caso import CORPUS_PADRAO

    raiz = Path(__file__).resolve().parents[3]
    chaves = chaves_do_corpus(raiz / CORPUS_PADRAO)
    assert len(chaves) >= 100, f"o corpus padrao nao foi lido ({len(chaves)} chaves)"
