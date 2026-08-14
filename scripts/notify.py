"""
Notifications Discord via webhook (URL en secret GitHub Actions, jamais committee --
meme principe que strat_sim). Deux formats : evenement de trade, et resume fin de journee.
"""
import os
import requests

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")


def _post(content, embeds=None):
    if not WEBHOOK_URL:
        print("[notify] DISCORD_WEBHOOK_URL absent, notification non envoyee :")
        print(content)
        return
    payload = {"content": content}
    if embeds:
        payload["embeds"] = embeds
    resp = requests.post(WEBHOOK_URL, json=payload, timeout=15)
    if resp.status_code >= 300:
        print(f"[notify] Echec webhook Discord ({resp.status_code}) : {resp.text[:200]}")


def notify_entry(direction, fill, sl, tp1, tp2, contracts, bucket):
    emoji = "🔴" if direction == "short" else "🟢"
    label = "SHORT (fade HOD)" if direction == "short" else "LONG (fade LOD)"
    msg = (f"{emoji} **Entrée {label}**\n"
           f"Fill: `{fill:,.2f}` | SL: `{sl:,.2f}` | TP1: `{tp1:,.2f}` | TP2: `{tp2:,.2f}`\n"
           f"Contrats: `{contracts}` (bucket {bucket})")
    _post(msg)


def notify_exit(reason, price, pnl_usd, remaining_desc, equity_after, phase):
    emoji = "✅" if pnl_usd >= 0 else "❌"
    msg = (f"{emoji} **Sortie [{reason}]** ({remaining_desc})\n"
           f"Prix: `{price:,.2f}` | PnL: `{pnl_usd:+,.2f}$`\n"
           f"Équity: `{equity_after:,.2f}$` | Phase: `{phase}`")
    _post(msg)


def notify_special_event(event, state):
    if event == "FAIL_MLL":
        msg = (f"⚠️ **COMBINE ÉCHOUÉ** (Trailing Max Loss touché)\n"
               f"Tentative n°{state['n_combine_attempts']} — nouveau Combine relancé automatiquement à 50 000$")
    elif event == "PASS":
        msg = (f"🎉 **COMBINE RÉUSSI !** Passage en compte financé.\n"
               f"Comptes financés obtenus : {state['n_funded_accounts']}")
    elif event == "BLOW_NO_PAYOUT":
        msg = (f"⚠️ **Compte financé perdu** (avant tout payout)\n"
               f"Nouveau Combine relancé — tentative n°{state['n_combine_attempts']}")
    elif event == "BLOW_POST_PAYOUT":
        msg = (f"⚠️ **Compte financé perdu** (après {state['n_payouts']} payout(s) déjà touché(s))\n"
               f"Total payouts conservés : {state['total_payouts']:,.2f}$ — nouveau Combine relancé")
    else:
        return
    _post(msg)


def notify_payout(amount, state):
    msg = (f"💰 **PAYOUT reçu : {amount:,.2f}$**\n"
           f"Total cumulé : {state['total_payouts']:,.2f}$ ({state['n_payouts']} payouts)\n"
           f"Équity compte financé : {state['equity']:,.2f}$")
    _post(msg)


def notify_eod_summary(day_pnl, state, n_days_this_combine):
    phase_txt = "🎯 Combine en cours" if state["phase"] == "COMBINE" else "💵 Compte financé"
    lines = [
        f"📊 **Bilan de fin de journée** — {phase_txt}",
        f"PnL du jour : `{day_pnl:+,.2f}$`",
        f"Équity : `{state['equity']:,.2f}$`",
    ]
    if state["phase"] == "COMBINE":
        profit = state["equity"] - 50000.0
        eff_target = max(3000.0, state["best_day_profit"] / 0.50) if state["best_day_profit"] > 0 else 3000.0
        lines.append(f"Progression : `{profit:,.2f}$` / `{eff_target:,.2f}$` cible (jour {n_days_this_combine})")
        lines.append(f"Tentatives Combine depuis le début : `{state['n_combine_attempts']}`")
    else:
        lines.append(f"Jours gagnants avant prochain payout : `{state['win_days_funded']}/5`")
        lines.append(f"Payouts cumulés : `{state['total_payouts']:,.2f}$` ({state['n_payouts']})")
    _post("\n".join(lines))


def notify_error(message):
    _post(f"🛑 **Erreur pipeline Canopus** : {message}")
