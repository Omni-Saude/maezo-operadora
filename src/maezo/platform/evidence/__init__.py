"""Evidência de EXECUÇÃO — o que o engine registrou depois de um processo rodar.

Distinto de `maezo.platform.validation`, que valida ARTEFATO estático (BPMN/DMN
bem-formados, referências cruzadas, política) para o CI. Aqui é o oposto do
estático: uma instância real é aberta contra um engine real e a evidência é lida
de volta dele.

Existe porque "validamos" não é afirmação — é um artefato que outra pessoa
reexecuta e confere.
"""

from .auth_instance import CRITERIOS, PAYLOAD_BASE, coletar, main

__all__ = ["CRITERIOS", "PAYLOAD_BASE", "coletar", "main"]
