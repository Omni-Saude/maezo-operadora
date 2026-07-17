"""Entrypoint `python -m maezo.platform.lifecycle` — intentional fail-closed refusal [T2.8].

See the package docstring (`__init__.py`) for why every invocation refuses and exits
non-zero. Importing this module has NO side effect; the refusal runs only under
`__main__`. The Helm CronJobs (`deploy/helm/maezo-tenant/templates/cronjob-lifecycle.yaml`)
hit this path — that non-zero exit is the DESIGN, not a defect.
"""

from __future__ import annotations

import sys

from . import main

if __name__ == "__main__":
    sys.exit(main())
