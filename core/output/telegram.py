"""Telegram output channel — sends picks and results to one or more chats.

Entirely generic: all domain data arrives via signal.features (a plain dict)
and the Signal ORM model. No betting-specific imports live here.

Phase 0: a single TELEGRAM_CHAT_ID drives a single-item chat_ids list.
Phase 1: pass chat_ids for every subscribed user — nothing else changes.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from core.models import Signal


def _esc(s: str) -> str:
    """Escape HTML special characters for Telegram parse_mode='HTML'."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------- #
# Message formatters (module-level so they're testable in isolation)          #
# --------------------------------------------------------------------------- #

def _format_picks(signals: list[Signal], for_date: date) -> str:
    lines: list[str] = [f"<b>&#9918; Sentinel picks &#8212; {for_date}</b>", ""]

    if not signals:
        lines.append("No +EV picks found for today.")
        return "\n".join(lines)

    for i, s in enumerate(signals, 1):
        f = s.features
        home = _esc(str(f.get("home_team", "?")))
        away = _esc(str(f.get("away_team", "?")))
        pick = _esc(str(f.get("pick", "?")))
        odd  = f.get("best_odd", 0)
        edge = float(f.get("edge", 0))
        ev   = float(s.expected_value)
        conf = float(s.confidence)
        lines.append(f"{i}. <b>{home} vs {away}</b>")
        lines.append(f"   Pick: <b>{pick}</b> @ {odd}")
        lines.append(f"   Edge: {edge:+.1%} | EV: {ev:+.1%} | Conf: {conf:.0%}")
        lines.append("")

    n = len(signals)
    lines.append(f"{n} pick{'s' if n != 1 else ''} today. "
                 "Paper trade only &#8212; track your results.")
    return "\n".join(lines)


def _format_results(signals: list[Signal], for_date: date) -> str:
    lines: list[str] = [f"<b>&#128202; Sentinel results &#8212; {for_date}</b>", ""]

    # Partition by status.
    display   = [s for s in signals if s.status in ("resolved", "void")]
    pending   = [s for s in signals if s.status == "active"]
    resolved  = [s for s in signals if s.status == "resolved"]
    won_count = sum(1 for s in resolved if s.outcome and s.outcome.was_correct)

    for i, s in enumerate(display, 1):
        f    = s.features
        home = _esc(str(f.get("home_team", "?")))
        away = _esc(str(f.get("away_team", "?")))
        pick = _esc(str(f.get("pick", "?")))

        if s.status == "void":
            lines.append(f"{i}. &#8709; <b>{pick}</b> ({home} vs {away})")
            lines.append("   Void")
        elif s.status == "resolved" and s.outcome is not None:
            icon = "&#10003;" if s.outcome.was_correct else "&#10007;"   # ✓ / ✗
            word = "Won" if s.outcome.was_correct else "Lost"
            hs   = s.outcome.outcome_metadata.get("home_score")
            as_  = s.outcome.outcome_metadata.get("away_score")
            score = f" {as_}-{hs}" if hs is not None and as_ is not None else ""
            lines.append(f"{i}. {icon} <b>{pick}</b> ({home} vs {away})")
            lines.append(f"   {word}{score}")
        lines.append("")

    if not display:
        lines.append("No picks resolved yet.")
        lines.append("")

    total_resolved = len(resolved)
    if total_resolved:
        win_rate = won_count / total_resolved
        lines.append(f"Day: {won_count}/{total_resolved} correct ({win_rate:.0%})")
    else:
        lines.append("Day: no resolved picks yet")

    lines.append(f"Pending: {len(pending)} pick{'s' if len(pending) != 1 else ''} "
                 "still unresolved")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Channel                                                                      #
# --------------------------------------------------------------------------- #

class TelegramChannel:
    """Sends Sentinel notifications to one or more Telegram chats.

    Args:
        bot_token: Telegram Bot API token (from @BotFather).
        chat_ids:  List of chat/user IDs to notify. Phase 0 passes a
                   single-item list; Phase 1 passes all subscribed users.
    """

    _BASE = "https://api.telegram.org"

    def __init__(self, bot_token: str, chat_ids: list[str]) -> None:
        self._token = bot_token
        self._chat_ids = chat_ids

    # -- public API --------------------------------------------------------- #

    def send_picks(self, signals: list[Signal], for_date: date) -> None:
        """Send today's +EV picks to every registered chat."""
        self._broadcast(_format_picks(signals, for_date))

    def send_results(self, signals: list[Signal], for_date: date) -> None:
        """Send resolution summary (won/lost/void) to every registered chat."""
        self._broadcast(_format_results(signals, for_date))

    # -- internals ---------------------------------------------------------- #

    def _broadcast(self, text: str) -> None:
        """Attempt delivery to every chat_id.

        Per-ID failures are logged as warnings and skipped so one bad chat
        does not block the rest. Raises RuntimeError if *all* IDs fail.
        """
        failed = 0
        for chat_id in self._chat_ids:
            try:
                self._send(chat_id, text)
            except Exception as exc:
                logging.warning(
                    "Telegram send failed for chat_id %s: %s", chat_id, exc
                )
                failed += 1

        if failed == len(self._chat_ids):
            raise RuntimeError(
                f"Telegram notification failed for all {len(self._chat_ids)} "
                "recipient(s). Check warnings above for details."
            )

    def _send(self, chat_id: str, text: str) -> None:
        url = f"{self._BASE}/bot{self._token}/sendMessage"
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        resp.raise_for_status()
