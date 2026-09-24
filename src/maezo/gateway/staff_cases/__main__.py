"""Entrypoint `python -m maezo.gateway.staff_cases`: uma rodada do emissor de casos staff (T1.6).

Ver `case_issuer_runtime`. Importar este modulo nao tem efeito colateral.
"""

from __future__ import annotations

import sys

from .case_issuer_runtime import main

if __name__ == "__main__":
    sys.exit(main())
