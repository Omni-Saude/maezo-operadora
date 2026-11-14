"""O vocabulario FECHADO da identidade do beneficiario (DL-0077/DL-0078), em comum entre o adaptador da AMH
(`platform/webhooks/whatsapp/helena_identidade.py`), o despachante e o grafo da Helena (ADR-0067).

Morava em `agents/helena/graph.py`; o grafo continua exportando os mesmos nomes.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

#: IDENTIDADE DO BENEFICIARIO (DL-0077). Quem escreve, resolvido pela AMH a partir do telefone (hash) e
#: entregue pelo despachante SO' com `MAEZO_HELENA_IDENTIDADE_AMH` ligada e SO' quando o telefone aponta
#: para UMA pessoa. E' CONTEXTO, nunca insumo de decisao: nenhum no' da Helena le' este campo para
#: classificar, tabelar, escalar ou redigir (nao entra no prompt nem na extracao); ele viaja no estado e
#: na telemetria para personalizacao futura e roteamento correto. Vocabulario FECHADO (Zona Geral,
#: ADR-0006): referencia opaca, faixa etaria grossa (nunca idade exata nem nascimento), e os fatos de
#: plano do contrato. Nunca telefone, CPF ou nome. `None` = sem identidade = a Helena de sempre.
#: UNICA EXCECAO DECLARADA (DL de 07/10/2026): o no' `consultar_plano`, so' com
#: `MAEZO_HELENA_CONSULTAS_AMH` ligada, le' a REFERENCIA OPACA para consultar os fatos do plano na AMH —
#: a referencia nunca entra no prompt, e o resto da identidade continua sem leitor.
IDENTIDADE_FAIXAS_ETARIAS: frozenset[str] = frozenset(
    {"lactente", "crianca", "adolescente", "adulto", "idoso"}
)
IDENTIDADE_CHAVES: frozenset[str] = frozenset(
    {
        "portable_subject_ref",
        "faixa_etaria",
        "plano_ativo",
        "vigencia_inicio",
        "vigencia_fim",
        "carencia_vigente",
        "titular_ref",
    }
)
_IDENTIDADE_REF: re.Pattern[str] = re.compile(r"^[A-Za-z0-9._:~-]{1,256}$")
_IDENTIDADE_DATA: re.Pattern[str] = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalizar_identidade(valor: object) -> dict[str, Any] | None:
    """A identidade em forma canonica, ou `None` quando ela nao pode ser confiada.

    Estrita e fail-closed: mapeamento com exatamente `IDENTIDADE_CHAVES`, referencia opaca no formato
    esperado, faixa dentro do vocabulario, booleanos/`None` e datas ISO (`YYYY-MM-DD`). Qualquer desvio
    devolve `None` — nunca uma identidade "parcial" ou corrigida. Nao levanta.
    """
    if not isinstance(valor, Mapping) or frozenset(valor) != IDENTIDADE_CHAVES:
        return None
    ref = valor["portable_subject_ref"]
    titular = valor["titular_ref"]
    faixa = valor["faixa_etaria"]
    if not isinstance(ref, str) or not _IDENTIDADE_REF.fullmatch(ref):
        return None
    if titular is not None and (not isinstance(titular, str) or not _IDENTIDADE_REF.fullmatch(titular)):
        return None
    if faixa is not None and faixa not in IDENTIDADE_FAIXAS_ETARIAS:
        return None
    for chave in ("plano_ativo", "carencia_vigente"):
        if valor[chave] is not None and not isinstance(valor[chave], bool):
            return None
    for chave in ("vigencia_inicio", "vigencia_fim"):
        data = valor[chave]
        if data is not None and (not isinstance(data, str) or not _IDENTIDADE_DATA.fullmatch(data)):
            return None
    return {chave: valor[chave] for chave in sorted(IDENTIDADE_CHAVES)}


#: AVISO DE IDENTIDADE (DL-0078, decisao do dono de 07/10/2026). O DESFECHO da resolucao pela AMH, em
#: vocabulario FECHADO, entregue pelo despachante ao lado da identidade (campo de ENTRADA
#: `identidade_desfecho`):
#:   * `reconhecido`    — candidato UNICO e identidade de vocabulario fechado montada;
#:   * `nao_encontrado` — a AMH respondeu DEFINITIVAMENTE que nao ha' candidato (`nenhum`, conjunto vazio);
#:   * `indeterminado`  — todo o resto (telefone compartilhado, falha, prazo, sem base legal, perfil fora
#:     do vocabulario). Indeterminado NAO fala nada sobre identidade: a Helena de sempre.
#: `None` = flag desligada (ou entrada sem despachante) = a Helena de sempre, byte a byte.
#:
#: SO' O FATO "reconheci / nao encontrei" chega a pessoa. NENHUM dado do plano (ativo, vigencia,
#: carencia, faixa etaria, titular) e' dito, nunca: identificar pelo telefone nao e' autenticar, e a
#: pessoa do outro lado pode nao ser o beneficiario. A identidade continua fora de todo prompt.
IDENTIDADE_RECONHECIDA: str = "reconhecido"
IDENTIDADE_NAO_ENCONTRADA: str = "nao_encontrado"
IDENTIDADE_INDETERMINADA: str = "indeterminado"
IDENTIDADE_DESFECHOS: frozenset[str] = frozenset(
    {IDENTIDADE_RECONHECIDA, IDENTIDADE_NAO_ENCONTRADA, IDENTIDADE_INDETERMINADA}
)
#: O nome de exibicao da operadora nos textos de identidade. O despachante passa o valor da settings
#: `helena_identidade_nome_operadora` (`MAEZO_HELENA_IDENTIDADE_NOME_OPERADORA`); este e' o default.
NOME_OPERADORA_PADRAO: str = "Austa Clínicas"
