"""Minimal HOS Knowledge Service experiment."""

from .service import KnowledgeService, KnowledgeServiceError, RegistryError, StaleIndexError

__all__ = ["KnowledgeService", "KnowledgeServiceError", "RegistryError", "StaleIndexError"]
