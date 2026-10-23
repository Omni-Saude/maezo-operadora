"""Habilidade `cobertura` FORA da Helena (dono, 23/10/2026, DL-0154; Plano de ajustes v2, etapa 5).

A Helena nao trata mais de cobertura: nenhuma consulta ao Rol, a carencia ou a segmentacao para dizer se um
procedimento esta' coberto, nenhuma explicacao de Rol/carencia e nenhuma oferta da equipe por causa da
cobertura. Toda pergunta de cobertura recebe UM aviso fixo com os canais da empresa
(`TEXTO_COBERTURA_FORA`, chave `cobertura_fora` do pacote de textos). O codigo da cobertura assistida
(`cobertura_assistida.py`, `cobertura_fluxo.py`, `cobertura_rol.py`) e o modo `MAEZO_HELENA_COBERTURA_MODO`
sairam; o historico do repositorio guarda a versao anterior (ate' 3c619d91).

Os DADOS do plano (carteirinha, dependentes, carencias do proprio titular, tipo de plano) continuam com a
habilidade `plano`. A trava de saida `cobertura_sem_promessa` continua: nenhum rascunho afirma cobertura.
"""

from __future__ import annotations

from typing import Final

#: O aviso do dono (23/10/2026), palavra por palavra.
TEXTO_COBERTURA_FORA: Final[str] = (
    "Por aqui eu não trato de cobertura. Você consegue essa informação pelo aplicativo Austa Clínicas, "
    "pelo portal ou pela central."
)
#: A chave do texto no pacote de comportamento (`spec/agents/helena/pacote/textos.yaml`).
CHAVE_TEXTO: Final[str] = "cobertura_fora"

__all__ = ["CHAVE_TEXTO", "TEXTO_COBERTURA_FORA"]
