"""Retention Manager — audit data retention policy (ADR-0007, DL-0018).

Implements the regulatory retention period of 5 years for audit data.
Per ADR-0007: "volume — partitioning + TTL (regulatorio 5+ anos; operacional meses)"

The RetentionManager provides:
- cutoff calculation: 5 years from now (by default)
- expiry check: whether a given timestamp has exceeded the retention period
- batch expiry: filter records older than the cutoff

Usage:
    manager = RetentionManager(retention_years=5)
    expired = manager.is_expired(some_timestamp)
    cutoff = manager.cutoff_timestamp()  # for DELETE WHERE ts < cutoff
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Regulatory minimum: 5 years per ADR-0007
DEFAULT_RETENTION_YEARS: int = 5


class RetentionManager:
    """Manages audit data retention with configurable TTL.

    ADR-0007 requires audit data to be retained for at least 5 years
    (regulatory minimum). After the retention period, data may be purged.

    The manager is designed to be used in cron jobs or lifecycle hooks
    that periodically clean up expired audit records.

    Typical usage:
        manager = RetentionManager(retention_years=5)
        cutoff = manager.cutoff_timestamp()
        # DELETE FROM audit_chain WHERE ts < cutoff
    """

    def __init__(self, retention_years: int = DEFAULT_RETENTION_YEARS) -> None:
        """Initialize the retention manager.

        Args:
            retention_years: Number of years to retain audit data.
                             Must be >= 5 (regulatory minimum). Default: 5.
        """
        if retention_years < 5:
            logger.warning(
                "retention_below_minimum",
                retention_years=retention_years,
                minimum_years=DEFAULT_RETENTION_YEARS,
            )
            retention_years = DEFAULT_RETENTION_YEARS

        self.retention_years = retention_years
        self.retention_period = timedelta(days=retention_years * 365)
        logger.info(
            "retention_manager_initialized",
            retention_years=retention_years,
        )

    def cutoff_timestamp(self, reference: datetime | None = None) -> datetime:
        """Calculate the cutoff timestamp for retention expiry.

        Records with timestamp OLDER than this cutoff are eligible for deletion.

        Args:
            reference: Reference point for cutoff calculation.
                      Defaults to now (UTC).

        Returns:
            The cutoff datetime. Records older than this are expired.
        """
        ref = reference if reference is not None else datetime.now(UTC)
        return ref - self.retention_period

    def is_expired(self, record_timestamp: datetime, reference: datetime | None = None) -> bool:
        """Check if a record timestamp has exceeded the retention period.

        Args:
            record_timestamp: The timestamp of the audit record.
            reference: Reference point (default: now UTC).

        Returns:
            True if the record is older than the retention period.
        """
        cutoff = self.cutoff_timestamp(reference)
        return record_timestamp < cutoff

    def filter_expired(
        self,
        records: list[dict[str, Any]],
        timestamp_field: str = "ts",
        reference: datetime | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Split records into expired and retained sets.

        Args:
            records: List of record dicts, each containing a timestamp field.
            timestamp_field: The key name for the timestamp (default: 'ts').
            reference: Reference point (default: now UTC).

        Returns:
            Tuple of (expired_records, retained_records).
        """
        cutoff = self.cutoff_timestamp(reference)
        expired: list[dict[str, Any]] = []
        retained: list[dict[str, Any]] = []

        for record in records:
            ts = record.get(timestamp_field)
            if ts is not None and ts < cutoff:
                expired.append(record)
            else:
                retained.append(record)

        logger.info(
            "retention_filter_applied",
            total=len(records),
            expired=len(expired),
            retained=len(retained),
            cutoff_iso=cutoff.isoformat(),
        )

        return expired, retained

    def retention_query(self, table_name: str = "audit_chain", ts_column: str = "ts") -> str:
        """Generate a SQL DELETE query for expired records.

        Args:
            table_name: The audit table name (default: 'audit_chain').
            ts_column: The timestamp column name (default: 'ts').

        Returns:
            SQL DELETE statement with cutoff placeholder.
        """
        cutoff = self.cutoff_timestamp()
        return f"DELETE FROM {table_name} WHERE {ts_column} < '{cutoff.isoformat()}'"
