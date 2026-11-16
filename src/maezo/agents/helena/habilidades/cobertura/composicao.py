"""A COMPOSICAO da habilidade `cobertura` com o pacote K2 pinado (DL-0167; combinado T-MZO-A/T-MZO-B).

A composicao do processo (T-MZO-A) chama SO' `compor_cobertura(raiz, cfg, catalogo)`; a regra de quando a
camada `regra_geral` entra fica aqui, com a dona da Helena:

1. o pin vem do lock do consumidor (`config/integrations/rco/k2.lock.json`, `manual.pin_do_lock`);
   `sem_pacote` ou lock ausente = nada acontece (no-op);
2. o pacote em `config/integrations/rco/k2/` e' lido contra ESSE pin (`manual.carregar_ou_nenhum`), com o
   aceite de nao ratificado decidido pela configuracao e pelo `MAEZO_ENV` vivo (`aceite_da_configuracao`):
   producao nunca aceita pacote nao ratificado;
3. SO' com pin E pacote: registra o hash do MESMO pin para a isencao de prazo (`CHAVE_DO_HASH_K2`, HEL-5b) e a
   camada `regra_geral`. O hash tem um lugar so': o lock.

NUNCA liga a habilidade (`CHAVE_DO_GRAFO`): isso e' do pacote de comportamento. Sem pacote valido, nada e'
registrado e a habilidade (se ligada) cai no aviso de canais (falha fechada).

`catalogo`: os itens do catalogo de procedimentos da Tina ja' lidos (estatico). Vazio = a camada segue so'
pelos sinonimos do proprio K2 (`regra_geral`, item 1). A leitura ASSINCRONA por pergunta
(`FonteDoCatalogoDeProcedimentos`) e' outra unidade.
"""

from __future__ import annotations

from collections.abc import MutableMapping, Sequence
from pathlib import Path
from typing import Any, Final

import structlog

from maezo.agents.helena.habilidades import cobertura as _cobertura
from maezo.agents.helena.habilidades.cobertura import manual as _manual
from maezo.agents.helena.habilidades.cobertura.regra_geral import CamadaRegraGeral
from maezo.ports.tina import ProcedimentoCatalogo

logger = structlog.get_logger(__name__)

#: Onde o lock e os bytes do K2 moram, relativos a raiz do repositorio (combinado com a T-MZO-A, #1249).
LOCK_DO_K2: Final[str] = "config/integrations/rco/k2.lock.json"
DIRETORIO_DO_K2: Final[str] = "config/integrations/rco/k2"


def compor_cobertura(
    raiz: Path, cfg: MutableMapping[str, Any], catalogo: Sequence[ProcedimentoCatalogo] = ()
) -> None:
    """Registra em `cfg` o hash do K2 e a camada `regra_geral`, so' com pin E pacote validos (ver o modulo).

    Lock invalido sobe como `PacoteK2InvalidoError` (erro de configuracao do repositorio, nao do pacote)."""
    pin = _manual.pin_do_lock(raiz / LOCK_DO_K2)
    if pin is None:
        return
    manual = _manual.carregar_ou_nenhum(
        raiz / DIRETORIO_DO_K2, pin=pin, aceite=_manual.aceite_da_configuracao(cfg)
    )
    if manual is None:
        return
    cfg[_cobertura.CHAVE_DO_HASH_K2] = pin.sha256_manifesto
    existentes = cfg.get(_cobertura.CHAVE_DAS_CAMADAS) or ()
    camadas = [c for c in existentes if getattr(c, "camada", None) != "regra_geral"]
    cfg[_cobertura.CHAVE_DAS_CAMADAS] = [CamadaRegraGeral(manual, catalogo=catalogo), *camadas]
    logger.info("helena_k2_composto", versao=manual.versao, regras=len(manual.regras))


__all__ = ["DIRETORIO_DO_K2", "LOCK_DO_K2", "compor_cobertura"]
