# MouFlossover : notes pour Claude Code

Lis aussi `/opt/CLAUDE.md` (règles communes à toutes les applis : français partout, pas de sudo, pas de secret sur GitHub, prévenir avant de pousser ou de redémarrer pendant un traitement).

## Ce que c'est
Appli Flask qui range les crossovers entre séries dans Emby. Dossier `/opt/mouflossover`, service `mouflossover.service`, port **8002**, déploiement automatique chaque nuit à 5 h (après un push depuis le serveur, redémarrer le service à la main). Réglages et secrets dans `data/` (`data/secrets.env`, jamais sur GitHub).

## Où trouver quoi
- `engine.py` : tout le métier (détection Sonarr + Wikipédia, plan, rangement, annulation). `wiki.py` : lecture de Wikipédia, base SQLite `data/mouflossover.db`.
- `app.py` : routes Flask. `auth.py`, `diag.py`, `settings_page.py`, `apparence.py` : mêmes rôles que dans MouFlanga (kit commun `ui/mou-*.css|js` identique dans toutes les applis).
- `ui/mouflossover.css|js`, `templates/` : l'accueil. `ui/skins/<nom>/` : skins (voir `docs/SKINS.md`).

## Règles qui comptent
- Un rangement renomme et copie de vrais fichiers de la médiathèque : toujours passer par `make_plan` (aperçu) puis `execute` ; chaque action est notée dans `data/manifests/` pour pouvoir l'annuler.
- Les copies sont des spéciaux `S00E100…` avec `airsbefore_season/episode` dans le NFO. Ne jamais supprimer un fichier : seulement renommer, copier, restaurer.
- Avant tout redémarrage : `curl -s http://127.0.0.1:8002/api/occupe` doit répondre `"occupe": false`.

## Tests
`./venv/bin/python -m unittest discover -s tests` (doit rester vert avant tout push). Journal : `journalctl -u mouflossover`.
