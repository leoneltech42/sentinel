"""Output layer — sends Sentinel notifications via configured channels.

Current Phase 0 implementation: Telegram only (single chat_id).
Phase 1 extension: add EmailChannel, push, etc. by calling them here;
paper_trade.py never needs to know which channels are active.

Both functions silently skip if TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID
are absent — this keeps --mock and no-creds local runs clean.

The optional `domain` parameter drives the emoji/label in the picks message
(the only domain-aware change permitted in core/output/ per CLAUDE.md).
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.models import Signal


def notify_picks(signals: list[Signal], for_date: date, domain: str = "betting") -> None:
    """Send today's picks/alerts to all configured notification channels.

    Silently skips if TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not set.
    Callers should catch exceptions if a notification failure must not abort
    the parent process (paper_trade.py wraps this in try/except).
    """
    _telegram_picks(signals, for_date, domain)


def notify_results(signals: list[Signal], for_date: date, domain: str = "betting") -> None:
    """Send resolution summary to all configured notification channels.

    Silently skips if TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not set.
    """
    _telegram_results(signals, for_date, domain)


def notify_refresh(
    signals: list[Signal],
    for_date: date,
    prev_signals: dict[str, dict],
    followed_ids: set[uuid.UUID],
) -> None:
    """Send afternoon refresh (follow status + deltas) to all configured channels.

    Silently skips if TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not set.
    """
    _telegram_refresh(signals, for_date, prev_signals, followed_ids)


# --------------------------------------------------------------------------- #
# Internal channel dispatchers                                                #
# --------------------------------------------------------------------------- #

def _telegram_picks(signals: list[Signal], for_date: date, domain: str) -> None:
    token, chat_id = _telegram_creds()
    if not token or not chat_id:
        logging.debug("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set — skipping notification")
        return
    from core.output.telegram import TelegramChannel
    TelegramChannel(token, [chat_id]).send_picks(signals, for_date, domain=domain)


def _telegram_results(signals: list[Signal], for_date: date, domain: str) -> None:
    token, chat_id = _telegram_creds()
    if not token or not chat_id:
        logging.debug("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set — skipping notification")
        return
    from core.output.telegram import TelegramChannel
    TelegramChannel(token, [chat_id]).send_results(signals, for_date, domain=domain)


def _telegram_refresh(
    signals: list[Signal],
    for_date: date,
    prev_signals: dict[str, dict],
    followed_ids: set[uuid.UUID],
) -> None:
    token, chat_id = _telegram_creds()
    if not token or not chat_id:
        logging.debug("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set — skipping notification")
        return
    from core.output.telegram import TelegramChannel
    TelegramChannel(token, [chat_id]).send_refresh(
        signals, for_date, prev_signals=prev_signals, followed_ids=followed_ids
    )


def _telegram_creds() -> tuple[str, str]:
    """Return (bot_token, chat_id), both empty strings if not configured."""
    return (
        os.getenv("TELEGRAM_BOT_TOKEN", ""),
        os.getenv("TELEGRAM_CHAT_ID", ""),
    )
