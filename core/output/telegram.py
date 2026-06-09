"""Telegram output channel — sends picks and results to one or more chats.

Entirely generic: all domain data arrives via signal.features (a plain dict)
and the Signal ORM model. No betting-specific imports live here.

The `domain` parameter selects the header emoji and label — the only
domain-aware change permitted in core/output/ per CLAUDE.md. All domain-
specific field extraction falls back gracefully so this module never crashes
on an unknown domain.

Phase 0: a single TELEGRAM_CHAT_ID drives a single-item chat_ids list.
Phase 1: pass chat_ids for every subscribed user — nothing else changes.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from core.models import Signal


def _esc(s: str) -> str:
    """Escape HTML special characters for Telegram parse_mode='HTML'."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------- #
# Per-domain header config                                                     #
# --------------------------------------------------------------------------- #
# Maps domain slug -> (emoji HTML entity, label).
# This is the only place in core/output/ that is domain-aware.
_DOMAIN_HEADER: dict[str, tuple[str, str]] = {
    "betting": ("&#9918;", "Sentinel picks"),          # ⚾
    "flights": ("&#9992;", "Flight alert"),            # ✈
}
_DOMAIN_HEADER_DEFAULT = ("&#128204;", "Sentinel alert")  # 📌


def _header(domain: str, for_date: date) -> str:
    emoji, label = _DOMAIN_HEADER.get(domain, _DOMAIN_HEADER_DEFAULT)
    return f"<b>{emoji} {_esc(label)} &#8212; {for_date}</b>"


# --------------------------------------------------------------------------- #
# Message formatters (module-level so they're testable in isolation)          #
# --------------------------------------------------------------------------- #

def _format_picks(signals: list[Signal], for_date: date, domain: str = "betting") -> str:
    lines: list[str] = [_header(domain, for_date), ""]

    if not signals:
        lines.append("No picks / alerts found for today.")
        return "\n".join(lines)

    if domain == "flights":
        return _format_picks_flights(signals, for_date)

    # ---- Betting picks ----
    for i, s in enumerate(signals, 1):
        f = s.features
        home = _esc(str(f.get("home_team", "?")))
        away = _esc(str(f.get("away_team", "?")))
        pick = _esc(str(f.get("pick", "?")))
        odd   = f.get("best_odd", 0)
        edge  = float(f.get("edge", 0))
        ev    = float(s.expected_value)
        units = f.get("kelly_units")
        stars = f.get("star_rating", "")
        units_str = f"  {stars}  {units}u" if units is not None else ""
        lines.append(f"{i}. <b>{home} vs {away}</b>")
        lines.append(f"   Pick: {pick} @ {odd}{units_str}")
        lines.append(f"   Edge: {edge:+.1%} | EV: {ev:+.1%}")
        justification = f.get("justification")
        lines.append(f"<i>&#128161; {_esc(justification)}</i>" if justification else "")
        lines.append(f"   <code>python -m scripts.track follow {str(s.id)[:8]} </code>")
        lines.append("")

    n = len(signals)
    lines.append(f"{n} pick{'s' if n != 1 else ''} today. "
                 "Paper trade only &#8212; track your results.")
    lines.append("")
    lines.append("<i>1u = 1% of bankroll · 1/10 Kelly sizing</i>")
    return "\n".join(lines)


def _format_picks_flights(signals: list[Signal], for_date: date) -> str:
    """Telegram message for flight price alerts."""
    lines: list[str] = [_header("flights", for_date), ""]

    if not signals:
        lines.append("No flight price alerts today.")
        return "\n".join(lines)

    for i, s in enumerate(signals, 1):
        f = s.features
        origin = _esc(str(f.get("origin", "?")))
        dest = _esc(str(f.get("destination", "?")))
        dep_date = _esc(str(f.get("departure_date", "?")))
        price = f.get("price_usd", "?")
        airline = _esc(str(f.get("airline", "?")))
        stops = f.get("stops", "?")
        subtype = _esc(str(f.get("signal_subtype", "price_drop")))
        conf = float(s.confidence)
        avg = f.get("rolling_avg_price")

        lines.append(f"{i}. <b>{origin} &#8594; {dest}</b>  {dep_date}")
        lines.append(f"   Price: <b>${price}</b>  ({airline}, {stops} stop{'s' if stops != 1 else ''})")
        if avg:
            lines.append(f"   Avg: ${avg:.0f}  |  Signal: {subtype}  |  Conf: {conf:.0%}")
        else:
            lines.append(f"   Signal: {subtype}  |  Conf: {conf:.0%}")
        lines.append(f"   <code>{s.id}</code>")
        lines.append("")

    n = len(signals)
    lines.append(f"{n} flight alert{'s' if n != 1 else ''}. "
                 "Prices update daily &#8212; act within 7 days.")
    return "\n".join(lines)


def _format_results(signals: list[Signal], for_date: date, domain: str = "betting") -> str:
    lines: list[str] = [f"<b>&#128202; Sentinel results &#8212; {for_date}</b>", ""]

    # Partition by status.
    display   = [s for s in signals if s.status in ("resolved", "void")]
    pending   = [s for s in signals if s.status == "active"]
    resolved  = [s for s in signals if s.status == "resolved"]
    won_count = sum(1 for s in resolved if s.outcome and s.outcome.was_correct)

    for i, s in enumerate(display, 1):
        f    = s.features

        if domain == "flights":
            origin = _esc(str(f.get("origin", "?")))
            dest = _esc(str(f.get("destination", "?")))
            dep = _esc(str(f.get("departure_date", "?")))
            label = f"{origin}-{dest}  {dep}"
        else:
            home = _esc(str(f.get("home_team", "?")))
            away = _esc(str(f.get("away_team", "?")))
            pick = _esc(str(f.get("pick", "?")))
            label = f"<b>{pick}</b> ({home} vs {away})"

        if s.status == "void":
            lines.append(f"{i}. &#8709; {label}")
            lines.append("   Void")
        elif s.status == "resolved" and s.outcome is not None:
            icon = "&#10003;" if s.outcome.was_correct else "&#10007;"
            word = "Correct" if s.outcome.was_correct else "Wrong"
            if domain == "flights":
                pct = s.outcome.outcome_metadata.get("price_change_pct", "?")
                lines.append(f"{i}. {icon} {label}")
                lines.append(f"   {word}  (price {pct:+.1f}%)" if isinstance(pct, float) else f"   {word}")
            else:
                hs  = s.outcome.outcome_metadata.get("home_score")
                as_ = s.outcome.outcome_metadata.get("away_score")
                score = f" {as_}-{hs}" if hs is not None and as_ is not None else ""
                lines.append(f"{i}. {icon} {label}")
                lines.append(f"   {word}{score}")
        lines.append("")

    if not display:
        lines.append("No signals resolved yet.")
        lines.append("")

    total_resolved = len(resolved)
    if total_resolved:
        win_rate = won_count / total_resolved
        lines.append(f"Day: {won_count}/{total_resolved} correct ({win_rate:.0%})")
    else:
        lines.append("Day: no resolved signals yet")

    lines.append(f"Pending: {len(pending)} signal{'s' if len(pending) != 1 else ''} "
                 "still unresolved")
    return "\n".join(lines)


def _format_refresh(
    signals: list[Signal],
    for_date: date,
    prev_signals: dict[str, dict],
    followed_ids: set[uuid.UUID],
) -> str:
    """Afternoon refresh message: follow status + deltas for today's slate."""
    time_str = datetime.now(timezone.utc).strftime("%H:%M")
    lines: list[str] = [
        f"<b>&#128202; Sentinel refresh &#8212; {for_date}</b>",
        f"<i>{time_str} UTC update</i>",
        "",
    ]

    if not signals:
        lines.append("No active picks for today.")
        return "\n".join(lines)

    no_prev = not prev_signals

    for s in signals:
        f        = s.features
        pick     = _esc(str(f.get("pick", "?")))
        match    = _esc(str(f.get("match", "?")))
        # Build "pick vs opponent" label
        home     = f.get("home_team", "")
        away     = f.get("away_team", "")
        pick_val = f.get("pick", "")
        opponent = away if pick_val == home else home
        opp_str  = _esc(str(opponent)) if opponent else _esc(match)

        odd      = f.get("best_odd", "?")
        kelly    = f.get("kelly_units")
        stars    = f.get("star_rating", "")
        followed = s.id in followed_ids

        follow_icon = "&#128204;" if followed else "&#9898;"   # 📌 or ⚪
        units_str   = f"  {stars}  {kelly}u" if kelly is not None else ""
        odd_str     = f"{odd:.2f}" if isinstance(odd, float) else str(odd)

        lines.append(f"{follow_icon} {opp_str}")
        lines.append(f"   Pick: {pick} @ {odd_str}{units_str}")
        justification = f.get("justification")
        lines.append(f"<i>&#128161; {_esc(justification)}</i>" if justification else "")
        lines.append(f"<code>python -m scripts.track follow {str(s.id)[:8]} </code>")

        if no_prev:
            lines.append("No previous run to compare")
        else:
            raw_key = str(s.raw_event_id)
            snap    = prev_signals.get(raw_key)
            if snap is None:
                lines.append("&#128994; New pick")   # 🆕 approximation via green circle
            else:
                prev_odd   = snap.get("best_odd")
                prev_kelly = snap.get("kelly_units")
                parts: list[str] = []
                if (odd is not None and prev_odd is not None
                        and abs(float(odd) - float(prev_odd)) > 0.02):
                    icon = "&#128200;" if float(odd) > float(prev_odd) else "&#128201;"  # 📈 / 📉
                    parts.append(f"{icon} {prev_odd}&#8594;{odd_str}")
                if (kelly is not None and prev_kelly is not None
                        and abs(float(kelly) - float(prev_kelly)) > 0.2):
                    arrow = "&#8593;" if float(kelly) > float(prev_kelly) else "&#8595;"
                    parts.append(f"Kelly: {prev_kelly}u&#8594;{kelly}u {arrow}")
                if parts:
                    lines.append("  |  ".join(parts))
                # no changes → omit entirely (no text)
        lines.append("")

    followed_count  = sum(1 for s in signals if s.id in followed_ids)
    available_count = len(signals)
    lines.append(f"{followed_count} followed · {available_count} available")
    lines.append("<i>1u = 1% of bankroll · 1/10 Kelly sizing</i>")
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

    def send_picks(self, signals: list[Signal], for_date: date, domain: str = "betting") -> None:
        """Send today's picks/alerts to every registered chat."""
        self._broadcast(_format_picks(signals, for_date, domain=domain))

    def send_results(self, signals: list[Signal], for_date: date, domain: str = "betting") -> None:
        """Send resolution summary to every registered chat."""
        self._broadcast(_format_results(signals, for_date, domain=domain))

    def send_refresh(
        self,
        signals: list[Signal],
        for_date: date,
        prev_signals: dict[str, dict],
        followed_ids: set[uuid.UUID],
    ) -> None:
        """Send afternoon refresh with follow status and deltas."""
        self._broadcast(_format_refresh(signals, for_date, prev_signals, followed_ids))

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
