"""
Machine a etats du Combine TopStep 50K -> compte finance -> payouts, pour usage LIVE
(appelee a chaque evenement de trade avec l'etat persistant charge depuis state.json).
Regles identiques a celles validees dans les simulations (09_lifecycle.py/10_multi_firm.py).
"""
START_BALANCE = 50000.0
TARGET = 3000.0
TRAIL_COMBINE = 2000.0
DLL = 1000.0
CONSISTENCY = 0.50

FUNDED_TRAIL = 2000.0
PAYOUT_MIN = 125.0
PAYOUT_CAP_FIXED = 5000.0
PAYOUT_CAP_PCT = 0.50
SPLIT = 0.90
WIN_DAY_THRESH = 200.0
WIN_DAYS_REQUIRED = 5


def new_combine_state():
    return {
        "phase": "COMBINE", "equity": START_BALANCE, "highest_eod": START_BALANCE,
        "day_start_equity": START_BALANCE, "best_day_profit": 0.0, "n_days": 0,
        "dll_hit_today": False, "win_days_funded": 0, "cycle_base_funded": START_BALANCE,
        "floor_locked_funded": False, "total_payouts": 0.0, "n_payouts": 0,
        "n_combine_attempts": 1, "n_funded_accounts": 0,
    }


def on_new_day(state):
    """A appeler au tout debut de chaque nouvelle session de trading."""
    state["day_start_equity"] = state["equity"]
    state["dll_hit_today"] = False
    state["n_days"] += 1


def apply_pnl(state, pnl_usd):
    """Applique un PnL realise (evenement de trade). Retourne un evenement special
    ('FAIL_MLL','PASS','BLOW','FUNDED') ou None si rien de notable ne se declenche."""
    if state["phase"] == "COMBINE":
        if state["dll_hit_today"]:
            return None  # DLL soft : plus de nouveaux trades comptabilises aujourd'hui
        state["equity"] += pnl_usd
        floor = min(state["highest_eod"] - TRAIL_COMBINE, START_BALANCE)
        if (state["equity"] - state["day_start_equity"]) <= -DLL:
            state["dll_hit_today"] = True
        if state["equity"] <= floor:
            _reset_after_combine_fail(state)
            return "FAIL_MLL"
        return None

    elif state["phase"] == "FUNDED":
        state["equity"] += pnl_usd
        floor = (START_BALANCE if state["floor_locked_funded"]
                 else min(state["highest_eod"] - FUNDED_TRAIL, START_BALANCE))
        if state["equity"] <= floor:
            had_payout = state["n_payouts"] > 0
            _reset_after_funded_blow(state)
            return "BLOW_POST_PAYOUT" if had_payout else "BLOW_NO_PAYOUT"
        return None


def end_of_day(state):
    """A appeler en fin de session (15:55 NY). Verifie PASS (Combine) et met a jour le
    plancher trailing. Retourne un evenement ('PASS', 'FUNDED') ou None."""
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
            state["floor_locked_funded"] = False
            return "PASS"
        state["highest_eod"] = max(state["highest_eod"], state["equity"])
        return None

    elif state["phase"] == "FUNDED":
        if day_pnl > WIN_DAY_THRESH:
            state["win_days_funded"] += 1
        if not state["floor_locked_funded"]:
            state["highest_eod"] = max(state["highest_eod"], state["equity"])
            if state["highest_eod"] - FUNDED_TRAIL >= START_BALANCE:
                state["floor_locked_funded"] = True
        return None


def check_payout(state):
    """A appeler apres end_of_day si phase FUNDED. Retourne le montant verse au trader
    (float) si un payout se declenche, sinon None."""
    if state["phase"] != "FUNDED":
        return None
    cycle_avail = state["equity"] - state["cycle_base_funded"]
    if state["win_days_funded"] >= WIN_DAYS_REQUIRED and cycle_avail >= PAYOUT_MIN:
        payout_gross = min(cycle_avail, PAYOUT_CAP_FIXED, PAYOUT_CAP_PCT * state["equity"])
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
