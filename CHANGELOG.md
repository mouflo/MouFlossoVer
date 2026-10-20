# Journal des versions

La version affichée dans l'appli est `v1.0.N (hash)` : N est le nombre de commits depuis la version de base.

## [1.0] — 2026-10-20 (audit)
- 📼 **Saison que tu ne gardes plus** : quand un crossover touche une saison que tu n'as plus (tu ne gardes que les saisons pas encore vues), l'appli te demande si tu veux quand même l'ajouter dans cette série. « Seulement dans les autres séries » : l'épisode sert juste de source, puis il est retiré, sans garder de dossier de saison.
- 🛡️ **Rangement sûr** : en cas d'erreur en cours de route, tout est remis comme avant automatiquement ; une copie n'apparaît sous son nom qu'une fois complète ; une annulation qui rencontre un problème ne perd plus le journal et peut être relancée.
- 🔗 **Plus de crossover « perdu »** : quand une nouvelle partie est détectée ou qu'un épisode invité s'ajoute, le crossover garde son rangement, sa correction et sa mise de côté.
- 🩹 Une copie dont Emby a remplacé le titre est reconnue (à sa taille) et son titre est remis, au lieu d'être copiée une deuxième fois.
- Un double appui sur « Organiser » ne lance plus deux rangements ; « Rétablir la détection automatique » ne bloque plus le défilement de la page.

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
