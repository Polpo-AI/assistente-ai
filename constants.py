"""Costanti di dominio condivise tra tutti i moduli Polpo AI."""

from enum import StrEnum


class DraftStatus(StrEnum):
    PENDING     = "pending"
    APPROVED    = "approved"
    SENDING     = "sending"
    SENT        = "sent"
    SEND_FAILED = "send_failed"
    IGNORED     = "ignored"


class ClassifiedBy(StrEnum):
    FILTER       = "filter"
    LLM          = "llm"
    LLM_FALLBACK = "llm_fallback"
    NONE         = "none"
