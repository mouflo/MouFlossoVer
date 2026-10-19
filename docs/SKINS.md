# 🎨 Créer un skin pour MouFlossover

Un **skin** change l'apparence de MouFlossover (couleurs, polices, arrondis, fonds…) sans toucher au fonctionnement.
Il tient dans **un dossier** avec **un fichier CSS**. Pas de JavaScript, pas de modification des pages.

## Ce que voit l'utilisateur
Menu **🎨 Apparence** (en haut de la page) : choisir « Style d'origine » ou un skin de la liste, puis régler 4 couleurs
(fond, cartes, texte, accent). Le choix est enregistré **par compte**. Le style d'origine est toujours disponible.

## Créer un skin
1. Copie le dossier `ui/skins/papier/` sous un nouveau nom (lettres minuscules, chiffres et tirets : `ui/skins/sakura/`).
2. Modifie `skin.json` : `nom`, `auteur`, `description` (une phrase).
3. Écris ton CSS dans `skin.css`.

### La méthode simple : les variables
Le style commun (`ui/mou-ui.css`) utilise des variables. Les redéfinir suffit à changer toute l'appli :

| Variable | Rôle |
|---|---|
| `--bg` | fond de la page |
| `--card`, `--surface`, `--box-bg` | cartes, en-tête, fenêtres |
| `--surface2`, `--field`, `--input-bg` | champs, boutons secondaires |
| `--line`, `--box-border`, `--input-border` | traits et bordures |
| `--text`, `--fg`, `--input-fg` | texte principal |
| `--text2`, `--muted` | texte secondaire, discret |
| `--accent`, `--accent-hover` | boutons, liens, éléments actifs |
| `--err`, `--warn` | erreur, avertissement |

Écris-les dans `:root, :root[data-theme] { … }` (voir `ui/skins/papier/skin.css`).
Pour un thème clair, mets aussi `color-scheme: light;`.

### Aller plus loin
Tu peux cibler les classes de l'appli (`.mou-header`, `.mou-btn`, `.mou-panel`, `body.mou-skin input`…) : ouvre la page
dans le navigateur de ton téléphone ou ordinateur et inspecte les éléments pour trouver leurs noms.
Les images de ton skin se rangent dans ton dossier et s'appellent ainsi : `url(/ui/skins/sakura/fond.jpg)`.

### Interdit (le serveur refuse le skin)
`@import`, adresses `http(s)://` ou `//`, balises, `javascript:`, `expression()`. Seules les images de ton dossier
`/ui/skins/…` et les images intégrées `data:image/…` sont permises dans `url(...)`. Taille maximale : 150 Ko.
Les polices sous licence (Arial…) ne vont **jamais** sur GitHub.

## L'essayer sans rien installer
Dans **🎨 Apparence → 🧪 Essayer un skin brouillon**, colle le contenu de `skin.css` : il est enregistré **pour toi
seul** et appliqué tout de suite. Les images d'un brouillon doivent être en `data:image/…`.

## Si tu te perds
- `/?skin=off` : affiche la page sans aucun skin (le menu 🎨 reste utilisable).
- `/apparence/reset` : remet le style d'origine pour ton compte.

## Proposer ton skin (sans accès au dépôt)
1. **Fork** du dépôt `mouflo/MouFlossover` sur GitHub.
2. Une branche, ton dossier `ui/skins/<ton-skin>/` (rien d'autre), un commit.
3. Une **Pull Request** vers `main`. Le propriétaire relit, valide, et le skin arrive au déploiement de 5 h.

Une PR ne doit contenir que `ui/skins/<ton-skin>/…` : toute modification ailleurs sera refusée.
