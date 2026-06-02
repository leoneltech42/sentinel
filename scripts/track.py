"""Phase 0 personal paper-trading tracker.

Usage:
    python -m scripts.track follow <signal_uuid> <stake>
    python -m scripts.track pnl
    python -m scripts.track global

Commands:
    follow  Record a paper-trade follow with a given stake amount.
    pnl     Print personal P&L summary for all followed picks.
    global  Show system-wide P&L across all resolved signals (no user needed).

Env (see .env.example):
    SENTINEL_USER_ID     UUID of your user row (printed on first run)
    SENTINEL_USER_EMAIL  Email used when creating the user row
                         (default: phase0@sentinel.local)

Phase 1 note: SENTINEL_USER_ID is a Phase 0 shortcut. Replace
_get_or_create_user() with a real auth lookup (Supabase Auth / JWT)
without touching the rest of this file.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from core.db import SessionLocal, init_db
from core.models import Domain, Signal, SignalOutcome, User, UserSignalView

# Ensure Unicode output (tick marks, currency symbols) works on Windows
# terminals that default to cp1252.  errors='replace' prevents hard crashes
# if a character cannot be encoded; the fallback is a '?' in the output.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# Entry point                                                                  #
# --------------------------------------------------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="track",
        description="Sentinel Phase 0 personal paper-trade tracker",
    )
    sub = parser.add_subparsers(dest="cmd", required=True,
                                metavar="{follow,pnl,global}")

    # follow
    p_follow = sub.add_parser("follow",
                               help="record a paper-trade follow with a stake")
    p_follow.add_argument("signal_uuid",
                          help="UUID of the signal (from `paper_trade` output)")
    p_follow.add_argument("stake", type=float,
                          help="paper stake amount (e.g. 10 for $10)")

    # pnl
    sub.add_parser("pnl", help="show personal P&L summary")

    # global
    sub.add_parser("global",
                   help="show system-wide P&L across all resolved signals")

    args = parser.parse_args()
    init_db()

    with SessionLocal() as session:
        if args.cmd == "follow":
            _cmd_follow(session, args.signal_uuid, args.stake)
        elif args.cmd == "global":
            _cmd_global(session)
        else:
            _cmd_pnl(session)


# --------------------------------------------------------------------------- #
# follow                                                                       #
# --------------------------------------------------------------------------- #

def _cmd_follow(session, signal_uuid_str: str, stake: float) -> None:
    # -- parse UUID ----------------------------------------------------------
    try:
        sig_id = uuid.UUID(signal_uuid_str)
    except ValueError:
        print(f"ERROR: '{signal_uuid_str}' is not a valid UUID.")
        sys.exit(1)

    # -- look up signal ------------------------------------------------------
    signal = session.scalars(
        select(Signal)
        .where(Signal.id == sig_id)
        .options(selectinload(Signal.outcome))
    ).first()

    if signal is None:
        print(f"ERROR: Signal {sig_id} not found.")
        sys.exit(1)

    f      = signal.features
    match  = f.get("match", "?")
    pick   = f.get("pick", "?")
    odd    = f.get("best_odd", "?")

    # -- status warnings -----------------------------------------------------
    if signal.status == "resolved":
        if signal.outcome:
            tag = "[W]" if signal.outcome.was_correct else "[L]"
        else:
            tag = "[?]"
        print(f"[WARN] This pick is already resolved ({tag}). Recording anyway.")
    elif signal.status == "void":
        print("[WARN] This pick is void (postponed / cancelled). Recording anyway.")

    # -- print signal details for confirmation -------------------------------
    print(f"\n  Signal found: {match}")
    print(f"  Pick:         {pick} @ {odd}  |  "
          f"EV: {signal.expected_value:+.1%}  |  "
          f"Conf: {signal.confidence:.0%}")
    print(f"  Stake:        ${stake:.2f}\n")

    # -- resolve user --------------------------------------------------------
    user = _get_or_create_user(session)

    # -- upsert UserSignalView (no duplicates) --------------------------------
    view = session.scalars(
        select(UserSignalView).where(
            UserSignalView.user_id  == user.id,
            UserSignalView.signal_id == signal.id,
        )
    ).first()

    if view is not None:
        prev_stake = view.stake
        view.stake    = stake
        view.followed = True
        print(f"  Updated existing follow (stake: ${prev_stake:.2f} -> ${stake:.2f}).")
    else:
        session.add(UserSignalView(
            user_id=user.id,
            signal_id=signal.id,
            followed=True,
            stake=stake,
        ))

    session.commit()
    print("[OK] Pick followed. Good luck!\n")


# --------------------------------------------------------------------------- #
# pnl                                                                          #
# --------------------------------------------------------------------------- #

def _cmd_pnl(session) -> None:
    user = _get_or_create_user(session)

    views = session.scalars(
        select(UserSignalView)
        .where(
            UserSignalView.user_id   == user.id,
            UserSignalView.followed.is_(True),
        )
        .options(
            selectinload(UserSignalView.signal)
            .selectinload(Signal.outcome)
        )
        .order_by(UserSignalView.viewed_at.desc())
    ).all()

    # -- partition by status -------------------------------------------------
    resolved = [v for v in views if v.signal.status == "resolved"]
    voided   = [v for v in views if v.signal.status == "void"]
    pending  = [v for v in views if v.signal.status == "active"]
    won      = [v for v in resolved
                if v.signal.outcome and v.signal.outcome.was_correct]

    # -- aggregate figures ---------------------------------------------------
    total_staked = sum(v.stake or 0.0 for v in views)

    # Settled P&L: resolved rows have pnl set by orchestrator; void = 0 (stake back).
    settled_pnl = sum(
        (v.pnl or 0.0) if v.signal.status == "resolved" else 0.0
        for v in resolved + voided
    )
    total_return = total_staked + settled_pnl
    pct_return   = (settled_pnl / total_staked * 100) if total_staked else 0.0

    # -- header --------------------------------------------------------------
    print(f"\n{'='*64}")
    print(f"  SENTINEL - Personal P&L")
    print(f"{'='*64}")

    print(f"  Followed picks: {len(views)}")

    void_note = f", {len(voided)} void" if voided else ""
    print(f"  Resolved:       {len(resolved)}  ({len(pending)} pending{void_note})")

    if resolved:
        print(f"  Won:            {len(won)} / {len(resolved)}  "
              f"({len(won)/len(resolved):.0%})")
    else:
        print(f"  Won:            -- / --  (no results yet)")

    print()
    print(f"  Total staked:   ${total_staked:.2f}")
    print(f"  Total return:   ${total_return:.2f}")
    sign = "+" if settled_pnl >= 0 else ""
    print(f"  Net P&L:        {sign}${settled_pnl:.2f}  ({sign}{pct_return:.1f}%)")

    # -- recent picks (last 10) ----------------------------------------------
    if views:
        print()
        print(f"  Recent picks:")
        for v in views[:10]:
            sig   = v.signal
            f     = sig.features
            match = f.get("match", "?")
            pick  = f.get("pick", "?")
            odd   = f.get("best_odd", "?")
            stake = v.stake or 0.0
            label = f"{match} - {pick}"

            if sig.status == "resolved" and sig.outcome is not None:
                icon = "[W]" if sig.outcome.was_correct else "[L]"
                pnl  = v.pnl or 0.0
                s    = "+" if pnl >= 0 else ""
                print(f"  {icon} {label:<54}  "
                      f"${stake:.0f} -> {s}${pnl:.2f}  (@ {odd})")
            elif sig.status == "void":
                print(f"  [/] {label:<54}  "
                      f"${stake:.0f} -> void  (@ {odd})")
            else:
                print(f"  [P] {label:<54}  "
                      f"${stake:.0f} -> pending")

    print(f"{'='*64}\n")


# --------------------------------------------------------------------------- #
# global                                                                       #
# --------------------------------------------------------------------------- #

def _cmd_global(session) -> None:
    """System-wide P&L across all resolved betting signals.

    No user context required — this is the full model scorecard.
    Net units assumes 1 unit staked per pick:
      won:  profit = actual_value - 1  (actual_value is the odd paid)
      lost: profit = -1
      void: profit = 0 (stake returned, excluded from win-rate calc)
    ROI = net_units / resolved_count * 100
    """
    today = datetime.now(timezone.utc).date()

    # All betting signals, newest first
    all_signals = session.scalars(
        select(Signal)
        .join(Domain, Signal.domain_id == Domain.id)
        .where(Domain.slug == "betting")
        .options(selectinload(Signal.outcome))
        .order_by(Signal.valid_for_date.desc(), Signal.created_at.desc())
    ).all()

    resolved = [s for s in all_signals if s.status == "resolved"]
    voided   = [s for s in all_signals if s.status == "void"]
    pending  = [s for s in all_signals
                if s.status == "active" and s.valid_for_date <= today]
    won      = [s for s in resolved if s.outcome and s.outcome.was_correct]
    lost     = [s for s in resolved if s.outcome and not s.outcome.was_correct]

    # Net units (1u/pick): void counts as 0 (stake back)
    net_units = 0.0
    for s in resolved:
        if s.outcome:
            net_units += (s.outcome.actual_value - 1.0) if s.outcome.was_correct else -1.0

    roi = (net_units / len(resolved) * 100) if resolved else 0.0

    # -- header ----------------------------------------------------------------
    print(f"\n{'='*64}")
    print(f"  SENTINEL — System P&L (betting)")
    print(f"{'='*64}")
    print(f"  Generated picks:   {len(all_signals)}")

    void_note = f", {len(voided)} void" if voided else ""
    print(f"  Resolved:          {len(resolved)}  ({len(pending)} pending{void_note})")

    if resolved:
        print(f"  Won:               {len(won)} / {len(resolved)}  "
              f"({len(won) / len(resolved):.1%})")
    else:
        print(f"  Won:               -- / --  (no results yet)")

    # -- units + ROI -----------------------------------------------------------
    print()
    unit_sign = "+" if net_units >= 0 else ""
    roi_sign  = "+" if roi >= 0 else ""
    print(f"  Net units (1u/pick):  {unit_sign}{net_units:.2f}")
    print(f"  ROI (1u/pick):        {roi_sign}{roi:.1f}%")

    # -- confidence bands ------------------------------------------------------
    bands = [
        ("50–60%", 0.50, 0.60),
        ("60–70%", 0.60, 0.70),
        ("70%+",        0.70, 1.01),
    ]
    print()
    print(f"  By confidence band:")
    for label, lo, hi in bands:
        band_res = [s for s in resolved if lo <= s.confidence < hi]
        band_won = [s for s in band_res if s.outcome and s.outcome.was_correct]
        if band_res:
            pct = len(band_won) / len(band_res) * 100
            print(f"    {label}:  {pct:.1f}%  correct  (n={len(band_res)})")
        else:
            print(f"    {label}:  --  (n=0)")

    # -- last 10 resolved picks ------------------------------------------------
    last_10 = resolved[:10]   # already sorted newest-first
    if last_10:
        print()
        print(f"  Last {len(last_10)} resolved picks:")
        for s in last_10:
            f    = s.features
            match = f.get("match", "?")
            odd   = f.get("best_odd", "?")
            edge  = f.get("edge")
            icon  = "[W]" if (s.outcome and s.outcome.was_correct) else "[L]"
            odd_str  = f"@ {odd:.2f}" if isinstance(odd, float) else f"@ {odd}"
            edge_str = f"  edge {edge:+.1%}" if isinstance(edge, float) else ""
            print(f"  {icon} {match:<44}  {odd_str}{edge_str}")

    print(f"{'='*64}\n")


# --------------------------------------------------------------------------- #
# User helper                                                                  #
# --------------------------------------------------------------------------- #

def _get_or_create_user(session) -> User:
    """Return the Phase 0 user, creating one the first time if needed.

    Resolution order:
      1. SENTINEL_USER_ID env var (fastest path after first run).
      2. Existing user with matching SENTINEL_USER_EMAIL.
      3. Create a fresh user, print UUID, ask the user to set .env.

    Phase 1 replacement: swap this function body for a real auth lookup.
    """
    user_id_str = os.getenv("SENTINEL_USER_ID", "").strip()
    email       = os.getenv("SENTINEL_USER_EMAIL", "phase0@sentinel.local").strip()

    # 1. Try by explicit UUID.
    if user_id_str:
        try:
            user = session.get(User, uuid.UUID(user_id_str))
            if user:
                return user
            print(f"[WARN] SENTINEL_USER_ID {user_id_str!r} not in DB — "
                  "falling back to email lookup.")
        except ValueError:
            print(f"[WARN] SENTINEL_USER_ID {user_id_str!r} is not a valid UUID — "
                  "falling back to email lookup.")

    # 2. Try by email (idempotent if .env not yet updated after first run).
    existing = session.scalars(
        select(User).where(User.email == email)
    ).first()
    if existing:
        if not user_id_str:
            # First run: let the operator know what to put in .env.
            print(f"\n  [INFO] Phase 0 user found by email.")
            print(f"  Add to .env:  SENTINEL_USER_ID={existing.id}\n")
        return existing

    # 3. Create a new user.
    domain = session.scalars(
        select(Domain).where(Domain.slug == "betting")
    ).first()
    if domain is None:
        print("ERROR: betting domain not found. "
              "Run `python -m scripts.paper_trade` at least once first.")
        sys.exit(1)

    user = User(email=email, plan="paid", domain_id=domain.id)
    session.add(user)
    session.flush()
    session.commit()   # commit immediately so the row survives read-only callers
    print(f"\n  [INFO] Created Phase 0 user: {user.id}")
    print(f"  Add to .env:  SENTINEL_USER_ID={user.id}")
    print(f"  email: {email}  plan: paid\n")
    return user


if __name__ == "__main__":
    main()
