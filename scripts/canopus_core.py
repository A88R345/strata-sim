"""
Coeur de la strategie Canopus, restructure pour un traitement INCREMENTAL (une barre 5min
a la fois, a partir d'un etat persistant) plutot que batch. Formules identiques a
lib_pipeline.py (audite le 13/08) -- LOOKBACK=10, K_HOD/K_LOD papier section 4.3,
hmm_adj +-12%/+-6%, buffer TP2=0.15, SL=50pts, BE=60%, TRAIL=75%/35%.
"""
import numpy as np

LOOKBACK = 10
SL_POINTS = 50
SLIP = 0.5
PROX_BUFFER = 0.15
TP2_BUFFER = 0.15
BE_THRESH = 0.60
TRAIL_THRESH = 0.75
TRAIL_DIST = 0.35
RANGE_PETIT = 0.8
CONTRACT_CAP = 5  # cap reel verifie le plus conservateur (cf. discussion) ; 5 ou 8 -> memes resultats en pratique
K_HOD = {"sideways": 0.90, "bull": 0.73, "bear": 1.19}
K_LOD = {"sideways": 0.92, "bull": 0.95, "bear": 0.79}
HMM_ADJ_MAG = {"bull_fort": 0.12, "bear_fort": 0.12, "bull_soft": 0.06, "bear_soft": 0.06, "sideways": 0.0}
MIN_PRIOR_REGIME = 25
SESSION_START = "03:00"
SESSION_END = "15:55"
ROLL_HOUR_NY = 23


def ou_fit(m):
    m = np.asarray(m, dtype=float)
    x, y = m[:-1], np.diff(m)
    if len(x) < 3 or np.std(x) == 0:
        return float(np.mean(m)), 0.0, float(np.std(m)) if len(m) > 1 else 0.0, 0.0
    beta, alpha = np.polyfit(x, y, 1)
    theta = -beta
    resid = y - (alpha + beta * x)
    sigma_eps = float(np.std(resid, ddof=2)) if len(resid) > 2 else float(np.std(resid))
    ss_res, ss_tot = float(np.sum(resid ** 2)), float(np.sum((y - np.mean(y)) ** 2))
    r2 = max(0.0, min(1.0, 1 - ss_res / ss_tot)) if ss_tot > 0 else 0.0
    if theta <= 0.05:
        return float(np.mean(m)), 0.0, sigma_eps, 0.0
    return float(alpha / theta), float(theta), sigma_eps, r2


def classify_regime(trend_score, soft=0.002, fort=0.005):
    if trend_score is None:
        return None
    if trend_score > fort: return "bull_fort"
    if trend_score > soft: return "bull_soft"
    if trend_score < -fort: return "bear_fort"
    if trend_score < -soft: return "bear_soft"
    return "sideways"


def compute_todays_zone(recent_sessions, ratio_hist, current_open):
    """recent_sessions : liste triee de dicts (>=LOOKBACK+1 elements), chacun avec
    open/hod/lod/park_var/ewma_var/hod_move/lod_move/open_ret.
    Retourne le dict de zone pour la session EN COURS (dont on connait deja l'open)."""
    if len(recent_sessions) < LOOKBACK:
        raise ValueError(f"Pas assez d'historique ({len(recent_sessions)}/{LOOKBACK} sessions)")

    opens = [s["open"] for s in recent_sessions]
    open_rets = [None] + [opens[i] / opens[i - 1] - 1 for i in range(1, len(opens))]
    mu_short = np.mean([r for r in open_rets[-3:] if r is not None]) if len(open_rets) >= 3 else None
    mu_med = np.mean([r for r in open_rets[-LOOKBACK:] if r is not None]) if len(open_rets) >= LOOKBACK else None
    trend_score = 0.6 * mu_short + 0.4 * mu_med if (mu_short is not None and mu_med is not None) else None
    regime5 = classify_regime(trend_score)
    if regime5 is None:
        raise ValueError("Regime indetermine (pas assez d'historique de rendements)")
    regime3 = {"bull_fort": "bull", "bull_soft": "bull", "bear_fort": "bear", "bear_soft": "bear", "sideways": "sideways"}[regime5]

    prior = recent_sessions[-LOOKBACK:]
    hod_moves = [s["hod_move"] for s in prior]
    lod_moves = [s["lod_move"] for s in prior]
    mu_hod_ou, th_h, sig_h, r2_h = ou_fit(hod_moves)
    mu_lod_ou, th_l, sig_l, r2_l = ou_fit(lod_moves)
    mu_hod = r2_h * mu_hod_ou + (1 - r2_h) * np.mean(hod_moves)
    mu_lod = r2_l * mu_lod_ou + (1 - r2_l) * np.mean(lod_moves)
    ou_var_hod = (sig_h ** 2) / (2 * th_h) if th_h > 0 else float(np.var(hod_moves))
    ou_var_lod = (sig_l ** 2) / (2 * th_l) if th_l > 0 else float(np.var(lod_moves))

    hist_hod = ratio_hist[regime3]["hod"]
    hist_lod = ratio_hist[regime3]["lod"]
    k_hod = float(np.median(hist_hod)) if len(hist_hod) >= MIN_PRIOR_REGIME else K_HOD[regime3]
    k_lod = float(np.median(hist_lod)) if len(hist_lod) >= MIN_PRIOR_REGIME else K_LOD[regime3]

    park_vals = [s["park_var"] for s in prior]
    ewma_vals = [s["ewma_var"] for s in prior]
    range_vals = [s["hod"] - s["lod"] for s in prior]
    park_ratio = prior[-1]["park_var"] / np.mean(park_vals) if np.mean(park_vals) else 1.0
    ewma_ratio = prior[-1]["ewma_var"] / np.mean(ewma_vals) if np.mean(ewma_vals) else 1.0
    range_ratio_prior = range_vals[-1] / np.mean(range_vals) if np.mean(range_vals) else 1.0
    vol_ratio = float(np.clip(0.6 * park_ratio + 0.4 * ewma_ratio, 0.5, 2.0))

    hod_s1 = current_open + mu_hod * k_hod
    lod_s1 = current_open - mu_lod * k_lod
    hmm_mag = HMM_ADJ_MAG.get(regime5, 0.0)
    is_bull, is_bear = "bull" in regime5, "bear" in regime5
    adj_hod = (1 + hmm_mag) if is_bull else ((1 - hmm_mag) if is_bear else 1.0)
    adj_lod = (1 - hmm_mag) if is_bull else ((1 + hmm_mag) if is_bear else 1.0)
    hod_s2 = hod_s1 + np.sqrt(max(ou_var_hod, 0)) * vol_ratio * adj_hod
    lod_s2 = lod_s1 - np.sqrt(max(ou_var_lod, 0)) * vol_ratio * adj_lod

    return {
        "open": current_open, "hod_s1": hod_s1, "hod_s2": hod_s2, "lod_s1": lod_s1, "lod_s2": lod_s2,
        "regime5": regime5, "regime3": regime3, "range_ratio_prior": range_ratio_prior,
        "k_hod": k_hod, "k_lod": k_lod, "_mu_hod": mu_hod, "_mu_lod": mu_lod,
    }


def check_entry(zone, bar, bars_last10_ranges, close_3bars_ago, hod_sig_done, lod_sig_done,
                 hod_s2_touched, lod_s2_touched):
    """bar: dict Open/High/Low/Close de la barre courante. Retourne (direction, fill_price) ou None."""
    prox_hod = zone["hod_s1"] - PROX_BUFFER * (zone["hod_s2"] - zone["hod_s1"])
    prox_lod = zone["lod_s1"] + PROX_BUFFER * (zone["lod_s1"] - zone["lod_s2"])
    avg_range10 = np.mean(bars_last10_ranges) if bars_last10_ranges else None
    move3 = abs(bar["Close"] - close_3bars_ago) if close_3bars_ago is not None else 0.0
    momentum_ok = (move3 <= 2 * avg_range10) if avg_range10 else True

    if not hod_sig_done and momentum_ok and bar["High"] >= prox_hod and bar["Close"] < zone["hod_s1"]:
        return "short", zone["hod_s1"] - SLIP
    if not lod_sig_done and momentum_ok and bar["Low"] <= prox_lod and bar["Close"] > zone["lod_s1"]:
        return "long", zone["lod_s1"] + SLIP
    return None


def new_position(direction, sigma1, fill, open_px, zone):
    tp2 = (open_px + TP2_BUFFER * (zone["hod_s2"] - zone["hod_s1"])) if direction == "short" \
        else (open_px - TP2_BUFFER * (zone["lod_s1"] - zone["lod_s2"]))
    sl = sigma1 + SL_POINTS if direction == "short" else sigma1 - SL_POINTS
    tp1 = (fill + tp2) / 2
    return {
        "dir": direction, "sigma1": sigma1, "fill": fill, "tp2": tp2, "tp1": tp1,
        "tp_dist": abs(fill - tp2), "stop": sl, "tp1_done": False, "be": False,
        "trail_active": False, "best": fill, "contracts": None,  # contracts fixe au moment de l'entree par combine_state
    }


def manage_position(pos, bar):
    """Retourne (event, price, remaining_closed) ou (None, None, None) si rien ne se passe.
    event in {"SL","BE","TRAIL","TP2","TP1_PARTIAL","EOD"}."""
    d = pos["dir"]
    pos["best"] = min(pos["best"], bar["Low"]) if d == "short" else max(pos["best"], bar["High"])

    stop = pos["stop"]
    if (d == "short" and bar["High"] >= stop) or (d == "long" and bar["Low"] <= stop):
        reason = "TRAIL" if pos["trail_active"] else ("BE" if pos["be"] else "SL")
        return reason, stop, "ALL"

    if (d == "short" and bar["Low"] <= pos["tp2"]) or (d == "long" and bar["High"] >= pos["tp2"]):
        return "TP2", pos["tp2"], "ALL"

    if not pos["tp1_done"]:
        hit = (d == "short" and bar["Low"] <= pos["tp1"]) or (d == "long" and bar["High"] >= pos["tp1"])
        if hit:
            pos["tp1_done"] = True
            pos["stop"] = pos["fill"]
            pos["be"] = True
            return "TP1_PARTIAL", pos["tp1"], "HALF"

    if not pos["be"]:
        fav = (pos["fill"] - bar["Close"]) if d == "short" else (bar["Close"] - pos["fill"])
        if fav >= BE_THRESH * pos["tp_dist"]:
            pos["stop"] = pos["fill"]
            pos["be"] = True

    if pos["tp1_done"]:
        fav_best = (pos["fill"] - pos["best"]) if d == "short" else (pos["best"] - pos["fill"])
        if fav_best >= TRAIL_THRESH * pos["tp_dist"]:
            pos["trail_active"] = True
        if pos["trail_active"]:
            new_stop = pos["best"] + TRAIL_DIST * pos["tp_dist"] if d == "short" else pos["best"] - TRAIL_DIST * pos["tp_dist"]
            pos["stop"] = min(pos["stop"], new_stop) if d == "short" else max(pos["stop"], new_stop)

    return None, None, None


def sizing_bucket(s2_touched, range_ratio_prior):
    small = range_ratio_prior < RANGE_PETIT
    free = not s2_touched
    if free and small: return "2x", 0.01
    if free or small: return "1.5x", 0.0075
    return "base", 0.005
