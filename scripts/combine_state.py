"""
Machine a etats du Combine LucidFlex 50K -> compte finance -> payouts, pour usage LIVE
(appelee a chaque evenement de trade avec l'etat persistant charge depuis state.json).
Regles verifiees pour LucidFlex specifiquement (pas TopStep) :
  - Eval : pas de Daily Loss Limit, MLL EOD 2000$, consistency 50% (eval uniquement)
  - Finance : pas de consistency, pas de DLL, MLL EOD 2000$ qui NE SE VERROUILLE JAMAIS
    (contrairement a TopStep/Tradeify -- risque de trailing permanent chez LucidFlex)
  - Payout : seuil jour gagnant 150$ (compte 50K), 5 jours requis, minimum 500$,
    plafond 50% du profit du cycle jusqu'a 2000$ max, split 90/10
"""
START_BALANCE = 50000.0
TARGET = 3000.0
TRAIL_COMBINE = 2000.0
CONSISTENCY = 0.50
# Pas de DLL chez LucidFlex (contrairement a TopStep) -- concept retire entierement

FUNDED_TRAIL = 2000.0
PAYOUT_MIN = 500.0
PAYOUT_CAP_FIXED = 2000.0
PAYOUT_CAP_PCT = 0.50
SPLIT = 0.90
WIN_DAY_THRESH = 150.0
WIN_DAYS_REQUIRED = 5


def new_combine_state():
    return {
        "phase": "COMBINE", "equity": START_BALANCE, "highest_eod": START_BALANCE,
        "day_start_equity": START_BALANCE, "best_day_profit": 0.0, "n_days": 0,
        "win_days_funded": 0, "cycle_base_funded": START_BALANCE,
        "total_payouts": 0.0, "n_payouts": 0,
        "n_combine_attempts": 1, "n_funded_accounts": 0,
    }


def on_new_day(state):
    """A appeler au tout debut de chaque nouvelle session de trading."""
    state["day_start_equity"] = state["equity"]
    state["n_days"] += 1


def apply_pnl(state, pnl_usd):
    """Applique un PnL realise (evenement de trade). Retourne un evenement special
    ('FAIL_MLL','PASS','BLOW_NO_PAYOUT','BLOW_POST_PAYOUT') ou None."""
    if state["phase"] == "COMBINE":
        state["equity"] += pnl_usd
        floor = min(state["highest_eod"] - TRAIL_COMBINE, START_BALANCE)
        if state["equity"] <= floor:
            _reset_after_combine_fail(state)
            return "FAIL_MLL"
        return None

    elif state["phase"] == "FUNDED":
        state["equity"] += pnl_usd
        # LucidFlex : le trailing suit le plus haut atteint EN PERMANENCE, jamais fige.
        floor = state["highest_eod"] - FUNDED_TRAIL
        if state["equity"] <= floor:
            had_payout = state["n_payouts"] > 0
            _reset_after_funded_blow(state)
            return "BLOW_POST_PAYOUT" if had_payout else "BLOW_NO_PAYOUT"
        return None


def end_of_day(state):
    """A appeler en fin de session (15:55 NY). Retourne un evenement ('PASS') ou None."""
    day_pnl = state["equity"] - state["day_start_equity"]

    if state["phase"] == "COMBINE":
        state["best_day_profit"] = max(state["best_day_profit"], day_pnl)
        profit = state["equity"] - START_BALANCE
        eff_target = max(TARGET, state["best_day_profit"] / CONSISTENCY) if state["best_day_profit"] > 0 else TARGET
        if profit >= eff_target:
            state["phase"] = "FUNDED"
            state["n_funded_accounts"] += 1
            state["highest_eod"] = state["equity"]
            state["win_days_funded"] = 0
            state["cycle_base_funded"] = state["equity"]
            return "PASS"
        state["highest_eod"] = max(state["highest_eod"], state["equity"])
        return None

    elif state["phase"] == "FUNDED":
        if day_pnl > WIN_DAY_THRESH:
            state["win_days_funded"] += 1
        state["highest_eod"] = max(state["highest_eod"], state["equity"])
        return None


def check_payout(state):
    """A appeler apres end_of_day si phase FUNDED. Retourne le montant verse au trader
    (float) si un payout se declenche, sinon None."""
    if state["phase"] != "FUNDED":
        return None
    cycle_avail = state["equity"] - state["cycle_base_funded"]
    if state["win_days_funded"] >= WIN_DAYS_REQUIRED and cycle_avail >= PAYOUT_MIN:
        payout_gross = min(cycle_avail * PAYOUT_CAP_PCT, PAYOUT_CAP_FIXED)
        if payout_gross >= PAYOUT_MIN:
            trader_share = payout_gross * SPLIT
            state["equity"] -= payout_gross
            state["cycle_base_funded"] = state["equity"]
            state["win_days_funded"] = 0
            state["total_payouts"] += trader_share
            state["n_payouts"] += 1
            return trader_share
    return None


def _reset_after_combine_fail(state):
    state["phase"] = "COMBINE"
    state["equity"] = START_BALANCE
    state["highest_eod"] = START_BALANCE
    state["best_day_profit"] = 0.0
    state["n_combine_attempts"] += 1


def _reset_after_funded_blow(state):
    state["phase"] = "COMBINE"
    state["equity"] = START_BALANCE
    state["highest_eod"] = START_BALANCE
    state["best_day_profit"] = 0.0
    state["n_combine_attempts"] += 1
