"""
Recupere les dernieres barres 5min NQ via yfinance. Retry + refuse de retourner des
donnees partielles/absentes plutot que de laisser le reste du pipeline tourner sur du
NaN -- meme principe que strat_sim (refuser d'ecrire/poster si un prix manque).
"""
import time
import pandas as pd
import yfinance as yf

TICKER = "NQ=F"  # E-mini Nasdaq-100 continuous futures (yfinance). MNQ=F si dispo/prefere.
MAX_RETRIES = 3
RETRY_DELAY_S = 15


class DataFetchError(Exception):
    pass


def fetch_recent_5min(period="5d"):
    """Retourne un DataFrame 5min (colonnes Open/High/Low/Close/Volume, index UTC-aware),
    ou leve DataFetchError si echec apres retries. Ne retourne JAMAIS un df partiel/NaN."""
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            df = yf.Ticker(TICKER).history(period=period, interval="5m", auto_adjust=False)
            if df is None or df.empty:
                raise DataFetchError(f"yfinance a retourne un DataFrame vide (tentative {attempt})")
            df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
            if df.isna().any().any():
                bad = df[df.isna().any(axis=1)]
                raise DataFetchError(f"{len(bad)} barre(s) avec valeur(s) manquante(s), refus de continuer")
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            else:
                df.index = df.index.tz_convert("UTC")
            return df
        except Exception as e:
            last_err = e
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_S)
    raise DataFetchError(f"Echec apres {MAX_RETRIES} tentatives : {last_err}")


if __name__ == "__main__":
    df = fetch_recent_5min()
    print(f"{len(df)} barres recuperees, {df.index.min()} -> {df.index.max()}")
    print(df.tail(3))
