/* MouFlossover : page d'accueil (liste des crossovers, aperçu du plan, rangement, annulation). */
(function () {
  'use strict';
  var COL = {FIRE: '#ff7a45', MED: '#2dd4bf', PD: '#60a5fa', JUSTICE: '#c084fc', FBI: '#818cf8', MW: '#fbbf24', INTL: '#4ade80', GREYS: '#f472b6', S19: '#facc15'};
  var TABS = {
    todo: ['Prêts à organiser', 'Tout est là, il suffit de valider', 'var(--warn)'],
    blocked: ['Bloqués', 'Il manque quelque chose', 'var(--err)'],
    done: ['Terminés', 'Rien à faire', 'var(--accent)'],
    ignored: ['Mis de côté', 'Que tu as ignorés', 'var(--muted)']
  };
  var BADGE = {todo: 'Prêt', done: 'Terminé', blocked: 'Bloqué', ignored: 'Ignoré'};
  var st = {items: [], updated: null, status: 'todo', fr: 'Tous', q: '', loaded: false, wiki: null, sig: '', pending: false, wikiRun: false};
  var $ = function (s) { return document.querySelector(s); };

  function h(tag, attrs) {
    var e = document.createElement(tag);
    attrs = attrs || {};
    Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (k === 'class') e.className = v;
      else if (k.indexOf('on') === 0) e.addEventListener(k.slice(2), v);
      else if (k === 'style') e.style.cssText = v;
      else e.setAttribute(k, v);
    });
    for (var i = 2; i < arguments.length; i++) {
      [].concat(arguments[i]).forEach(function (k) { if (k != null && k !== false) e.append(k.nodeType ? k : document.createTextNode(k)); });
    }
    return e;
  }
  async function api(path, opt) {
    try {
      var r = await fetch(path, opt);
      if (r.status === 401) { location.href = '/login'; return {error: 'Session expirée'}; }
      var j = await r.json().catch(function () { return {}; });
      if (!r.ok && !j.error) j.error = 'Erreur ' + r.status;
      return j;
    } catch (e) { return {error: 'Serveur injoignable'}; }
  }
  function post(p, b) { return api(p, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b || {})}); }
  function toast(msg, kind) { if (window.MouToast) window.MouToast(msg, kind || 'ok'); else alert(msg); }
  var pad = function (n) { return String(n).padStart(2, '0'); };
  function fmtDate(d) { return new Date(d + 'T12:00:00').toLocaleDateString('fr-FR', {day: 'numeric', month: 'long', year: 'numeric'}); }

  // Fenêtre de confirmation (même fenêtre que le Journal du kit commun)
  function dlg(title, body, okLabel, danger) {
    return new Promise(function (res) {
      var m = h('div', {class: 'mou-modal show', id: 'mfDlg'});
      var close = function (v) { m.remove(); if (!document.querySelector('.mou-modal.show')) document.body.style.overflow = ''; res(v); };
      m.append(h('div', {class: 'mou-box narrow'}, h('h3', {}, title), body,
        h('div', {class: 'mou-actions-row'},
          h('button', {class: 'mou-btn ghost', onclick: function () { close(false); }}, 'Annuler'),
          h('button', {class: 'mou-btn' + (danger ? ' danger' : ''), onclick: function () { close(true); }}, okLabel))));
      m.addEventListener('mousedown', function (e) { if (e.target === m) close(false); });
      document.body.append(m);
      document.body.style.overflow = 'hidden';
    });
  }

  function explainBlocker(b) {
    var m;
    if (/pas encore téléchargé/.test(b)) return {icon: '⏳', t: 'Épisode pas encore téléchargé', d: b.split(' : ')[0] + " n'est pas encore dans la bibliothèque. Sonarr doit d'abord le récupérer, puis clique sur « Actualiser ».", files: []};
    if (/introuvable sur le disque/.test(b)) return {icon: '❓', t: 'Fichier introuvable', d: b.split(' : ')[0] + " : Sonarr le croit présent mais je ne le trouve pas dans le dossier de la saison.", files: []};
    if ((m = b.match(/^(.*?) : plusieurs fichiers candidats \((.*)\)$/))) return {icon: '⚠️', t: 'Deux fichiers pour le même épisode', d: m[1] + " : je ne sais pas lequel est le bon. Supprime ou renomme le doublon sur le disque, puis clique sur « Actualiser ».", files: m[2].split(/\.mkv, /).map(function (f) { return f.replace(/\.mkv$/, ''); })};
    if (/en dehors du dossier des séries/.test(b)) return {icon: '🚫', t: 'Dossier hors du dossier des séries', d: b, files: []};
    if (/Destination existante/.test(b)) return {icon: '⚠️', t: 'Un fichier du même nom existe déjà', d: b, files: []};
    return {icon: '⚠️', t: b, d: '', files: []};
  }
  function explainWarning(w) {
    var m = w.match(/^(.*?) : le titre du NFO « (.*) » ne se termine pas par \((\d)\)$/);
    if (m) return {t: 'Titre à vérifier (' + m[1] + ')', d: 'Le titre « ' + m[2] + " » ne précise pas qu'il s'agit de la partie " + m[3] + '. Ce n\'est pas bloquant : vérifie-le dans Emby après le rangement.'};
    return {t: w, d: ''};
  }
  function opText(o) {
    if (o.kind === 'rename_group') return ['✎', 'Ajouter le repère de partie au nom du fichier de la série', 'Le contenu ne change pas ; seul le nom du fichier change.', o.to_stem];
    if (o.kind === 'copy_group') return ['⧉', o.label.replace(' en S00E', ' comme spécial n°'), 'Une vraie copie : le fichier, la vignette et les infos sont dupliqués.', o.to_stem];
    if (o.kind === 'patch_nfo') return ['✦', 'Régler le titre et la place dans la saison', "Le spécial s'affichera juste avant l'épisode S" + pad(o.airs[0]) + 'E' + pad(o.airs[1]) + ' (spécial n°' + o.episode + ').', null];
    if (o.kind === 'unmonitor') return ['◌', 'Dire à Sonarr de ne plus surveiller cet épisode', 'Sinon Sonarr le croirait manquant après le renommage et le retélécharger.', null];
    return ['•', o.label, '', null];
  }

  var busy = function () { return !!document.querySelector('.mou-modal.show') || !!document.querySelector('.mf-plan.open') || document.activeElement === $('#mfQ'); };

  async function load(force) {
    var btn = $('#mfRefresh');
    if (force) { btn.disabled = true; btn.firstChild.nodeValue = '↻ Actualisation…'; }
    try {
      if (force) { var r = await post('/api/refresh'); if (r.error) toast(r.error, 'err'); }
      var j = await api('/api/crossovers');
      if (j.error && (force || !st.loaded)) toast('Erreur : ' + j.error, 'err');
      var items = j.items || st.items, sig = JSON.stringify(items), changed = sig !== st.sig, first = !st.loaded;
      st.sig = sig; st.items = items; st.updated = j.updated || st.updated; st.loaded = true;
      if (force || first) render();
      else if (changed) { if (busy()) { st.pending = true; updateMeta(); } else render(); }
      else updateMeta();
      if (j.items && !j.updated) setTimeout(function () { load(false); }, 2500);
    } finally { if (force) { btn.disabled = false; btn.firstChild.nodeValue = '↻ Actualiser'; } }
  }
  function metaText() {
    var w = st.wiki;
    var wtxt = w && w.running ? "Analyse de Wikipédia en cours : " + w.done + ' séries sur ' + w.total + " (les résultats s'ajoutent au fur et à mesure). " : (w && w.total ? 'Wikipédia : ' + w.pages + ' séries analysées. ' : '');
    return wtxt + (st.updated ? 'Dernière mise à jour : ' + new Date(st.updated).toLocaleString('fr-FR') : 'Analyse en cours…') + (st.pending ? ' · Nouveaux résultats disponibles : clique sur « Actualiser » pour les afficher.' : '');
  }
  function updateMeta() { var m = $('#mfMeta'); if (m) m.textContent = metaText(); }

  function render() {
    var counts = {todo: 0, done: 0, blocked: 0, ignored: 0};
    st.items.forEach(function (i) { counts[i.status]++; });
    $('#mfTabs').replaceChildren.apply($('#mfTabs'), Object.keys(TABS).map(function (k) {
      return h('button', {type: 'button', class: 'mf-tab' + (st.status === k ? ' on' : ''), style: '--c:' + TABS[k][2], onclick: function () { st.status = k; render(); }},
        h('span', {class: 'nb'}, String(counts[k])), h('span', {class: 'lb'}, TABS[k][0]), h('span', {class: 'ds'}, TABS[k][1]));
    }));
    var hints = {
      todo: "Ces crossovers sont complets. Ouvre « Voir ce qui va changer » pour comprendre, puis « Organiser » pour lancer.",
      blocked: "Ces crossovers ne peuvent pas être traités pour l'instant. Chaque carte explique ce qui manque.",
      done: "Tout est en place pour ces crossovers (si tu viens de les traiter, actualise la bibliothèque dans Emby).",
      ignored: "Tu as mis ces crossovers de côté. Ils ne seront pas traités."
    };
    $('#mfHint').textContent = hints[st.status];
    var frs = ['Tous'].concat(Array.from(new Set(st.items.map(function (i) { return i.franchise; }))));
    $('#mfPills').replaceChildren.apply($('#mfPills'), frs.map(function (f) {
      return h('button', {type: 'button', class: 'mf-pill' + (st.fr === f ? ' on' : ''), onclick: function () { st.fr = f; render(); }}, f);
    }).concat([h('span', {class: 'mf-meta', id: 'mfMeta'}, metaText())]));
    st.pending = false;
    if (!st.loaded) return;
    var q = st.q.toLowerCase();
    var list = st.items.filter(function (i) {
      return i.status === st.status && (st.fr === 'Tous' || i.franchise === st.fr) &&
        (!q || (i.name + ' ' + i.franchise + ' ' + i.parts.map(function (p) { return p.series + ' ' + p.title; }).join(' ')).toLowerCase().indexOf(q) >= 0);
    });
    var grid = $('#mfGrid');
    if (!list.length) { grid.replaceChildren(h('div', {class: 'mf-empty'}, h('div', {style: 'font-size:2.4rem'}, '🎬'), h('p', {}, 'Aucun crossover dans cette catégorie.'))); return; }
    grid.replaceChildren.apply(grid, list.map(card));
  }
  function summary(c) {
    var n = c.parts.length, sr = Array.from(new Set(c.parts.map(function (p) { return p.series; })));
    if (c.status === 'todo') return ['Prêt à être organisé', 'Crossover de ' + n + ' parties sur ' + sr.length + ' séries. Après le rangement, chaque série contiendra les ' + n + ' parties dans l\'ordre. ' + c.opCount + ' action(s) prévue(s).'];
    if (c.status === 'blocked') return ['Impossible pour l\'instant', 'Vois ci-dessous ce qui bloque et ce qu\'il faut faire.'];
    if (c.status === 'done') return ['Tout est en place', 'Chaque série contient déjà les ' + n + ' parties, au bon endroit.'];
    return ['Mis de côté', 'Ce crossover ne sera pas traité tant que tu ne le restaures pas.'];
  }
  function card(c, idx) {
    var plan = h('div', {class: 'mf-plan'});
    var s = summary(c);
    var parts = c.parts.map(function (p) {
      return h('div', {class: 'mf-part', style: '--c:' + (COL[p.tag] || '#888')},
        h('div', {class: 'mf-pn'}, String(p.part)),
        h('div', {class: 'mf-tx'}, h('b', {}, 'Partie ' + p.part + ' — ' + p.series), h('small', {}, 'Saison ' + p.season + ', épisode ' + p.ep + ' · « ' + p.title + ' »'),
          h('span', {class: 'mf-chip ' + (p.hasFile ? 'y' : 'n')}, p.hasFile ? 'Fichier présent' : 'Pas encore téléchargé'),
          p.source === 'wikipedia' ? h('span', {class: 'mf-chip w', title: 'Repéré grâce à Wikipédia'}, 'repéré via Wikipédia') : null));
    });
    var issues = [];
    c.blockers.forEach(function (b) { var e = explainBlocker(b); issues.push(h('div', {class: 'mf-issue bad'}, h('b', {}, e.icon + ' ' + e.t), e.d ? h('p', {}, e.d) : null, e.files.length ? h('ul', {}, e.files.map(function (f) { return h('li', {}, f); })) : null)); });
    c.warnings.forEach(function (w) { var e = explainWarning(w); issues.push(h('div', {class: 'mf-issue'}, h('b', {}, 'ℹ️ ' + e.t), e.d ? h('p', {}, e.d) : null)); });
    var wk = (c.wiki && c.wiki.length) ? h('div', {class: 'mf-wiki'}, h('div', {class: 'mf-cap'}, 'Pour vérifier sur Wikipédia'),
      c.wiki.map(function (w) {
        return h('div', {class: 'mf-wk'}, h('a', {href: w.url, target: '_blank', rel: 'noopener noreferrer'}, '📖 ' + w.page + ' ↗'),
          h('small', {}, w.extrait ? '« ' + w.extrait + ' »' : 'Page de la série : aucun passage précis trouvé, cherche « crossover » dans la page.'));
      })) : null;
    var acts = h('div', {class: 'mf-actions'});
    if (c.status === 'todo') acts.append(h('button', {type: 'button', class: 'mou-btn', onclick: async function () {
      var body = h('div', {}, h('p', {class: 'mou-msub'}, '« ' + c.name + ' » : ' + c.opCount + ' action(s) vont être exécutées sur tes fichiers (renommages, copies, informations Emby, réglage Sonarr).'),
        h('p', {class: 'mou-msub'}, "Aucun fichier n'est supprimé. Un journal est conservé : « Annuler le traitement » remettra tout comme avant."));
      if (!await dlg('Organiser ce crossover ?', body, 'Organiser')) return;
      var r = await post('/api/crossovers/' + c.id + '/apply');
      if (r.ok) toast('Crossover organisé (' + r.actions + ' actions). Actualise la bibliothèque dans Emby pour voir le résultat.', 'ok'); else toast(r.error || 'Échec', 'err');
      load(false);
    }}, 'Organiser ce crossover'));
    if (c.status !== 'ignored' && !c.blockers.length) {
      var prev = h('button', {type: 'button', class: 'mou-btn ghost', onclick: async function () {
        if (plan.classList.contains('open')) { plan.classList.remove('open'); prev.textContent = 'Voir ce qui va changer'; return; }
        prev.disabled = true; var p = await api('/api/crossovers/' + c.id + '/plan'); prev.disabled = false;
        if (p.error) { toast(p.error, 'err'); return; }
        plan.replaceChildren.apply(plan, [h('h4', {}, p.ops.length ? 'Voici ce qui va se passer' : 'Rien à faire'),
          h('p', {class: 'mf-note'}, p.ops.length ? "Dans l'ordre. Aucun fichier n'est supprimé." : 'Tout est déjà en place pour ce crossover.')].concat(
          p.ops.map(function (o) { var t = opText(o); return h('div', {class: 'mf-op'}, h('i', {}, t[0]), h('div', {}, t[1], t[2] ? h('code', {}, t[2]) : null, t[3] ? h('code', {}, t[3]) : null)); })));
        plan.classList.add('open'); prev.textContent = 'Masquer le détail';
      }}, 'Voir ce qui va changer');
      acts.append(prev);
    }
    if (c.applied) acts.append(h('button', {type: 'button', class: 'mou-btn danger', onclick: async function () {
      if (!await dlg('Annuler le traitement ?', h('p', {class: 'mou-msub'}, "Les copies créées seront supprimées, les fichiers renommés retrouveront leur nom d'origine et les informations modifiées seront restaurées."), 'Annuler le traitement', true)) return;
      var r = await post('/api/crossovers/' + c.id + '/undo'); toast(r.ok ? 'Traitement annulé' : (r.error || 'Échec'), r.ok ? 'ok' : 'err'); load(false);
    }}, 'Annuler le traitement'));
    acts.append(h('button', {type: 'button', class: 'mou-btn ghost small', onclick: async function () { await post('/api/crossovers/' + c.id + '/ignore', {ignored: c.status !== 'ignored'}); load(false); }}, c.status === 'ignored' ? 'Restaurer' : 'Mettre de côté'));
    return h('article', {class: 'mf-card', style: 'animation-delay:' + Math.min(idx * 40, 400) + 'ms'},
      h('div', {class: 'mf-top'}, h('div', {style: 'flex:1;min-width:0'}, h('h3', {}, c.name), h('div', {class: 'mf-sub'}, fmtDate(c.date), h('span', {class: 'mf-fr'}, c.franchise), c.applied ? h('span', {class: 'mf-chip w'}, 'rangé par MouFlossover') : null)),
        h('span', {class: 'mf-badge ' + c.status}, BADGE[c.status])),
      h('div', {class: 'mf-say ' + c.status}, h('b', {}, s[0]), s[1]),
      h('div', {class: 'mf-cap'}, 'Les parties'), parts, wk, issues, acts, plan);
  }

  async function loadWiki() {
    var w = await api('/api/wiki');
    if (w.error) return;
    st.wiki = w; var was = st.wikiRun; st.wikiRun = w.running; updateMeta();
    if (was && !w.running) { toast('Analyse Wikipédia terminée', 'ok'); load(false); }
  }

  function init() {
    $('#mfQ').addEventListener('input', function (e) { st.q = e.target.value; render(); });
    $('#mfRefresh').addEventListener('click', function () { load(true); });
    try { if (localStorage.getItem('mf-help') === 'hidden') $('#mfHelp').style.display = 'none'; } catch (e) {}
    $('#mfHelpX').addEventListener('click', function () { $('#mfHelp').style.display = 'none'; try { localStorage.setItem('mf-help', 'hidden'); } catch (e) {} });
    load(false); loadWiki();
    setInterval(function () { load(false); }, 60000);
    setInterval(loadWiki, 8000);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
