# canopus-sim

Logging de signaux Canopus en temps quasi-reel (aucune execution reelle d'ordres),
simulant un Combine TopStep 50K jusqu'a un compte finance et ses payouts.

## Fonctionnement
- GitHub Actions tourne toutes les 5 min (cron UTC), une garde Python filtre sur
  l'heure reelle NY + jours ouvres avant de lancer quoi que ce soit
- `scripts/run.py` recupere les nouvelles barres 5min (yfinance, `NQ=F`) depuis la
  derniere execution, met a jour les zones (walk-forward), verifie les signaux,
  gere toute position ouverte, avance la machine a etats Combine/Finance, notifie
  Discord, sauvegarde l'etat dans `state/state.json` (commite automatiquement)
- Notifications : une par evenement de trade (entree/sortie), une resume en fin de
  journee de trading (15:55 NY)

## Mise en route
1. `pip install -r requirements.txt`
2. Creer un webhook Discord (Parametres du salon -> Integrations -> Webhooks)
3. L'ajouter en secret GitHub Actions : `DISCORD_WEBHOOK_URL`
4. `state/seed_from_backtest.json` amorce l'historique (calibration walk-forward
   figee au 2025-09-03, 60 dernieres sessions) -- au premier run, `state/state.json`
   est cree a partir de ce seed
5. Activer le workflow (`workflow_dispatch` pour tester manuellement avant le cron)

## Limites connues / a surveiller
- yfinance : donnees 5min limitees a une fenetre glissante recente (pas un souci ici,
  on ne recupere que le delta depuis la derniere execution, jamais tout l'historique)
- Cap contrats fixe a 5 (le plus conservateur des deux lectures possibles des regles
  TopStep -- verifie ne rien changer aux resultats vs 8, cf. discussion)
- Bucket de sizing (2x/1.5x/base) : le signe de l'effet range_ratio reste non
  explique (documente dans le dossier de reference) -- le code implemente la regle
  du papier telle quelle, pas une version "corrigee"
- Montant exact du trailing sur compte finance suppose identique au Combine (2000$),
  jamais reconfirme independamment
- Premier run reel = premier vrai test de bout en bout ; attendu que des ajustements
  soient necessaires (comme pour tout systeme qui passe du backtest au live)
