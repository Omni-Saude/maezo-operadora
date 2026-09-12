"""Trusted Q1 read ports. Shape never proves publisher, cursor custody or classification.

No concrete provider, storage, crypto or time-to-live policy is supplied here. Q2 must
qualify their current authority and invalidate cursors across every bound revision.
"""

from abc import ABC, abstractmethod
from typing import Literal, Self

from pydantic import Field, TypeAdapter, model_validator

from maezo.portal.contracts.models import HumanPrincipal, OpaqueRef, Revision, Sha256Digest, TaskSnapshot
from maezo.portal.contracts.queues import (
    ClosedRead,
    OpaqueCursor,
    PageLimit,
    QueueName,
    ReadErrorCode,
    TaskQueueRequest,
    UTCDateTime,
)

from .models import AuthoritativeTask, CurrentTaskAuthority, Scope


class ReadRefusalError(Exception):
    def __init__(self, code: ReadErrorCode) -> None:
        self.code: ReadErrorCode = TypeAdapter(ReadErrorCode).validate_python(code)
        super().__init__(self.code)


class CatalogTrustAnchor(ClosedRead):
    scope: Scope
    catalog_ref: OpaqueRef
    publisher_ref: OpaqueRef


class CatalogExpectation(ClosedRead):
    anchor: CatalogTrustAnchor
    catalog_revision: Revision
    catalog_digest: Sha256Digest
    source_observed_at: UTCDateTime
    valid_until: UTCDateTime


class QueueBinding(ClosedRead):
    scope: Scope
    principal: HumanPrincipal = Field(repr=False)
    queue: QueueName
    limit: PageLimit
    catalog_revision: Revision
    catalog_ref: OpaqueRef
    publisher_ref: OpaqueRef
    catalog_digest: Sha256Digest
    order: Literal["task_id_ascending"] = "task_id_ascending"


class CandidateWindow(ClosedRead):
    binding: QueueBinding
    task_ids: tuple[OpaqueRef, ...]
    source_observed_at: UTCDateTime
    valid_until: UTCDateTime
    next_cursor: OpaqueCursor | None

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if (
            self.task_ids != tuple(sorted(set(self.task_ids)))
            or len(self.task_ids) > self.binding.limit + 1
            or (len(self.task_ids) > self.binding.limit) != (self.next_cursor is not None)
        ):
            raise ValueError("invalid candidate window")
        return self


class CatalogExpectationSource(ABC):
    @abstractmethod
    async def current_catalog(self, *, anchor: CatalogTrustAnchor) -> CatalogExpectation:
        """Independently authenticate current publisher/designation/artifact, never discover()."""
        raise NotImplementedError


class HumanTaskQuery(ABC):
    scope: Scope

    @abstractmethod
    async def discover(
        self, principal: HumanPrincipal, request: TaskQueueRequest, *, expected_catalog: CatalogExpectation
    ) -> CandidateWindow:
        """Bounded eligible refs, including one lookahead; cursor binding is verified anew.

        Unsupported/unclassified refs, uncertain/partial windows and dependency errors
        refuse. No all-tenant scan, skipped forbidden row or synthetic empty result.
        Published references must have a qualified non-PHI opaque identity origin.
        """
        raise NotImplementedError


class CursorGrant(ClosedRead):
    cursor: OpaqueCursor = Field(repr=False)
    binding: QueueBinding
    after_task_id: OpaqueRef
    valid_until: UTCDateTime


class CursorCustody(ABC):
    scope: Scope

    @abstractmethod
    async def resolve(self, cursor: str, *, binding: QueueBinding) -> CursorGrant:
        """Authenticate confidential custody; malformed=400, proven stale binding=409."""
        raise NotImplementedError

    @abstractmethod
    def finalize(
        self, cursor: str, *, binding: QueueBinding, after_task_id: str, valid_until: UTCDateTime
    ) -> CursorGrant:
        """Synchronous, NO I/O: restrict preprepared custody to the final grant minimum.

        Q2 must prove custody enforces this ceiling on future resolve, not merely
        return a DTO echo. No await, storage/network I/O, renewal, crypto or fallback
        is implemented by Q1. An adapter unable to meet this contract must refuse.
        """
        raise NotImplementedError


class TaskDisclosureGrant(ClosedRead):
    scope: Scope
    principal: HumanPrincipal = Field(repr=False)
    snapshot: TaskSnapshot = Field(repr=False)
    authority_revision: Revision
    classification_ref: OpaqueRef
    classification_digest: Sha256Digest
    projection: Literal["full_task_detail.v1"]
    valid_until: UTCDateTime


class TaskDisclosureSource(ABC):
    scope: Scope

    @abstractmethod
    async def classify(
        self, principal: HumanPrincipal, task: AuthoritativeTask, authority: CurrentTaskAuthority
    ) -> TaskDisclosureGrant:
        """Authenticate reviewed classification and current ACL for ALL public fields.

        Includes assignee/groups and mandatory PAGTO evidence. No permissive boolean,
        inferred classification or dropping required evidence. Lives in the PHI zone.
        """
        raise NotImplementedError
