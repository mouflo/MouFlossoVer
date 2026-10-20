# Journal des versions

La version affichée dans l'appli est `v1.0.N (hash)` : N est le nombre de commits depuis la version de base.

## [1.0] — 2026-10-20
- 🎨 **Interface commune, version 2** (identique dans les cinq applis) : les fenêtres et les bulles de message suivent les couleurs du skin choisi, les fenêtres plus hautes que l'écran défilent avec leurs boutons collés en bas, la touche « retour » du téléphone ferme la fenêtre ouverte au lieu de quitter l'appli, et le **logo de l'en-tête et l'icône de l'onglet changent de couleur avec le skin**.

## [1.0] — 2026-10-19
Première version installée comme les autres applis MouFl (service systemd, dépôt GitHub, mise à jour de nuit).

- Interface au style commun MouFl (en-tête, connexion, ⚙️ Réglages, 🩺 Journal) et **skins** (Violet cinéma, Océan, Papier) choisis par compte.
- Nouveau logo : deux flèches qui se croisent, **aux couleurs du skin en cours** (icône de l'onglet comprise).
- Fenêtre 🎨 Apparence : elle défile sur téléphone et le bouton Enregistrer reste visible en bas.
- Page Réglages : connexion à Sonarr (avec test), dossier des séries, analyse Wikipédia, redémarrage.
- Mise à jour automatique reportée tant qu'un rangement ou une analyse est en cours (`/api/occupe`).
- Sécurité : MouFlossover refuse de toucher à un fichier situé hors du dossier des séries réglé.

## Avant la version 1.0 (prototype en conteneur Docker, 2026-10-19)
- 0.3 : données Wikipédia gardées dans une base SQLite (seules les pages modifiées sont retéléchargées) ; interface expliquée en français ; plus de rafraîchissement permanent.
- 0.2 : analyse de Wikipédia pour toute la bibliothèque Sonarr ; interface web (aperçu du plan, rangement, annulation).
- 0.1 : détection des crossovers par les titres Sonarr « (n) » et une liste de référence ; copie des parties dans les autres séries en spéciaux, avec fiches NFO corrigées.
