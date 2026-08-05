"""Transactional persistence for processed RouteFrom traces."""

from .postgres import PersistenceResult, persist_processed_trace

__all__ = ["PersistenceResult", "persist_processed_trace"]
