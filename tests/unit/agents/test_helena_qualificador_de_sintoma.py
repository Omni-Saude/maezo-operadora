"""O codigo grave nao nasce sem a palavra que o justifica — F3 da bateria de 21/09/2026.

O QUE FOI MEDIDO. Caso `C1`: a pessoa escreveu cinco palavras, *"estou com dor de cabeca"*. A
extracao devolveu `cefaleia_subita_intensa` — a regra `r4` da tabela adulta, que dispara
P1/emergencia com prazo de cinco minutos — com `intensidade=desconhecida`. E a resposta CITOU a
pessoa entre aspas: *"voce descreveu essa dor de cabeca como 'a pior da vida', de inicio subito,
correto?"*. Nenhuma das duas palavras estava na mensagem.

E' o mesmo defeito de 13/09, medido de novo oito dias depois. A diferenca e' que agora existe uma
regua escrita (`docs/design/regua-de-extracao.md`, R1 e R2) e ela nao estava no texto do prompt: o
prompt listava os codigos e pedia "nunca invente um codigo fora desta lista", o que e' uma regra
sobre o VOCABULARIO. O defeito nao foi de vocabulario — o codigo existe e e' legitimo. Foi
atribuir um codigo QUALIFICADO a uma mensagem sem o qualificador.

O CONTRASTE QUE PROVA QUE A TABELA ESTA CERTA, e que estes testes existem para preservar: o caso
`C4`, *"minha dor de cabeca comecou de repente e e a pior da minha vida"*, sai
`cefaleia_subita_intensa`/P1 e TEM de continuar saindo. Ensinar a extracao a nunca produzir o
codigo trocaria um falso positivo por um falso negativo numa emergencia real — por isso a regra e'
"sem o qualificador na mensagem", nunca "sem o codigo".

O QUE ESTE ARQUIVO PROVA, e o que ele NAO prova. Prova que a regra esta no texto que o modelo le',
que a lista de codigos qualificados nao pode crescer em silencio (um codigo novo com qualificador
no nome e sem declaracao deixa a cerca vermelha) e que a proibicao de citar o beneficiario entre
aspas esta no prompt de resposta. NAO prova que o modelo obedece — isso e' o corpus rotulado
(`tests/evals/extracao/casos.json`) mais a medicao ao vivo (`tests/evals/test_extracao_live.py`),
e a licao de 12 e 13/09 e' exatamente que pedir no prompt nao segura sozinho.

POR QUE A COBRANCA AQUI NAO E' UMA CERCA DE CODIGO (como a de saida). Uma cerca que recusasse
`cefaleia_subita_intensa` quando a mensagem nao contem as palavras "subita"/"intensa" reprovaria o
`C4`, onde a pessoa disse as duas coisas com OUTRAS palavras ("comecou de repente", "a pior da
minha vida"). A R1 da regua e' explicita: traduzir o que foi dito de outro jeito e' leitura, nao
invencao. Uma cerca literal aqui trocaria o defeito por um pior, e por isso a cobranca e' o rotulo
humano do corpus — que julga a mensagem inteira — e nao um `in`.
"""

from __future__ import annotations

import pytest

from maezo.agents.helena.prompts import (
    ALLOWED_SINTOMA_CODIGOS,
    CLASSIFY_PROMPT_VERSION,
    QUALIFICADORES_OBRIGATORIOS,
    RESPONSE_PROMPT_VERSION,
    SINTOMA_CODIGOS_BY_POPULATION,
    TOKENS_DE_QUALIFICADOR,
    classify_prompt,
    response_prompt,
)

#: A mensagem MEDIDA no caso `C1` de 21/09/2026, e o pedaco do `C4` que tem de continuar casando.
C1_MEDIDO = "estou com dor de cabeca"
C4_CONTRASTE = "comecou de repente"


# =================================================================================================
# 1. A declaracao dos codigos qualificados nao pode envelhecer
# =================================================================================================


def test_todo_codigo_com_qualificador_no_nome_esta_declarado() -> None:
    """A direcao que pega o codigo NOVO, que e' como esta lista apodrece.

    Acrescentar `dor_toracica_intensa` a allowlist sem declarar o qualificador poria de volta no
    vocabulario um codigo que o modelo pode escolher sem a palavra que o justifica — e a cerca
    ficaria verde, porque nada ligaria o nome do codigo a regra.
    """
    com_qualificador = {
        codigo for codigo in ALLOWED_SINTOMA_CODIGOS if any(t in codigo for t in TOKENS_DE_QUALIFICADOR)
    }
    faltando = com_qualificador - set(QUALIFICADORES_OBRIGATORIOS)

    assert not faltando, (
        f"codigo(s) com qualificador no nome e sem declaracao: {sorted(faltando)}. Declare o "
        f"qualificador em QUALIFICADORES_OBRIGATORIOS — sem isso o prompt nao cobra a palavra que "
        f"justifica o codigo, que e' o defeito de 13/09 e de 21/09."
    )


def test_nenhum_codigo_declarado_esta_fora_da_allowlist() -> None:
    """O inverso: uma declaracao para codigo que nao existe mais e' regra cobrada no vazio."""
    orfaos = set(QUALIFICADORES_OBRIGATORIOS) - set(ALLOWED_SINTOMA_CODIGOS)

    assert not orfaos, f"declaracao para codigo inexistente na allowlist: {sorted(orfaos)}"


def test_os_cinco_codigos_qualificados_de_hoje_estao_declarados() -> None:
    """Prova de nao-vacuidade do teste acima: se `TOKENS_DE_QUALIFICADOR` fosse esvaziado, ele
    passaria sobre um conjunto vazio e ninguem veria."""
    assert set(QUALIFICADORES_OBRIGATORIOS) == {
        "cefaleia_subita_intensa",
        "sangramento_ativo",
        "cefaleia_alteracao_visual",
        "contracoes_regulares",
        "movimentos_fetais_reduzidos",
    }


def test_o_codigo_de_fallback_existe_na_mesma_populacao_ou_e_null() -> None:
    """`sem_ele` e' o que a extracao devolve quando o qualificador nao apareceu.

    Ele TEM de ser `null` ou um codigo que a tabela daquela mesma populacao reconheca: apontar
    para o codigo de outra populacao produziria uma extracao que o validador do grafo trata como
    `falha_tecnica` — trocando um codigo grave inventado por um turno perdido.
    """
    problemas: list[str] = []
    for codigo, q in QUALIFICADORES_OBRIGATORIOS.items():
        if q.sem_ele == "null":
            continue
        for populacao, codes in SINTOMA_CODIGOS_BY_POPULATION.items():
            if codigo in codes and q.sem_ele not in codes:
                problemas.append(f"{codigo}: fallback {q.sem_ele!r} nao existe em {populacao!r}")
    assert not problemas, "\n  ".join(problemas)


def test_todo_qualificador_declara_o_que_a_mensagem_precisa_ter_dito() -> None:
    """Um `exige` vazio seria uma regra sem conteudo — e ela vai LITERALMENTE para o prompt."""
    for codigo, q in QUALIFICADORES_OBRIGATORIOS.items():
        assert q.tokens, f"{codigo}: sem token de qualificador"
        assert len(q.exige.strip()) > 20, f"{codigo}: `exige` curto demais para o modelo entender"


# =================================================================================================
# 2. A regra esta no texto que o modelo le'
# =================================================================================================


def test_o_prompt_de_classify_cobra_o_qualificador_de_cada_codigo_declarado() -> None:
    """O prompt e' GERADO da declaracao — se ele puder divergir dela, a regra vale no papel.

    Mesma razao pela qual a allowlist de codigos ja' e' gerada de `SINTOMA_CODIGOS_BY_POPULATION`
    em vez de escrita a mao no texto.
    """
    texto = classify_prompt()

    for codigo, q in QUALIFICADORES_OBRIGATORIOS.items():
        assert codigo in texto, f"{codigo} nao aparece no prompt de classify"
        assert q.exige in texto, f"o qualificador exigido de {codigo} nao aparece no prompt"


def test_o_prompt_de_classify_proibe_atribuir_qualificador_que_nao_apareceu() -> None:
    texto = classify_prompt()

    assert "QUALIFICADOR" in texto
    assert "NUNCA atribua" in texto
    # A justificativa viaja junto: sem ela a regra vira proibicao sem porque, e a proxima edicao
    # do prompt a remove por parecer redundante (mesma logica da cerca da negativa clinica).
    assert "P1" in texto and "cinco minutos" in texto


def test_o_prompt_de_classify_carrega_a_mensagem_medida_como_contraexemplo() -> None:
    """Cinco palavras que viraram um P1. O contraexemplo no proprio prompt e' o que impede a
    proxima edicao de "simplificar" a regra sem saber o que ela custou."""
    texto = classify_prompt()

    assert C1_MEDIDO in texto
    assert "null" in texto


def test_o_prompt_de_classify_manda_desconhecida_quando_a_intensidade_nao_foi_dita() -> None:
    """R1 da regua. No `C1` a intensidade veio `desconhecida` e o codigo veio grave — a extracao
    inventou o qualificador e nao inventou a intensidade, o que mostra que as duas regras nao
    estavam cobradas no mesmo lugar."""
    texto = classify_prompt()

    assert "nunca infira" in texto.lower()
    assert '"desconhecida" sempre que a mensagem NAO disser a intensidade' in texto


def test_o_prompt_de_classify_preserva_a_leitura_com_outras_palavras() -> None:
    """A metade que protege o `C4`.

    "Comecou de repente" e "a pior da minha vida" SAO a pessoa dizendo subito e intenso. Uma regra
    que exigisse a palavra literal reprovaria a emergencia de verdade — e e' o falso negativo que
    custa mais caro que o falso positivo que estamos consertando.
    """
    texto = classify_prompt()

    assert C4_CONTRASTE in texto
    assert "leitura" in texto and "invencao" in texto


# =================================================================================================
# 3. F3(b) — a resposta nunca cita o beneficiario
# =================================================================================================


def test_o_prompt_de_resposta_proibe_aspas_sobre_a_fala_do_beneficiario() -> None:
    """Fabricar uma citacao e' pior que inferir: o texto entre aspas parece prova.

    A Helena escreveu que a pessoa "descreveu essa dor de cabeca como 'a pior da vida'" numa
    conversa de UMA mensagem, em que a pessoa nao descreveu nada disso.
    """
    texto = response_prompt()

    assert "NUNCA CITE O BENEFICIARIO ENTRE ASPAS" in texto
    assert "terceira pessoa" in texto


def test_o_prompt_de_resposta_nao_quebra_a_frase_de_memoria_a_confirmar() -> None:
    """A confirmacao da memoria clinica (Frente 2.1) JA' vem pronta e em terceira pessoa.

    A proibicao de aspas nao pode ser lida como "nao confirme": confirmar o dado lembrado e'
    obrigatorio, e a frase vem de `graph.py::_frase_de_confirmacao`, nao do modelo.
    """
    texto = response_prompt()

    assert "memoria_a_confirmar" in texto
    assert "copie-a como veio" in texto


# =================================================================================================
# 4. A versao e' o que responde QUAL texto falou com o beneficiario
# =================================================================================================


@pytest.mark.parametrize(
    ("rotulo", "versao", "anterior"),
    [
        ("classify", CLASSIFY_PROMPT_VERSION, "classify-v3"),
        ("response", RESPONSE_PROMPT_VERSION, "response-v6"),
    ],
)
def test_a_versao_subiu_junto_com_a_regra(rotulo: str, versao: str, anterior: str) -> None:
    """Mudar a regra sem subir a versao faz o numero apontar para o texto errado — e esse numero
    e' o que responderia a um auditor da ANS sobre qual instrucao falou com a pessoa."""
    assert versao.startswith(f"{rotulo}-v")
    assert versao != anterior, f"o texto de {rotulo} mudou em 21/09; a versao tem de subir"
