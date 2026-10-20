"""Limpeza agendada da custodia do telefone (ADR-0061 Proposto): apaga as linhas VENCIDAS.

`python -m maezo.platform.custodia_limpeza`. Uma rodada chama
`gateway/recipient_custody.py::PostgresRecipientVault.purge_expired` uma vez e sai.

Por que existe: a leitura da custodia ja' IGNORA linha com `expires_at` no passado, mas ninguem a
APAGAVA — o telefone cifrado ficava no banco indefinidamente depois do prazo. Este job e' o chamador
que faltava. DESLIGADO por padrao (`MAEZO_CUSTODIA_LIMPEZA=false`): sai antes de montar qualquer
dependencia. Ligar (e o prazo de 30 dias que decide o que esta' "vencido") depende da ciencia do DPO
registrada para o ADR-0061 (`docs/sme-dispatch/dpo/CIENCIA-ADR-0061-RASCUNHO.md`).

A limpeza NAO tem chave: monta o cofre com `SemChaveKeyWrapper`, que recusa cifrar e decifrar. A task
nao precisa (nem recebe) permissao de KMS — ela so' apaga linha cujo prazo acabou.
"""

from .job import (
    ComposicaoDaLimpezaError,
    LimpezaCustodiaSettings,
    SemChaveKeyWrapper,
    executar,
    montar_cofre,
    rodar,
)

__all__ = [
    "ComposicaoDaLimpezaError",
    "LimpezaCustodiaSettings",
    "SemChaveKeyWrapper",
    "executar",
    "montar_cofre",
    "rodar",
]
