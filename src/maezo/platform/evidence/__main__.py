"""Ponto de entrada da coleta de evidência.

    python -m maezo.platform.evidence              -> instância real ponta a ponta
    python -m maezo.platform.evidence.dmn_sweep    -> tabelas DMN avaliadas isoladas

As duas coisas são complementares e nenhuma substitui a outra: a instância prova que o
fluxo atravessa; o sweep prova o que cada tabela conclui, inclusive em casos que uma
instância normal não produziria.
"""

from __future__ import annotations

from .auth_instance import main

if __name__ == "__main__":
    raise SystemExit(main())
