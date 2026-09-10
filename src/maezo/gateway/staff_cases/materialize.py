"""Single ECS init writer for one version-pinned, task-local staff material volume."""

from __future__ import annotations

import os
import sys

from .materials import _owned, decode_bundle
from .production_config import MATERIAL_PARENT, PortalProductionSettings, PortalStaffBootstrapError


def materialize(raw: bytes, settings: PortalProductionSettings) -> None:
    try:
        manifest, files = decode_bundle(raw, settings)
        parent = os.open(MATERIAL_PARENT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            _owned(os.fstat(parent), 0o700, directory=True)
            if os.listdir(parent):
                raise PortalStaffBootstrapError()
            # A single init container owns this fresh volume. Existing staging or
            # published contents are an uncertain predecessor, never overwritten.
            os.mkdir(".initializing", mode=0o700, dir_fd=parent)
            staging = os.open(".initializing", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                for name, content in sorted((files | {"manifest.json": manifest.canonical()}).items()):
                    fd = os.open(
                        name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400, dir_fd=staging
                    )
                    try:
                        view = memoryview(content)
                        while view:
                            written = os.write(fd, view)
                            if written <= 0:
                                raise PortalStaffBootstrapError()
                            view = view[written:]
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                os.fchmod(staging, 0o500)
                os.fsync(staging)
            finally:
                os.close(staging)
            # No other writer is permitted; BFF starts only after ECS SUCCESS and
            # sees this volume read-only. Never retry a failed initializer in place.
            os.rename(".initializing", "current", src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            os.close(parent)
    except Exception:
        raise PortalStaffBootstrapError() from None


def main() -> None:
    try:
        settings = PortalProductionSettings()  # type: ignore[call-arg]
        raw = os.environ.pop("MAEZO_PORTAL_STAFF_SECRET_BUNDLE").encode("utf-8")
        materialize(raw, settings)
    except Exception:
        print("portal_staff_bootstrap_unavailable", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
