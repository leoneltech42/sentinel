"""Investigation 2: is the pitcher-ERA adjustment under- or over-weighted in
the lambda calculation, relative to its actual predictive power?

NOTE on file location: the task description names
adapters/betting/mlb/model.py, but per CLAUDE.md the Phase 1 per-league
adapter restructure (adapters/betting/mlb/...) has NOT happened yet -- MLB
logic is still monolithic. The actual lambda formula lives in
adapters/betting/stats.py::mlb_lambdas(). Printed verbatim below.

Sensitivity test: holding HOME_ADVANTAGE at the current production value
(1.04 -- this script does not conflate the pending HA decision with this
one) and the season/recent RPG blend constant, raise the ERA ratio to the
power `era_exponent` instead of 1:
    lam *= (starter_era / league_avg_era) ** era_exponent
era_exponent=1 reproduces the current production formula exactly;
2 doubles its effective weight, 0.5 halves it.

Same reconstruction caveat as scripts/ha_resim.py: season/recent RPG
wasn't persisted for these 103 picks, so it's reconstructed point-in-time.
Pitcher ERA IS stored exactly -- no reconstruction needed for that input.

Read-only. Does not modify adapters/betting/stats.py or any signal data.

Usage:
    python -m scripts.era_weight_sensitivity
"""

from __future__ import annotations

import inspect
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select

from adapters.betting import stats as S
from adapters.betting.model import baseball_match_probs
from adapters.betting.stats import MLBStatsProvider
from core.db import SessionLocal, init_db
from core.models import ModelRun, Signal, SignalOutcome
from scripts.backtest import StatsCache
from scripts.ha_resim import _rpg_blend

MODEL_VERSION = "poisson_v0.3.0"
PROD_HA = 1.04  # held constant -- this investigation isolates ERA weight only
LEAGUE_AVG_ERA = 4.20
EXPONENTS = [0.5, 1.0, 2.0]
RECON_TOLERANCE = 0.02


def _print_formula() -> None:
    print("=" * 72)
    print("  adapters/betting/stats.py :: mlb_lambdas() -- current production formula")
    print("=" * 72)
    src = inspect.getsource(S.mlb_lambdas)
    print(src)


def _era_diff_and_tier(pick: str, home_team: str, away_team: str, home_era, away_era):
    if home_era is None or away_era is None:
        return None, None
    own, opp = (home_era, away_era) if pick == home_team else (away_era, home_era)
    diff = own - opp
    if diff < -1.99:
        tier = "strong"
    elif diff <= -1.05:
        tier = "moderate"
    else:
        tier = "weak"
    return diff, tier


def _lambdas(rpg_home, rpg_away, home_era, away_era, era_exponent: float):
    lam_home = rpg_home * PROD_HA
    lam_away = rpg_away
    if away_era and away_era > 0:
        lam_home *= (away_era / LEAGUE_AVG_ERA) ** era_exponent
    if home_era and home_era > 0:
        lam_away *= (home_era / LEAGUE_AVG_ERA) ** era_exponent
    return lam_home, lam_away


def main() -> None:
    _print_formula()

    init_db()
    with SessionLocal() as session:
        rows = session.execute(
            select(Signal, SignalOutcome)
            .join(SignalOutcome, SignalOutcome.signal_id == Signal.id)
            .join(ModelRun, Signal.model_run_id == ModelRun.id)
            .where(
                ModelRun.model_version == MODEL_VERSION,
                SignalOutcome.was_correct.is_not(None),
            )
            .order_by(Signal.valid_for_date)
        ).all()

    mlb_rows = [(s, o) for s, o in rows if (s.features or {}).get("sport", "").endswith("_mlb")]
    print(f"Resolved {MODEL_VERSION} picks: {len(rows)}  |  MLB: {len(mlb_rows)}\n")

    stats_cache = StatsCache()
    provider = MLBStatsProvider(season=2026)

    results = []
    excluded = []
    n = len(mlb_rows)
    for i, (signal, outcome) in enumerate(mlb_rows, 1):
        if i % 10 == 0 or i == n:
            print(f"  Re-simulating {i}/{n} ...", end="\r")

        f = signal.features
        home_team, away_team, pick = f.get("home_team"), f.get("away_team"), f.get("pick")
        home_era, away_era = f.get("home_pitcher_era"), f.get("away_pitcher_era")
        game_date = signal.valid_for_date
        stored_prob = float(f.get("model_probability", signal.confidence))

        if not home_team or not away_team or not pick:
            excluded.append((str(signal.id), "missing home/away/pick"))
            continue

        try:
            rpg_home = _rpg_blend(stats_cache, provider, home_team, game_date)
            rpg_away = _rpg_blend(stats_cache, provider, away_team, game_date)
        except Exception as exc:
            excluded.append((str(signal.id), f"RPG fetch failed: {exc}"))
            continue
        time.sleep(0.1)

        pick_is_home = pick == home_team
        era_diff, era_tier = _era_diff_and_tier(pick, home_team, away_team, home_era, away_era)

        recon_by_exp = {}
        flip_by_exp = {}
        for exp in EXPONENTS:
            lam_h, lam_a = _lambdas(rpg_home, rpg_away, home_era, away_era, exp)
            p_home, p_away = baseball_match_probs(lam_h, lam_a)
            recon_by_exp[exp] = p_home if pick_is_home else p_away
            flip_by_exp[exp] = (p_home >= p_away) != pick_is_home

        results.append({
            "signal_id": str(signal.id),
            "was_correct": int(outcome.was_correct),
            "era_tier": era_tier,
            "stored_prob": stored_prob,
            "recon": recon_by_exp,
            "flip": flip_by_exp,
            "noisy": abs(recon_by_exp[1.0] - stored_prob) > RECON_TOLERANCE,
        })

    print(f"\nRe-simulated: {len(results)}  |  Excluded: {len(excluded)}")
    for sid, reason in excluded:
        print(f"  excluded {sid}: {reason}")

    noisy_n = sum(r["noisy"] for r in results)
    if noisy_n:
        print(f"\n{noisy_n}/{len(results)} signal(s) have >{RECON_TOLERANCE} reconstruction drift "
              "at exponent=1 vs the stored value (RPG drift since generation time -- still "
              "included below).")

    def brier(rows_, exp_key):
        n_ = len(rows_)
        if n_ == 0:
            return float("nan")
        if exp_key == "stored":
            return sum((r["stored_prob"] - r["was_correct"]) ** 2 for r in rows_) / n_
        return sum((r["recon"][exp_key] - r["was_correct"]) ** 2 for r in rows_) / n_

    print(f"\n{'='*72}")
    print(f"  Brier score by ERA exponent ({len(results)} picks, HOME_ADVANTAGE held at {PROD_HA})")
    print(f"{'='*72}")
    print(f"  stored (production, exponent=1, HA=1.04 as originally run): {brier(results, 'stored'):.4f}")
    for exp in EXPONENTS:
        flips = sum(r["flip"][exp] for r in results)
        tag = "  <- current formula" if exp == 1.0 else ""
        print(f"  exponent={exp:>3}  (reconstructed): {brier(results, exp):.4f}   "
              f"flips={flips}/{len(results)}{tag}")

    print(f"\n{'='*72}")
    print("  Win rate and Brier by era_advantage_tier, per exponent")
    print(f"{'='*72}")
    tiers = ["strong", "moderate", "weak"]
    print(f"  {'tier':<10} {'n':>4} {'win_rate':>9} " + "  ".join(f"brier@{e}" for e in EXPONENTS))
    for tier in tiers:
        tier_rows = [r for r in results if r["era_tier"] == tier]
        if not tier_rows:
            continue
        n_t = len(tier_rows)
        win_rate = sum(r["was_correct"] for r in tier_rows) / n_t
        briers = [brier(tier_rows, exp) for exp in EXPONENTS]
        brier_str = "  ".join(f"{b:.4f}  " for b in briers)
        print(f"  {tier:<10} {n_t:>4} {win_rate:>9.1%}   {brier_str}")
    untiered = [r for r in results if r["era_tier"] is None]
    if untiered:
        print(f"  (no ERA tier -- pitcher not announced): n={len(untiered)}")

    # --- direction summary ---------------------------------------------- #
    b_half, b_one, b_double = brier(results, 0.5), brier(results, 1.0), brier(results, 2.0)
    print(f"\n{'='*72}")
    print("  SUMMARY")
    print(f"{'='*72}")
    print(f"  Brier: exponent=0.5 -> {b_half:.4f}   exponent=1 -> {b_one:.4f}   exponent=2 -> {b_double:.4f}")
    if b_double < b_one and b_double < b_half:
        print("  -> MORE ERA weight (exponent=2) improves Brier on this sample. Doubling the")
        print("     ERA adjustment's exponent moves in the right direction -- consistent with")
        print("     era_diff being the strongest single feature in the statistical report.")
    elif b_half < b_one and b_half < b_double:
        print("  -> LESS ERA weight (exponent=0.5) improves Brier on this sample -- the current")
        print("     formula may already be over-weighting ERA, or this sample is too noisy to")
        print("     tell direction reliably.")
    else:
        print("  -> exponent=1 (current production formula) is already the best of the three on")
        print("     this sample -- no clear sensitivity signal in either direction.")
    print(
        f"  n={len(results)} (and only the subset with both pitcher ERAs announced contributes\n"
        "  to the tiered breakdown) -- this is a sensitivity direction check only, not a\n"
        "  formula recommendation. era_diff being the strongest feature in the original report\n"
        "  doesn't by itself tell us the RIGHT exponent -- only that the current linear (^1)\n"
        "  weighting is worth testing against. No production code changed."
    )


if __name__ == "__main__":
    main()
