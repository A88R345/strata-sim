"""
Point d'entree principal, appele a chaque execution du workflow GitHub Actions.
Traite toute nouvelle barre 5min depuis la derniere execution, gere position/Combine,
notifie Discord, sauvegarde l'etat (commite par le workflow).

Garde fuseau horaire + jours ouvres (pattern strat_sim) : on ne traite que les barres
NY, et on ne considere "nouvelle session" que via la regle de bascule 23:00 NY --
le filtrage weekend est naturel (pas de nouvelles barres du vendredi 17h au dimanche 18h ET).
"""
import json
import sys
from pathlib import Path
from datetime import time as dtime

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import canopus_core as cc
import combine_state as cs
import notify
from data_fetch import fetch_recent_5min, DataFetchError

STATE_DIR = Path(__file__).parent.parent / "state"
STATE_FILE = STATE_DIR / "state.json"
SEED_FILE = STATE_DIR / "seed_from_backtest.json"


def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    with open(SEED_FILE) as f:
        seed = json.load(f)
    return {
        "combine": cs.new_combine_state(),
        "recent_sessions": seed["recent_sessions"],
        "ratio_hist": seed["ratio_hist"],
        "last_processed_ts": None,
        "current_session": None,  # session en cours de formation (open, running hod/lod, bars, zone, flags)
        "open_position": None,
        "eod_sent_for": None,  # derniere session_date pour laquelle le resume EOD a ete envoye
    }


def save_state(state):
    STATE_DIR.mkdir(exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=1, default=str)


def parkinson_var_running(bars):
    import numpy as np
    h = np.array([b["High"] for b in bars])
    l = np.array([b["Low"] for b in bars])
    valid = (h > 0) & (l > 0) & (h >= l)
    if valid.sum() == 0:
        return 0.0
    r = np.log(h[valid] / l[valid]) ** 2
    return float(r.sum() / (4 * len(r) * np.log(2)))


def finalize_session(state, sess):
    """Cloture la session en cours : calcule ses stats finales, les ajoute a
    recent_sessions, met a jour ratio_hist (walk-forward, no-lookahead : utilise le
    mu_hod/mu_lod DEJA fige au moment ou la zone de CETTE session avait ete calculee)."""
    hod = max(b["High"] for b in sess["bars"])
    lod = min(b["Low"] for b in sess["bars"])
    park_var = parkinson_var_running(sess["bars"])
    record = {
        "session_date": sess["session_date"], "open": sess["open"], "hod": hod, "lod": lod,
        "park_var": park_var, "ewma_var": sess["bars"][-1].get("ewma_var", park_var),
        "hod_move": hod - sess["open"], "lod_move": sess["open"] - lod,
        "open_ret": None,
    }
    state["recent_sessions"].append(record)
    state["recent_sessions"] = state["recent_sessions"][-90:]  # fenetre glissante raisonnable

    if sess.get("zone"):
        reg = sess["zone"]["regime3"]
        mu_hod, mu_lod = sess["zone"].get("_mu_hod"), sess["zone"].get("_mu_lod")
        if mu_hod and abs(mu_hod) > 1e-6:
            state["ratio_hist"][reg]["hod"].append(record["hod_move"] / mu_hod)
        if mu_lod and abs(mu_lod) > 1e-6:
            state["ratio_hist"][reg]["lod"].append(record["lod_move"] / mu_lod)


def process_bar(state, ts_ny, bar):
    t = ts_ny.time()
    sess_date = ts_ny.date().isoformat()
    if t >= dtime(cc.ROLL_HOUR_NY, 0):
        sess_date = (ts_ny.date() + pd.Timedelta(days=1)).isoformat()

    cur = state["current_session"]
    if cur is None or cur["session_date"] != sess_date:
        if cur is not None:
            finalize_session(state, cur)
        state["current_session"] = {
            "session_date": sess_date, "open": bar["Open"], "bars": [], "zone": None,
            "hod_sig": False, "lod_sig": False, "hod_s2_touch": False, "lod_s2_touch": False,
        }
        cur = state["current_session"]
        cs.on_new_day(state["combine"])

    cur["bars"].append(bar)

    signal_window = dtime(3, 0) <= t <= dtime(15, 55)
    post_close_window = dtime(15, 55) <= t < dtime(cc.ROLL_HOUR_NY, 0)  # borne avant le rollover 23:00

    if cur["zone"] is None and signal_window and len(state["recent_sessions"]) >= cc.LOOKBACK:
        try:
            zone = cc.compute_todays_zone(state["recent_sessions"], state["ratio_hist"], cur["open"])
            cur["zone"] = zone
        except ValueError as e:
            print(f"[warn] zone non calculee : {e}")

    pos = state["open_position"]
    if pos is not None:
        event, price, remaining = cc.manage_position(pos, bar)
        if event:
            pnl_pts = (pos["fill"] - price) if pos["dir"] == "short" else (price - pos["fill"])
            n_contracts = pos["contracts"] if remaining == "ALL" else max(1, pos["contracts"] // 2)
            pnl_usd = pnl_pts * 2.0 * n_contracts - 0.62 * n_contracts
            combine_event = cs.apply_pnl(state["combine"], pnl_usd)
            desc = "clôture totale" if remaining == "ALL" else f"{n_contracts} contrat(s) (partiel)"
            notify.notify_exit(event, price, pnl_usd, desc, state["combine"]["equity"], state["combine"]["phase"])
            if combine_event:
                notify.notify_special_event(combine_event, state["combine"])
            if remaining == "ALL":
                state["open_position"] = None
            else:
                pos["contracts"] -= n_contracts

    if t >= dtime(15, 55) and t < dtime(cc.ROLL_HOUR_NY, 0) and pos is not None:
        pnl_pts = (pos["fill"] - bar["Close"]) if pos["dir"] == "short" else (bar["Close"] - pos["fill"])
        pnl_usd = pnl_pts * 2.0 * pos["contracts"] - 0.62 * pos["contracts"]
        combine_event = cs.apply_pnl(state["combine"], pnl_usd)
        notify.notify_exit("EOD", bar["Close"], pnl_usd, "clôture forcée EOD", state["combine"]["equity"], state["combine"]["phase"])
        if combine_event:
            notify.notify_special_event(combine_event, state["combine"])
        state["open_position"] = None
        pos = None

    if pos is None and cur["zone"] is not None and signal_window:
        recent_bars = cur["bars"][-11:-1]
        ranges = [b["High"] - b["Low"] for b in recent_bars] if len(recent_bars) >= 10 else []
        close_3ago = cur["bars"][-4]["Close"] if len(cur["bars"]) >= 4 else None
        sig = cc.check_entry(cur["zone"], bar, ranges, close_3ago, cur["hod_sig"], cur["lod_sig"],
                              cur["hod_s2_touch"], cur["lod_s2_touch"])
        if bar["High"] >= cur["zone"]["hod_s2"]:
            cur["hod_s2_touch"] = True
        if bar["Low"] <= cur["zone"]["lod_s2"]:
            cur["lod_s2_touch"] = True
        if sig:
            direction, fill = sig
            if direction == "short":
                cur["hod_sig"] = True
                touched = cur["hod_s2_touch"]
            else:
                cur["lod_sig"] = True
                touched = cur["lod_s2_touch"]
            bucket, risk_pct = cc.sizing_bucket(touched, cur["zone"]["range_ratio_prior"])
            contracts = min(cc.CONTRACT_CAP, max(1, round(state["combine"]["equity"] * risk_pct / (cc.SL_POINTS * 2.0))))
            pos = cc.new_position(direction, cur["zone"]["hod_s1"] if direction == "short" else cur["zone"]["lod_s1"],
                                   fill, cur["zone"]["open"], cur["zone"])
            pos["contracts"] = contracts
            state["open_position"] = pos
            notify.notify_entry(direction, fill, pos["stop"], pos["tp1"], pos["tp2"], contracts, bucket)

    if post_close_window and state["eod_sent_for"] != sess_date:
        day_pnl = state["combine"]["equity"] - state["combine"]["day_start_equity"]
        cs_event = cs.end_of_day(state["combine"])
        if cs_event:
            notify.notify_special_event(cs_event, state["combine"])
        payout = cs.check_payout(state["combine"])
        if payout:
            notify.notify_payout(payout, state["combine"])
        notify.notify_eod_summary(day_pnl, state["combine"], state["combine"]["n_days"])
        state["eod_sent_for"] = sess_date


def main():
    state = load_state()
    try:
        df = fetch_recent_5min()
    except DataFetchError as e:
        notify.notify_error(f"Fetch données échoué : {e}")
        sys.exit(1)

    df.index = df.index.tz_convert("America/New_York")
    last_ts = state.get("last_processed_ts")
    if last_ts:
        df = df[df.index > pd.Timestamp(last_ts).tz_convert("America/New_York")]

    if df.empty:
        print("Aucune nouvelle barre depuis la dernière exécution.")
        return

    new_day_started = False
    for ts_ny, row in df.iterrows():
        bar = {"Open": float(row["Open"]), "High": float(row["High"]), "Low": float(row["Low"]), "Close": float(row["Close"])}
        process_bar(state, ts_ny, bar)
        state["last_processed_ts"] = ts_ny.isoformat()

    save_state(state)
    print(f"Traitement termine. {len(df)} barres traitees. Derniere : {df.index[-1]}")


if __name__ == "__main__":
    main()
