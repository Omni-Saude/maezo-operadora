"""AUTH intake/response dispatch daemon (`python -m maezo.runtime.intake_dispatch`).

Importing this package has no side effect: the composition root lives in `__main__.main`,
the drain engine in `service.py` (pure over ports) and the concrete Postgres/published-head
adapters in `assembly.py`.
"""
