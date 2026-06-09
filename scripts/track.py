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
from datetime import date, datetime, timezone

from sqlalchemy import cast, select, String
from sqlalchemy.orm import selectinload

from core.db import SessionLocal, init_db
from core.models import Domain, ModelRun, Signal, SignalOutcome, User, UserSignalView

# Current production model. pnl/global default to this version only — mixing
# older versions (which had known calibration issues, see CLAUDE.md decision
# log) into the live scorecard would distort the read on the current model.
DEFAULT_VERSION = "poisson_v0.3.0"


def _parse_version_filter(version_arg: str | None) -> str | None:
    """Returns the model_version string to filter on, or None for all versions.

    None (no --version flag)   -> DEFAULT_VERSION (current model only)
    "all"                      -> None (no filter — every version)
    "v0.2.0" / "poisson_v0.2.0" -> "poisson_v0.2.0" (accepts either form)
    """
    if version_arg is None:
        return DEFAULT_VERSION
    if version_arg.lower() == "all":
        return None
    if not version_arg.startswith("poisson_"):
        return f"poisson_{version_arg}"
    return version_arg


def _version_header(version_filter: str | None) -> str:
    """Human-readable line describing which model version is in scope."""
    if version_filter is None:
        return "Model: all versions"
    if version_filter == DEFAULT_VERSION:
        return f"Model: {version_filter}  (use --version all to see all picks)"
    return f"Model: {version_filter}"

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
    p_pnl = sub.add_parser("pnl", help="show personal P&L summary")
    p_pnl.add_argument("--today", action="store_true",
                       help="show only picks for today")
    p_pnl.add_argument("--date", metavar="YYYY-MM-DD", default=None,
                       help="show only picks for a specific date")
    p_pnl.add_argument("--version", metavar="VERSION", default=None,
                       help="filter by model version (default: "
                            f"{DEFAULT_VERSION} only; 'all' for every version)")

    # global
    p_global = sub.add_parser("global",
                   help="show system-wide P&L across all resolved signals")
    p_global.add_argument("--version", metavar="VERSION", default=None,
                          help="filter by model version (default: "
                               f"{DEFAULT_VERSION} only; 'all' for every version)")

    args = parser.parse_args()
    init_db()

    with SessionLocal() as session:
        if args.cmd == "follow":
            _cmd_follow(session, args.signal_uuid, args.stake)
        elif args.cmd == "global":
            _cmd_global(session, version_filter=_parse_version_filter(args.version))
        else:
            # Resolve date filter: --today overrides --date
            filter_date: date | None = None
            if getattr(args, "today", False):
                filter_date = datetime.now(timezone.utc).date()
            elif getattr(args, "date", None):
                filter_date = date.fromisoformat(args.date)
            _cmd_pnl(session, filter_date=filter_date,
                     version_filter=_parse_version_filter(args.version))


# --------------------------------------------------------------------------- #
# follow                                                                       #
# --------------------------------------------------------------------------- #

def _cmd_follow(session, signal_uuid_str: str, stake: float) -> None:
    # -- resolve UUID or 8-char prefix ---------------------------------------
    raw = signal_uuid_str.strip()

    if len(raw) < 8:
        print("ERROR: UUID must be at least 8 characters.")
        sys.exit(1)

    if len(raw) == 36:
        # Full UUID — validate format and query exact.
        try:
            sig_id = uuid.UUID(raw)
        except ValueError:
            print(f"ERROR: '{raw}' is not a valid UUID.")
            sys.exit(1)
        signal = session.scalars(
            select(Signal)
            .where(Signal.id == sig_id)
            .options(selectinload(Signal.outcome))
        ).first()
        if signal is None:
            print(f"ERROR: No signal found matching '{raw}'.")
            sys.exit(1)
    else:
        # Prefix lookup — cast UUID column to text for LIKE query.
        # Works on both Postgres (uuid type) and SQLite (stored as text).
        matches = session.scalars(
            select(Signal)
            .where(cast(Signal.id, String).like(f"{raw}%"))
            .options(selectinload(Signal.outcome))
            .order_by(Signal.created_at.desc())
        ).all()

        if not matches:
            print(f"ERROR: No signal found matching '{raw}'.")
            sys.exit(1)

        if len(matches) > 1:
            print(f"\n  Multiple signals match '{raw}' — be more specific:\n")
            for m in matches:
                f = m.features
                print(f"    {m.id}  {f.get('match', '?')}  pick={f.get('pick', '?')}"
                      f"  date={m.valid_for_date}")
            print()
            sys.exit(1)

        signal = matches[0]

    f      = signal.features
    match  = f.get("match", "?")
    pick   = f.get("pick", "?")
    odd    = f.get("best_odd", "?")

    # -- hard stop for non-actionable signals (before any DB write) ----------
    if signal.status == "expired":
        print(f"ERROR: This pick has expired (game already started).")
        print(f"       Expired picks cannot be followed.")
        return

    if signal.status == "void":
        print(f"ERROR: This pick was voided (postponed / cancelled / data quality).")
        print(f"       Voided picks cannot be followed.")
        return

    if signal.status == "resolved":
        print(f"ERROR: This pick is already resolved.")
        outcome = session.scalars(
            select(SignalOutcome).where(SignalOutcome.signal_id == signal.id)
        ).first()
        if outcome:
            result = "✓ won" if outcome.was_correct else "✗ lost"
            print(f"       Result: {result} @ {odd}")
        print(f"       Resolved picks cannot be followed.")
        return

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

def _cmd_pnl(session, filter_date: date | None = None,
             version_filter: str | None = None) -> None:
    user = _get_or_create_user(session)

    stmt = (
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
    )
    needs_signal_join = filter_date is not None or version_filter is not None
    if needs_signal_join:
        stmt = stmt.join(Signal, UserSignalView.signal_id == Signal.id)
    if filter_date is not None:
        stmt = stmt.where(Signal.valid_for_date == filter_date)
    if version_filter is not None:
        stmt = (
            stmt.join(ModelRun, Signal.model_run_id == ModelRun.id)
            .where(ModelRun.model_version == version_filter)
        )

    views = session.scalars(stmt).all()

    if filter_date is not None and not views:
        print(f"\n  {_version_header(version_filter)}")
        print(f"  No followed picks for {filter_date}.\n")
        return

    if not views:
        print(f"\n  {_version_header(version_filter)}")
        print(f"  No followed picks match this filter.\n")
        return

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
    print(f"  {_version_header(version_filter)}")
    print()

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

def _cmd_global(session, version_filter: str | None = None) -> None:
    """System-wide P&L across all resolved betting signals.

    No user context required — this is the full model scorecard.

    Flat ROI: 1 unit staked per pick regardless of sizing.
      won:  profit = actual_value - 1  (actual_value is the decimal odd paid)
      lost: profit = -1
      void: profit = 0 (stake returned, excluded from win-rate calc)
      ROI  = net_units / resolved_count * 100

    Kelly ROI: uses kelly_units stored in signal.features.
      won:  profit = kelly_u * (actual_value - 1)
      lost: profit = -kelly_u
      void: profit = 0
      ROI  = net_kelly / total_staked_kelly * 100
    Signals without kelly_units (stored before 2026-06-03) fall back to 1.0u.

    version_filter: model_version to restrict to (None = all versions).
      Defaults to DEFAULT_VERSION at the CLI layer — mixing older model
      versions with known calibration issues into the live scorecard would
      distort the read on the current model (see CLAUDE.md decision log).
    """
    today = datetime.now(timezone.utc).date()

    # All betting signals, newest first — optionally restricted to one
    # model_version via the ModelRun join (jsonb-free, uses the existing FK).
    stmt = (
        select(Signal)
        .join(Domain, Signal.domain_id == Domain.id)
        .where(Domain.slug == "betting")
        .options(selectinload(Signal.outcome))
        .order_by(Signal.valid_for_date.desc(), Signal.created_at.desc())
    )
    if version_filter is not None:
        stmt = (
            stmt.join(ModelRun, Signal.model_run_id == ModelRun.id)
            .where(ModelRun.model_version == version_filter)
        )

    all_signals = session.scalars(stmt).all()

    resolved = [s for s in all_signals if s.status == "resolved"]
    voided   = [s for s in all_signals if s.status == "void"]
    pending  = [s for s in all_signals
                if s.status == "active" and s.valid_for_date <= today]
    won      = [s for s in resolved if s.outcome and s.outcome.was_correct]
    lost     = [s for s in resolved if s.outcome and not s.outcome.was_correct]

    # -- Flat ROI (1u/pick) ----------------------------------------------------
    net_flat = 0.0
    for s in resolved:
        if s.outcome:
            net_flat += (s.outcome.actual_value - 1.0) if s.outcome.was_correct else -1.0
    roi_flat = (net_flat / len(resolved) * 100) if resolved else 0.0

    # -- Kelly ROI (kelly_units from features, fallback 1.0u) ------------------
    net_kelly    = 0.0
    total_staked = 0.0
    kelly_fallback_count = 0
    for s in resolved:
        if not s.outcome:
            continue
        raw_ku = s.features.get("kelly_units")
        if raw_ku is None:
            kelly_fallback_count += 1
            kelly_u = 1.0
        else:
            kelly_u = float(raw_ku)
        total_staked += kelly_u
        if s.outcome.was_correct:
            net_kelly += kelly_u * (s.outcome.actual_value - 1.0)
        else:
            net_kelly -= kelly_u
    roi_kelly = (net_kelly / total_staked * 100) if total_staked > 0 else 0.0

    # -- header ----------------------------------------------------------------
    print(f"\n{'='*64}")
    print(f"  SENTINEL — System P&L (betting)")
    print(f"{'='*64}")
    print(f"  {_version_header(version_filter)}")
    print()
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
    def _s(v: float) -> str:
        return "+" if v >= 0 else ""

    print(f"  Flat ROI (1u/pick):   {_s(net_flat)}{net_flat:.2f}u  ({_s(roi_flat)}{roi_flat:.1f}%)")
    print(f"  Kelly ROI:            {_s(net_kelly)}{net_kelly:.2f}u  ({_s(roi_kelly)}{roi_kelly:.1f}%)"
          "  [based on suggested sizing]")
    print(f"  Total staked (Kelly): {total_staked:.1f}u")
    if kelly_fallback_count:
        print(f"  (Note: {kelly_fallback_count} pick(s) use 1u fallback — "
              "kelly_units not stored before 2026-06-03)")

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

    # -- breakdown by model version (only when showing all versions) -----------
    if version_filter is None:
        # Signal has no ORM relationship to ModelRun — resolve model_version
        # via a small id->version lookup rather than per-row lazy loads.
        run_ids = {s.model_run_id for s in resolved}
        version_by_run_id: dict[uuid.UUID, str] = dict(
            session.execute(
                select(ModelRun.id, ModelRun.model_version)
                .where(ModelRun.id.in_(run_ids))
            ).all()
        ) if run_ids else {}

        by_version: dict[str, list[Signal]] = {}
        for s in resolved:
            ver = version_by_run_id.get(s.model_run_id, "unknown")
            by_version.setdefault(ver, []).append(s)
        if by_version:
            print()
            print(f"  By model version:")
            for ver in sorted(by_version):
                v_resolved = by_version[ver]
                v_won = [s for s in v_resolved if s.outcome and s.outcome.was_correct]
                v_lost = [s for s in v_resolved if s.outcome and not s.outcome.was_correct]
                pct = (len(v_won) / len(v_resolved) * 100) if v_resolved else 0.0
                print(f"    {ver:<16} {len(v_resolved):>3} picks  "
                      f"{len(v_won)}W/{len(v_lost)}L  {pct:.1f}%")

    # -- last 10 resolved picks ------------------------------------------------
    last_10 = resolved[:10]   # already sorted newest-first
    if last_10:
        print()
        print(f"  Last {len(last_10)} resolved picks:")
        for s in last_10:
            f     = s.features
            match = f.get("match", "?")
            odd   = f.get("best_odd", "?")
            edge  = f.get("edge")
            units = f.get("kelly_units")
            icon     = "[W]" if (s.outcome and s.outcome.was_correct) else "[L]"
            odd_str  = f"@ {odd:.2f}" if isinstance(odd, float) else f"@ {odd}"
            units_str = f"  {units}u" if units is not None else ""
            edge_str  = f"  edge {edge:+.1%}" if isinstance(edge, float) else ""
            print(f"  {icon} {match:<44}  {units_str}{odd_str}{edge_str}")

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
