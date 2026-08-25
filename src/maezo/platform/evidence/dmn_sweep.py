"""Validação das tabelas DMN por avaliação DIRETA, sem processo.

`POST /decision-definition/key/{key}/evaluate` avalia uma tabela isolada: você dá as
entradas, o engine devolve o que a linha vencedora concluiu. É a forma mais honesta de
validar regra determinística — não depende de o processo inteiro andar, e mostra a
tabela respondendo a casos que uma instância normal não produziria.

Complementa `auth_instance`, que é o oposto: uma instância real, ponta a ponta.

Uso:

    ENGINE_REST_URL=http://cibseven.<cluster>.internal:8080/engine-rest \\
        python -m maezo.platform.evidence.dmn_sweep

Não afirma que algo passou: imprime entrada e saída de cada caso e deixa a leitura
para quem confere. O julgamento de se a linha vencedora é a linha CERTA é clínico,
regulatório ou contratual — não é do código nem meu.

Os casos abaixo cobrem o caminho felizmente completo e as bordas que importam para o
princípio L0 ("nenhuma negativa automática"): beneficiário inativo, carência não
cumprida, validador que não executou. Se um deles algum dia devolver uma negativa em
vez de análise humana, é regressão de princípio, não de código.
"""

from __future__ import annotations

import os
from typing import Any

from maezo.gateway.tool_registry import build_dmn_seam, build_worker_seam_context
from maezo.tools.workers.dmn_transport import DmnEvaluationError, evaluate_sync

#: Casos por tabela: (chave DMN, título legível, [(rótulo, entradas)]).
#: Editar esta lista é a forma de acrescentar cobertura — sem tocar no motor.
CASOS: tuple[tuple[str, str, tuple[tuple[str, dict[str, Any]], ...]], ...] = (
    (
        "auth_admissibility",
        "ADMISSIBILIDADE da solicitação",
        (
            (
                "caminho felizmente completo",
                {
                    "requer_autorizacao": True,
                    "documentacao_completa": True,
                    "beneficiario_ativo": True,
                    "carencia_cumprida": True,
                },
            ),
            (
                "beneficiário INATIVO (não pode virar negativa automática)",
                {
                    "requer_autorizacao": True,
                    "documentacao_completa": True,
                    "beneficiario_ativo": False,
                    "carencia_cumprida": True,
                },
            ),
            (
                "carência NÃO cumprida (idem)",
                {
                    "requer_autorizacao": True,
                    "documentacao_completa": True,
                    "beneficiario_ativo": True,
                    "carencia_cumprida": False,
                },
            ),
            (
                "documentação INCOMPLETA",
                {
                    "requer_autorizacao": True,
                    "documentacao_completa": False,
                    "beneficiario_ativo": True,
                    "carencia_cumprida": True,
                },
            ),
            (
                "procedimento NÃO requer autorização",
                {
                    "requer_autorizacao": False,
                    "documentacao_completa": True,
                    "beneficiario_ativo": True,
                    "carencia_cumprida": True,
                },
            ),
        ),
    ),
    (
        "auth_sla",
        "PRAZO de análise por caráter/categoria",
        (
            ("eletivo + consulta", {"carater_atendimento": "eletivo", "categoria_procedimento": "consulta"}),
            (
                "urgência + consulta",
                {"carater_atendimento": "urgencia", "categoria_procedimento": "consulta"},
            ),
            (
                "eletivo + internação",
                {"carater_atendimento": "eletivo", "categoria_procedimento": "internacao"},
            ),
            (
                "eletivo + alta complexidade",
                {"carater_atendimento": "eletivo", "categoria_procedimento": "alta_complexidade"},
            ),
        ),
    ),
    (
        "auth_auto_approval",
        "APROVAÇÃO AUTOMÁTICA (os quatro critérios)",
        (
            (
                "todos os 4 OK e verificado",
                {
                    "auto_criteria_verificado": True,
                    "criterio_tecnico_ok": True,
                    "criterio_financeiro_ok": True,
                    "criterio_regulatorio_ok": True,
                    "criterio_contratual_ok": True,
                    "carater_atendimento": "eletivo",
                },
            ),
            (
                "um critério falso (técnico)",
                {
                    "auto_criteria_verificado": True,
                    "criterio_tecnico_ok": False,
                    "criterio_financeiro_ok": True,
                    "criterio_regulatorio_ok": True,
                    "criterio_contratual_ok": True,
                    "carater_atendimento": "eletivo",
                },
            ),
            (
                "validador NÃO executou (4 OK não bastam)",
                {
                    "auto_criteria_verificado": False,
                    "criterio_tecnico_ok": True,
                    "criterio_financeiro_ok": True,
                    "criterio_regulatorio_ok": True,
                    "criterio_contratual_ok": True,
                    "carater_atendimento": "eletivo",
                },
            ),
        ),
    ),
    (
        "carencia_check",
        "CARÊNCIA contratual",
        (
            (
                "consulta, 5 dias de adesão",
                {"tipo_procedimento": "consulta", "dias_desde_adesao": 5, "cpt_declarada": False},
            ),
            (
                "consulta, 400 dias de adesão",
                {"tipo_procedimento": "consulta", "dias_desde_adesao": 400, "cpt_declarada": False},
            ),
            (
                "parto, 200 dias",
                {"tipo_procedimento": "parto", "dias_desde_adesao": 200, "cpt_declarada": False},
            ),
            (
                "alta complexidade com CPT declarada",
                {"tipo_procedimento": "alta_complexidade", "dias_desde_adesao": 100, "cpt_declarada": True},
            ),
        ),
    ),
    (
        "dut_rol_coverage",
        "COBERTURA DUT/ROL por código TUSS",
        (
            (
                "consulta 40301010",
                {"codigo_procedimento_tuss": "40301010", "categoria_procedimento": "consulta"},
            ),
            (
                "código inexistente",
                {"codigo_procedimento_tuss": "99999999", "categoria_procedimento": "consulta"},
            ),
        ),
    ),
    (
        "triage_redflag_adult",
        "RED FLAG adulto (triagem clínica)",
        (
            (
                "dor torácica intensa, 55 anos",
                {"sintoma_codigo": "dor_toracica", "intensidade": "intensa", "idade_anos": 55},
            ),
            (
                "cefaleia leve, 30 anos",
                {"sintoma_codigo": "cefaleia", "intensidade": "leve", "idade_anos": 30},
            ),
        ),
    ),
)


def _base_url() -> str:
    return os.environ.get("ENGINE_REST_URL", "http://localhost:8080/engine-rest").rstrip("/")


def avaliar(chave: str, entradas: dict[str, Any], *, dmn: Any) -> list[dict[str, Any]] | str:
    """Avalia uma tabela pelo transporte SANCIONADO. Devolve as linhas, ou um texto da falha.

    MUDOU EM 25/08/2026: antes montava `"/decision-definition/key/" + chave + "/evaluate"` com
    um `httpx.Client` proprio. Duas razoes para trocar, e a segunda e' a que importa mais:

      - o portao `effect-chokepoint-fence` reprovava, e com razao: caminho de efeito duplicado
        fora do modulo que o possui e' exatamente como duas implementacoes do mesmo POST
        divergem — uma ganha retry, a outra nao, e ninguem nota ate' o dia em que importa;
      - `CibSevenDmnTransport` resolve a VERSAO autoritativa da tabela antes de avaliar
        (ADR-0028 §2). Para um sweep de cobertura de DMN isso nao e' detalhe: sem a versao, o
        relatorio diz "esta regra disparou" sem dizer de qual tabela — e uma tabela redeployada
        no meio do sweep produziria um relatorio que mistura duas.

    O `_tipar` local tambem saiu: o transporte faz a conversao para o formato do engine, e
    manter uma segunda copia da tabela de tipos era a mesma classe de duplicacao.
    """
    try:
        linhas, versao = evaluate_sync(dmn, chave, entradas)
    except DmnEvaluationError as exc:
        return "ERRO DE AVALIACAO: " + str(exc)[:200]

    if not linhas:
        # Tabela UNIQUE/FIRST sem linha casada. Nao e' erro do engine — e' lacuna de
        # cobertura da tabela, e saber disso e' metade do valor deste sweep.
        return (
            "NENHUMA REGRA DISPAROU — a tabela nao cobre esta entrada  (versao " + str(versao.version) + ")"
        )
    return linhas


def main() -> int:
    base = _base_url()
    total = 0
    sem_regra = 0
    # Pelo REGISTRO, nao construindo o transporte direto: e' o registro que aplica o portao de
    # autorizacao de acao antes de cada chamada, e importar o provedor concreto aqui
    # reintroduziria a dependencia que o portao `effect-chokepoint-fence` existe para impedir.
    # `tenant="amh"` porque este sweep e' de diagnostico do tenant unico de dev; um sweep
    # multi-tenant precisaria de um contexto por tenant, e seria outra ferramenta.
    dmn = build_dmn_seam(seam=build_worker_seam_context(tenant="amh"), base_url=base)
    for chave, titulo, casos in CASOS:
        print("")
        print("=" * 78)
        print(chave + "  —  " + titulo)
        print("=" * 78)
        for rotulo, entradas in casos:
            total += 1
            resultado = avaliar(chave, entradas, dmn=dmn)
            print("")
            print("  CASO: " + rotulo)
            print("    entrada -> " + str(entradas))
            if isinstance(resultado, str):
                if resultado.startswith("NENHUMA"):
                    sem_regra += 1
                print("    SAIDA   -> " + resultado)
            else:
                for saida in resultado:
                    print("    SAIDA   -> " + str(saida))

    print("")
    print("=" * 78)
    print("casos avaliados: " + str(total) + "   |   sem regra casada: " + str(sem_regra))
    print("Leitura humana obrigatória: uma linha que dispara não é uma linha CORRETA.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
