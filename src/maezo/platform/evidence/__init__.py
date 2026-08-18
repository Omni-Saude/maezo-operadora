"""Evidência de EXECUÇÃO — o que o engine registrou depois de rodar.

Distinto de `maezo.platform.validation`, que valida ARTEFATO estático (BPMN/DMN
bem-formados, referências cruzadas, política) para o CI. Aqui é execução real.

Dois coletores:

- `auth_instance` — abre uma instância de SP-OP-AUTH-001 e lê de volta o que o engine
  registrou: passos, decisões, critérios, prazos, tarefa humana, incidentes.
- `dmn_sweep` — avalia tabelas DMN DIRETAMENTE (`/decision-definition/.../evaluate`),
  sem processo, cobrindo casos de borda que uma instância normal não produziria.

Nenhum dos dois conclui que algo "passou". Existem porque "validamos" não é afirmação
— é um artefato que outra pessoa reexecuta e confere.
"""

from .auth_instance import CRITERIOS, PAYLOAD_BASE, coletar
from .auth_instance import main as main_instancia
from .dmn_sweep import CASOS, avaliar
from .dmn_sweep import main as main_dmn

__all__ = [
    "CASOS",
    "CRITERIOS",
    "PAYLOAD_BASE",
    "avaliar",
    "coletar",
    "main_dmn",
    "main_instancia",
]
