"""SQLAlchemy Base — shared metadata for all models across the platform.

All Alembic migrations reference target_metadata from this module.
Import models here (or in migrations) to ensure they are registered
for autogenerate support.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base for all platform models."""

    pass
