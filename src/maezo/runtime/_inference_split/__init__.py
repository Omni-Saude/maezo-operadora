"""Temporary staging package for the runtime.inference split (D2-02).

Not part of the public API. Every module here is promoted into ``maezo/runtime/inference/`` in
step 8 of ``docs/reports/inference-split-plan.md`` §5 (the same commit that renames
``runtime/inference.py`` to ``runtime/inference/__init__.py``); this package name is deliberately
DIFFERENT from the final ``inference/`` package name because a flat module ``inference.py`` and a
same-named package directory ``inference/`` cannot coexist in the same parent package under
CPython's import rules — a directory ``runtime/inference/`` (with or without ``__init__.py``)
SHADOWS the flat ``runtime/inference.py`` module the moment both exist, verified live this
session (a directory with ``__init__.py`` wins outright; a directory WITHOUT one still blocks any
``maezo.runtime.inference.<submodule>`` dotted import because the parent resolves to the flat
module, which has no ``__path__``). Staging here during steps 1-7 keeps ``runtime/inference.py``'s
own path stable — so the two CODEOWNED chokepoint-fence dictionaries that pin that literal path
(``scripts/ci/check_effect_chokepoint_fence.py`` §1.1) need NO edit until step 8, exactly as the
plan intends for steps 1-7 to be owner-review-free.
"""
