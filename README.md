# MouFlossover

**Range les crossovers entre séries dans Emby.** Quand une histoire se raconte sur plusieurs séries (Chicago Fire, puis Chicago Med, puis Chicago P.D., ou Grey's Anatomy et Station 19, ou les FBI…), chaque série doit contenir **toutes les parties, dans l'ordre**, avec leur titre, leur résumé et leur vignette. C'est indispensable pour tout regarder d'une traite, ou pour copier une saison entière sur une tablette hors connexion.

MouFlossover repère ces épisodes, prépare le rangement, te le montre, et ne fait rien sans ton clic.

## Ce que fait l'appli

1. **Je repère** : les titres de Sonarr (« Reckoning (1) », « (2) », « (3) ») et Wikipédia (pour chaque série de ta bibliothèque) servent à retrouver les épisodes d'un même crossover.
2. **Je prépare** : dans chaque série, l'épisode d'origine reçoit un repère de partie dans son nom (`part1(FIRE)`) et n'est plus surveillé dans Sonarr ; les parties des autres séries sont **copiées** comme épisodes spéciaux (`S00E21`, `S00E22`…) et placées juste avant ou après l'épisode d'origine, grâce aux balises de position des fiches NFO.
3. **Tu valides** : « Voir ce qui va changer » détaille chaque action. Rien n'est supprimé. Un journal est gardé : **« Annuler le traitement »** remet tout comme avant.

Les copies sont de vrais fichiers séparés (pas des liens), pour que chaque série reste autonome.

## Pages

- **Accueil** : crossovers « Prêts à organiser », « Bloqués » (avec la raison en français et quoi faire), « Terminés », « Mis de côté ».
- **⚙️ Réglages** : connexion à Sonarr, dossier des séries, analyse Wikipédia, redémarrage de l'appli.
- **🎨 Apparence** : skins et couleurs au choix, par compte (voir `docs/SKINS.md`). Fournis : *Violet cinéma*, *Océan*, *Papier*.
- **🩺 Journal** : rapport de diagnostic à copier-coller (clés masquées).

## Wikipédia en mémoire

Ce qui est lu sur Wikipédia est gardé dans une petite base SQLite (`data/mouflossover.db`) : les pages sont revérifiées au bout de 30 jours par leur numéro de révision, et seules les pages modifiées sont retéléchargées.

## Installation

Voir [INSTALL.md](INSTALL.md). Même méthode que les autres applis MouFl : dossier `/opt/mouflossover`, service systemd, mise à jour automatique chaque nuit à 5 h. Port **8002**.

## Prérequis

- Sonarr (adresse et clé API) ;
- le dossier des séries d'Emby accessible en écriture depuis le serveur ;
- Python 3.11.

## Version

Version affichée = base manuelle (`BASE_VERSION` dans `app.py`) + nombre de commits + hash court. Voir [CHANGELOG.md](CHANGELOG.md).

## Licence

MIT, voir [LICENSE](LICENSE).
