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
      else if (v === false || v == null) return;
      else e.setAttribute(k, v === true ? "" : v);
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
  function post(p, b, methode) { return api(p, {method: methode || 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b || {})}); }
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
    if (/^Partie .* manquante/.test(b)) return {icon: '🧩', t: 'Il manque la partie ' + (b.match(/^Partie (.*?) manquante/) || [0, ''])[1] + ' du crossover', d: "Cette partie du crossover vient d'une série que tu n'as pas dans Sonarr (ou d'un épisode que Sonarr ne connaît pas). Ajoute la série dans Sonarr, puis « Actualiser ». Si tu préfères ranger quand même les parties que tu as, utilise « Corriger » puis « Enregistrer » : les parties seront renumérotées sans trou.", files: []};
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

  var busy = function () { return !!document.querySelector('.mou-modal.show') || !!document.querySelector('.mf-plan.open') || document.activeElement === $('#mfQ') || !!document.querySelector('details.mf-wiki[open]'); };

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
    var noms = {};
    st.items.forEach(function (i) { if (i.status === st.status) i.parts.concat(i.refs || []).forEach(function (p) { noms[p.series] = 1; }); });
    var frs = ['Tous'].concat(Object.keys(noms).sort(function (a, b) { return a.localeCompare(b, 'fr'); }));
    if (frs.indexOf(st.fr) < 0) st.fr = 'Tous';
    $('#mfPills').replaceChildren.apply($('#mfPills'), frs.map(function (f) {
      return h('button', {type: 'button', class: 'mf-pill' + (st.fr === f ? ' on' : ''), onclick: function () { st.fr = f; render(); }}, f);
    }).concat([h('span', {class: 'mf-meta', id: 'mfMeta'}, metaText())]));
    st.pending = false;
    if (!st.loaded) return;
    var q = st.q.toLowerCase();
    var list = st.items.filter(function (i) {
      return i.status === st.status && (st.fr === 'Tous' || i.parts.concat(i.refs || []).some(function (p) { return p.series === st.fr; })) &&
        (!q || (i.name + ' ' + i.franchise + ' ' + i.parts.map(function (p) { return p.series + ' ' + p.title; }).join(' ')).toLowerCase().indexOf(q) >= 0);
    });
    var grid = $('#mfGrid');
    if (!list.length) { grid.replaceChildren(h('div', {class: 'mf-empty'}, h('div', {style: 'font-size:2.4rem'}, '🎬'), h('p', {}, 'Aucun crossover dans cette catégorie.'))); return; }
    grid.replaceChildren.apply(grid, list.map(card));
  }
  function summary(c) {
    var n = c.parts.length, sr = Array.from(new Set(c.parts.map(function (p) { return p.series; })));
    if (c.status === 'todo' && (c.refs || []).length) return ['Prêt à être organisé', 'Un seul épisode, où apparaissent des personnages d\'une autre série qui n\'a pas de suite : « ' + c.parts[0].title + ' » (' + c.parts[0].series + ') sera aussi ajouté dans ' + c.refs.map(function (r) { return r.series; }).join(', ') + ', juste avant « ' + c.refs[0].title + ' ». ' + c.opCount + ' action(s) prévue(s).'];
    if (c.status === 'todo') return ['Prêt à être organisé', 'Crossover de ' + n + ' parties sur ' + sr.length + ' séries. Après le rangement, chaque série contiendra les ' + n + ' parties dans l\'ordre. ' + c.opCount + ' action(s) prévue(s).'];
    if (c.status === 'blocked') return ['Impossible pour l\'instant', 'Vois ci-dessous ce qui bloque et ce qu\'il faut faire.'];
    if (c.status === 'done') return ['Tout est en place', 'Chaque série contient déjà les ' + n + ' parties, au bon endroit.'];
    return ['Mis de côté', 'Ce crossover ne sera pas traité tant que tu ne le restaures pas.'];
  }
  // Fenêtre d'avancement : suit le rangement ou l'annulation en cours jusqu'à la fin, puis recharge la liste
  var suivi = false;
  async function suivre(titre) {
    if (suivi) return; suivi = true;
    var bar = h('div', {class: 'mf-barfill'}), pct = h('b', {}, '0 %'), lab = h('p', {class: 'mou-msub'}, 'Préparation…');
    var m = h('div', {class: 'mou-modal show', id: 'mfJob'}, h('div', {class: 'mou-box narrow'}, h('h3', {}, titre),
      lab, h('div', {class: 'mf-bar'}, bar), h('div', {class: 'mf-barpct'}, pct),
      h('p', {class: 'mf-note'}, 'Tu peux laisser cette page ouverte : le traitement continue de toute façon, même si tu la quittes.')));
    document.body.append(m); document.body.style.overflow = 'hidden';
    var fin = null;
    while (!fin) {
      await new Promise(function (r) { setTimeout(r, 700); });
      var j; try { j = await api('/api/job'); } catch (e) { continue; }
      if (!j || j.error) continue;
      bar.style.width = (j.pct || 0) + '%'; pct.textContent = (j.pct || 0) + ' %';
      lab.textContent = (j.total ? 'Étape ' + Math.min((j.done || 0) + 1, j.total) + ' sur ' + j.total + ' — ' : '') + (j.label || '');
      if (j.state === 'fini') fin = j;
      else if (j.state !== 'running') fin = {ok: false, message: 'Le traitement ne répond plus.'};
    }
    m.remove(); if (!document.querySelector('.mou-modal.show')) document.body.style.overflow = '';
    suivi = false;
    toast((fin.message || '') + ' ' + (fin.emby || ''), fin.ok ? 'ok' : 'err');
    await load(false);
  }

  // Épisode d'une série absente de la bibliothèque : recherche de la série, saison et épisode, puis demande à Sonarr
  async function inviter(c) {
    var chosen = null, q = h('input', {type: 'text', class: 'mou-input', placeholder: 'Nom de la série (ex. NCIS Hawai)', autocapitalize: 'off'});
    var sa = h('input', {type: 'number', min: '1', class: 'mou-input', placeholder: 'Saison'}), ep = h('input', {type: 'number', min: '1', class: 'mou-input', placeholder: 'Épisode'});
    var res = h('div', {class: 'mf-fix'}), sug = h('div', {class: 'mf-fix'}), etat = h('p', {class: 'mf-note'}, '');
    var t = null;
    function montrer(list) {
      res.replaceChildren.apply(res, list.map(function (r) {
        return h('button', {type: 'button', class: 'mou-btn ghost mf-pick' + (chosen && chosen.tvdbId === r.tvdbId ? ' on' : ''), onclick: function () { chosen = r; etat.textContent = 'Série choisie : ' + r.title + (r.year ? ' (' + r.year + ')' : ''); montrer(list); }},
          h('b', {}, r.title + (r.year ? ' (' + r.year + ')' : '')), h('small', {}, r.overview || ''));
      }));
      if (!list.length) res.replaceChildren(h('p', {class: 'mf-note'}, 'Aucune série trouvée.'));
    }
    async function chercher(terme) {
      etat.textContent = 'Recherche…'; var r = await api('/api/lookup?term=' + encodeURIComponent(terme));
      etat.textContent = r.error || ''; montrer(r.items || []); return r.items || [];
    }
    q.addEventListener('input', function () { clearTimeout(t); t = setTimeout(function () { if (q.value.trim().length > 1) chercher(q.value.trim()); }, 500); });
    var sg = await api('/api/crossovers/' + c.id + '/suggestions');
    etat.textContent = (sg.items || []).length ? "Rempli d'après la suggestion ci-dessous, tu peux le modifier." : '';
    (sg.items || []).forEach(function (x) {
      sug.append(h('button', {type: 'button', class: 'mou-btn ghost mf-pick', onclick: function () { appliquer(x); }},
        h('b', {}, 'Wikipédia cite : ' + x.title + ' — saison ' + x.season + ', épisode ' + x.episode), h('small', {}, x.line || '')));
    });
    async function appliquer(x) {
      q.value = x.title; sa.value = x.season; ep.value = x.episode;
      var li = await chercher(x.title);
      if (li[0]) { chosen = li[0]; etat.textContent = 'Série choisie : ' + li[0].title + (li[0].year ? ' (' + li[0].year + ')' : ''); montrer(li); }
    }
    var body = h('div', {}, h('p', {class: 'mou-msub'}, "L'appli ajoute la série dans Sonarr dans un dossier de passage (invisible pour Emby, aucun autre épisode ne sera téléchargé), demande cet épisode, attend son arrivée puis le range avec le crossover. Le fichier n'est jamais supprimé : qBittorrent continue de le partager."),
      sug.children.length ? h('div', {}, h('div', {class: 'mf-cap'}, 'Suggestions'), sug) : null,
      h('div', {class: 'mf-cap'}, 'Série'), q, res, h('div', {class: 'mf-cap'}, 'Saison et épisode'), h('div', {style: 'display:grid;grid-template-columns:1fr 1fr;gap:8px'}, sa, ep), etat);
    if ((sg.items || []).length) appliquer(sg.items[0]);       // rempli d'office avec la 1re suggestion, modifiable
    if (!await dlg("Demander l'épisode manquant", body, 'Demander cet épisode')) return;
    if (!chosen) { toast("Choisis d'abord une série dans la liste.", 'err'); return; }
    var r = await post('/api/crossovers/' + c.id + '/invite', {tvdbId: chosen.tvdbId, title: chosen.title, season: parseInt(sa.value, 10), episode: parseInt(ep.value, 10)});
    toast(r.ok ? "Demande envoyée : l'appli s'occupe de tout." : (r.error || 'Échec'), r.ok ? 'ok' : 'err'); load(false);
  }

  // Choix de la version à télécharger : toutes les sources trouvées par les indexeurs, les mieux partagées d'abord
  async function sources(titre, eid) {
    var choix = null, zone = h('div', {class: 'mf-fix'}, h('p', {class: 'mou-msub'}, 'Recherche chez vos indexeurs… (jusqu\'à 30 secondes)'));
    var body = h('div', {}, h('p', {class: 'mou-msub'}, 'Choisis la version à télécharger. Celles qui ont des partageurs (seeds) arrivent en premier ; sans partageur, un téléchargement ne démarre pas.'), zone);
    var promesse = dlg('Sources pour ' + titre, body, 'Télécharger cette version');
    api('/api/episodes/' + eid + '/sources').then(function (r) {
      if (!r.ok || !r.sources.length) { zone.replaceChildren(h('p', {class: 'mou-msub'}, r.error || 'Aucune version trouvée chez vos indexeurs pour le moment.')); return; }
      zone.replaceChildren.apply(zone, r.sources.map(function (x) {
        var go = x.size ? (x.size / 1073741824).toFixed(1).replace('.', ',') + ' Go' : '';
        var seeds = x.protocol === 'torrent' ? (x.seeders || 0) + ' partageur(s)' : 'usenet';
        return h('label', {class: 'mf-src' + (x.rejected ? ' off' : '')},
          h('input', {type: 'radio', name: 'mfSrc', onchange: function () { choix = x; }}),
          h('span', {}, h('b', {}, x.title), h('small', {}, [x.indexer, seeds, go, x.quality, x.langues].filter(Boolean).join(' · ')),
            x.rejected ? h('small', {class: 'mf-bad'}, 'Écartée par Sonarr : ' + x.raison) : null));
      }));
    });
    if (!await promesse) return;
    if (!choix) { toast("Choisis d'abord une version dans la liste.", 'err'); return; }
    var r = await post('/api/episodes/' + eid + '/source', {guid: choix.guid, indexerId: choix.indexerId});
    toast(r.ok ? 'Téléchargement lancé avec cette version.' : (r.error || 'Échec'), r.ok ? 'ok' : 'err'); load(false);
  }

  // Crossover décrit à la main (quand la détection automatique n'a rien vu)
  async function ajouterManuel() {
    var rep = await api('/api/series');
    if (rep.error) { toast(rep.error, 'err'); return; }
    var m = {name: '', parts: [{sid: 0, season: '', ep: ''}, {sid: 0, season: '', ep: ''}], refs: []};
    while (true) {
      var liste = function (tab, ref) {
        var box = h('div', {class: 'mf-fix'});
        function dessiner() {
          box.replaceChildren.apply(box, tab.map(function (o, i) {
            var info = h('small', {class: 'mf-note'}, '');
            function verifier() {
              info.textContent = '';
              if (!o.sid || o.season === '' || !o.ep) return;
              api('/api/episode?sid=' + o.sid + '&season=' + o.season + '&ep=' + o.ep).then(function (r) {
                info.textContent = r.ok ? '« ' + (r.title || 'sans titre') + ' » · ' + (r.hasFile ? 'fichier présent' : 'pas encore téléchargé') : '⚠️ ' + (r.error || '');
              });
            }
            var sel = h('select', {class: 'mou-input', onchange: function () { o.sid = parseInt(sel.value, 10) || 0; verifier(); }},
              h('option', {value: ''}, '— choisir la série —'),
              rep.series.map(function (x) { var op = h('option', {value: String(x.id)}, x.title); if (x.id === o.sid) op.selected = true; return op; }));
            var sa = h('input', {type: 'number', min: '0', class: 'mou-input', placeholder: 'Saison', value: o.season === '' ? null : String(o.season), oninput: function () { o.season = sa.value === '' ? '' : parseInt(sa.value, 10); verifier(); }});
            var ep = h('input', {type: 'number', min: '1', class: 'mou-input', placeholder: 'Épisode', value: o.ep === '' ? null : String(o.ep), oninput: function () { o.ep = ep.value === '' ? '' : parseInt(ep.value, 10); verifier(); }});
            verifier();
            return h('div', {class: 'mf-manrow'},
              h('div', {class: 'mf-mantop'}, h('b', {}, ref ? '📍 Repère' : 'Partie ' + (i + 1)),
                h('span', {}, i > 0 && !ref ? h('button', {type: 'button', class: 'mou-btn ghost small', onclick: function () { tab.splice(i - 1, 0, tab.splice(i, 1)[0]); dessiner(); }}, '▲') : null,
                  (ref || tab.length > 1) ? h('button', {type: 'button', class: 'mou-btn ghost small', onclick: function () { tab.splice(i, 1); dessiner(); }}, 'Retirer') : null)),
              sel, h('div', {class: 'mf-manep'}, sa, ep), info,
              ref ? null : h('label', {class: 'mf-nc'}, h('input', {type: 'checkbox', checked: o.nc ? '' : null, onchange: function (ev) { o.nc = ev.target.checked; }}), ' Ne pas la copier dans les autres séries'));
          }));
        }
        dessiner();
        return {box: box, add: function () { tab.push({sid: 0, season: '', ep: ''}); dessiner(); }};
      };
      var lp = liste(m.parts, false), lr = liste(m.refs, true);
      var nom = h('input', {type: 'text', class: 'mou-input', placeholder: 'Nom (facultatif)', value: m.name || null, oninput: function () { m.name = nom.value; }});
      var body = h('div', {}, h('p', {class: 'mou-msub'}, "Indique les épisodes du crossover dans l'ordre. Si un épisode d'une série fait apparaître des personnages d'une autre série qui n'a pas de suite, ajoute cette autre série en « repère » : l'épisode y sera copié juste avant l'épisode repère."),
        nom, h('div', {class: 'mf-cap'}, 'Parties du crossover'), lp.box,
        h('button', {type: 'button', class: 'mou-btn ghost small', onclick: lp.add}, '➕ Ajouter une partie'),
        h('div', {class: 'mf-cap'}, 'Série qui reçoit l\'épisode sans en avoir la suite (facultatif)'), lr.box,
        h('button', {type: 'button', class: 'mou-btn ghost small', onclick: lr.add}, '➕ Ajouter une série qui reçoit'));
      if (!await dlg('Ajouter un crossover à la main', body, 'Ajouter')) return;
      var net = function (t) { return t.filter(function (o) { return o.sid && o.season !== '' && o.ep; }).map(function (o) { return {sid: o.sid, season: o.season, ep: o.ep, nocopy: !!o.nc}; }); };
      var r = await post('/api/manuels', {name: m.name, parts: net(m.parts), refs: net(m.refs)});
      if (r.ok) { toast('Crossover ajouté : il apparaît dans la liste.', 'ok'); load(false); return; }
      toast(r.error || 'Échec', 'err');
    }
  }

  // Correction à la main : changer l'ordre des parties ou en retirer une
  async function corriger(c) {
    var lignes = c.parts.map(function (p) { return {key: p.key, p: p, out: false, nc: !!p.nocopy}; });
    var liste = h('div', {class: 'mf-fix'});
    function dessiner() {
      var vues = lignes.filter(function (l) { return !l.out; });
      liste.replaceChildren.apply(liste, lignes.map(function (l) {
        var i = vues.indexOf(l);
        return h('div', {class: 'mf-fixrow' + (l.out ? ' out' : '')},
          h('div', {class: 'mf-pn'}, l.out ? '–' : String(i + 1)),
          h('div', {class: 'mf-tx'}, h('b', {}, l.p.series), h('small', {}, 'S' + pad(l.p.season) + 'E' + pad(l.p.ep) + ' · « ' + l.p.title + ' »'),
            l.out ? null : h('label', {class: 'mf-nc'}, h('input', {type: 'checkbox', checked: l.nc ? '' : null, onchange: function (ev) { l.nc = ev.target.checked; }}), ' Ne pas la copier dans les autres séries')),
          h('div', {class: 'mf-fixbtns'},
            l.out ? null : h('button', {type: 'button', class: 'mou-btn ghost small', disabled: i === 0, onclick: function () { var j = lignes.indexOf(vues[i - 1]), k = lignes.indexOf(l); lignes.splice(j, 0, lignes.splice(k, 1)[0]); dessiner(); }}, '▲'),
            l.out ? null : h('button', {type: 'button', class: 'mou-btn ghost small', disabled: i === vues.length - 1, onclick: function () { var j = lignes.indexOf(vues[i + 1]), k = lignes.indexOf(l); lignes.splice(j, 0, lignes.splice(k, 1)[0]); dessiner(); }}, '▼'),
            h('button', {type: 'button', class: 'mou-btn ghost small', onclick: function () { l.out = !l.out; dessiner(); }}, l.out ? 'Remettre' : 'Retirer')));
      }));
    }
    dessiner();
    var body = h('div', {}, h('p', {class: 'mou-msub'}, "Change l'ordre des parties avec ▲ ▼ ou retire une partie qui n'a rien à faire dans ce crossover. Rien n'est touché sur tes fichiers avant de cliquer sur « Organiser »."), liste,
      c.fixed ? h('button', {type: 'button', class: 'mou-btn ghost small', onclick: async function () { await post('/api/crossovers/' + c.id + '/fix', {reset: true}); document.getElementById('mfDlg').remove(); toast('Détection automatique rétablie', 'ok'); load(false); }}, 'Rétablir la détection automatique') : null);
    if (!await dlg('Corriger ce crossover', body, 'Enregistrer')) return;
    var r = await post('/api/crossovers/' + c.id + '/fix', {order: lignes.filter(function (l) { return !l.out; }).map(function (l) { return l.key; }), exclude: lignes.filter(function (l) { return l.out; }).map(function (l) { return l.key; }),
      nocopy: lignes.filter(function (l) { return !l.out && l.nc; }).map(function (l) { return l.key; })});
    toast(r.ok ? 'Correction enregistrée' : (r.error || 'Échec'), r.ok ? 'ok' : 'err'); load(false);
  }
  function card(c, idx) {
    var plan = h('div', {class: 'mf-plan'});
    var s = summary(c);
    var parts = c.parts.map(function (p) {
      return h('div', {class: 'mf-part', style: '--c:' + (COL[p.tag] || '#888')},
        h('div', {class: 'mf-pn'}, String(p.part)),
        h('div', {class: 'mf-tx'}, h('b', {}, 'Partie ' + p.part + ' — ' + p.series), h('small', {}, 'Saison ' + p.season + ', épisode ' + p.ep + ' · « ' + p.title + ' »'),
          h('span', {class: 'mf-chip ' + (p.hasFile || p.nettoye ? 'y' : 'n')}, p.hasFile ? 'Fichier présent' : (p.nettoye ? 'Copié dans les autres séries' : 'Pas encore téléchargé')),
          p.source === 'wikipedia' ? h('span', {class: 'mf-chip w', title: 'Repéré grâce à Wikipédia'}, 'repéré via Wikipédia') : null,
          p.source === 'tvdb' ? h('span', {class: 'mf-chip w', title: 'Repéré grâce au résumé TheTVDB'}, 'repéré via TheTVDB') : null,
          p.source === 'invite' ? h('span', {class: 'mf-chip w', title: 'Série ajoutée dans le dossier de passage'}, p.nettoye ? 'épisode invité · copié partout, fichier retiré' : 'épisode invité') : null,
          p.source === 'manuel' ? h('span', {class: 'mf-chip w', title: 'Crossover décrit à la main'}, 'ajouté à la main') : null,
          p.nocopy ? h('span', {class: 'mf-chip w', title: 'Cette partie reste dans sa série, elle n\'est pas copiée dans les autres'}, 'reste dans sa série') : null,
          p.dl ? h('span', {class: 'mf-chip w'}, '⏳ ' + p.dl) : null,
          (!p.hasFile && !p.dl && p.source !== 'invite' && !/Invites/i.test(p.path || '')) ? h('button', {type: 'button', class: 'mou-btn ghost small mf-dl', onclick: async function (ev) {
            var b = ev.currentTarget; b.disabled = true;
            var r = await post('/api/episodes/' + p.episodeId + '/telecharger');
            toast(r.ok ? 'Recherche lancée chez vos indexeurs : « Actualiser » quand il sera téléchargé.' : (r.error || 'Échec'), r.ok ? 'ok' : 'err');
            if (!r.ok) b.disabled = false; else b.textContent = '⏳ Recherche lancée';
          }}, '⬇️ Télécharger cet épisode') : null,
          (!p.hasFile && p.source !== 'invite' && !/Invites/i.test(p.path || '')) ? h('button', {type: 'button', class: 'mou-btn ghost small mf-dl', onclick: function () { sources(p.series + ' S' + pad(p.season) + 'E' + pad(p.ep), p.episodeId); }}, '🔎 Choisir la version') : null));
    });
    var issues = [];
    c.blockers.forEach(function (b) { var e = explainBlocker(b); issues.push(h('div', {class: 'mf-issue bad'}, h('b', {}, e.icon + ' ' + e.t), e.d ? h('p', {}, e.d) : null, e.files.length ? h('ul', {}, e.files.map(function (f) { return h('li', {}, f); })) : null)); });
    c.warnings.forEach(function (w) {
      var e = explainWarning(w);
      var cree = /pas de NFO/.test(w) ? h('button', {type: 'button', class: 'mou-btn ghost small', onclick: async function (ev) {
        ev.currentTarget.disabled = true;
        var r = await post('/api/crossovers/' + c.id + '/fiches');
        toast(r.ok ? (r.faits.length ? 'Fiche créée : ' + r.faits.join(', ') : 'Aucune fiche à créer.') : (r.error || 'Échec'), r.ok ? 'ok' : 'err'); load(false);
      }}, '📝 Créer la fiche sans risque') : null;
      issues.push(h('div', {class: 'mf-issue'}, h('b', {}, 'ℹ️ ' + e.t), e.d ? h('p', {}, e.d) : null,
        cree ? h('p', {class: 'mf-note'}, "Ne remplace jamais une fiche existante. Elle reprend le titre et le résumé qu'Emby affiche déjà : rien ne change à l'écran.") : null, cree));
    });
    var wk = (c.wiki && c.wiki.length) ? h('details', {class: 'mf-wiki'}, h('summary', {class: 'mf-cap'}, '📖 Pour vérifier (' + c.wiki.length + ' source' + (c.wiki.length > 1 ? 's' : '') + ' : Wikipédia, TheTVDB)'),
      c.wiki.map(function (w) {
        return h('div', {class: 'mf-wk'}, h('a', {href: w.url, target: '_blank', rel: 'noopener noreferrer'}, '📖 ' + w.page + ' ↗'),
          h('small', {}, w.extrait ? '« ' + w.extrait + ' »' : 'Page de la série : aucun passage précis trouvé, cherche « crossover » dans la page.'));
      })) : null;
    var inv = (c.invites || []).length ? h('div', {class: 'mf-inv'}, (c.invites || []).map(function (g) {
      var etapes = {ajout: '1/4 Ajout de la série', recherche: '2/4 Recherche', attente: '3/4 Téléchargement', pret: '4/4 Reçu', range: '✔ Terminé'};
      return h('div', {class: 'mf-issue'}, h('b', {}, '📥 Épisode invité : ' + g.title + ' S' + pad(g.season) + 'E' + pad(g.episode)),
        h('p', {}, (etapes[g.status] || g.status) + ' — ' + (g.message || '')),
        (g.status !== 'pret' && g.status !== 'range') ? h('p', {class: 'mf-note'}, 'Tu as déjà le fichier ? Dépose-le tel quel dans le dossier de passage (son nom doit contenir S' + pad(g.season) + 'E' + pad(g.episode) + ') : il sera reconnu et rangé tout seul.') : null,
        h('div', {class: 'mf-actions'}, (g.status !== 'pret' && g.status !== 'range') && g.episodeId ? h('button', {type: 'button', class: 'mou-btn ghost small', onclick: function () { sources(g.title + ' S' + pad(g.season) + 'E' + pad(g.episode), g.episodeId); }}, '🔎 Choisir la version') : null,
          (g.status !== 'pret' && g.status !== 'range') ? h('button', {type: 'button', class: 'mou-btn ghost small', onclick: async function () { var r = await post('/api/invites/deposer'); toast(r.pris ? r.pris + ' fichier(s) déposé(s) reconnu(s) et rangé(s).' : 'Aucun fichier déposé à reconnaître pour l\'instant.', r.pris ? 'ok' : 'err'); load(false); }}, '📂 Chercher un fichier déposé') : null,
          (g.status !== 'pret' && g.status !== 'range') ? h('button', {type: 'button', class: 'mou-btn ghost small', onclick: async function () { await post('/api/invites/' + g.id + '/retry'); toast('Nouvel essai lancé', 'ok'); load(false); }}, 'Réessayer') : null,
          (g.status !== 'pret' && g.status !== 'range') ? h('button', {type: 'button', class: 'mou-btn ghost small', onclick: async function () { await post('/api/invites/' + g.id + '/cancel'); load(false); }}, 'Annuler la demande') : null));
    })) : null;
    var acts = h('div', {class: 'mf-actions'});
    if (c.manuel && !c.applied) acts.append(h('button', {type: 'button', class: 'mou-btn ghost small', onclick: async function () {
      var b = h('p', {class: 'mou-msub'}, '« ' + c.name + ' » sera retiré de la liste. Tes fichiers ne sont pas touchés.');
      if (!await dlg('Supprimer ce crossover manuel ?', b, 'Supprimer', true)) return;
      var r = await post('/api/manuels/' + c.manuel, {}, 'DELETE');
      toast(r.ok ? 'Crossover retiré' : (r.error || 'Échec'), r.ok ? 'ok' : 'err'); load(false);
    }}, '🗑️ Supprimer'));
    if (c.status === 'todo') acts.append(h('button', {type: 'button', class: 'mou-btn', onclick: async function () {
      var body = h('div', {}, h('p', {class: 'mou-msub'}, '« ' + c.name + ' » : ' + c.opCount + ' action(s) vont être exécutées sur tes fichiers (renommages, copies, informations Emby, réglage Sonarr).'),
        h('p', {class: 'mou-msub'}, "Aucun fichier n'est supprimé. Un journal est conservé : « Annuler le traitement » remettra tout comme avant."));
      if (!await dlg('Organiser ce crossover ?', body, 'Organiser')) return;
      var r = await post('/api/crossovers/' + c.id + '/apply');
      if (r.ok) suivre('Organisation de « ' + c.name + ' »'); else { toast(r.error || 'Échec', 'err'); load(false); }
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
      var r = await post('/api/crossovers/' + c.id + '/undo');
      if (r.ok) suivre('Annulation de « ' + c.name + ' »'); else { toast(r.error || 'Échec', 'err'); load(false); }
    }}, 'Annuler le traitement'));
    if (c.status === 'blocked' && !(c.invites || []).length && c.blockers.some(function (b) { return /^Partie .* manquante/.test(b); })) acts.append(h('button', {type: 'button', class: 'mou-btn', onclick: function () { inviter(c); }}, "➕ Demander l'épisode manquant"));
    if (!c.applied && c.status !== 'ignored') acts.append(h('button', {type: 'button', class: 'mou-btn ghost small', onclick: function () { corriger(c); }}, c.fixed ? '✏️ Corrigé à la main' : '✏️ Corriger'));
    acts.append(h('button', {type: 'button', class: 'mou-btn ghost small', onclick: async function () { await post('/api/crossovers/' + c.id + '/ignore', {ignored: c.status !== 'ignored'}); load(false); }}, c.status === 'ignored' ? 'Restaurer' : 'Mettre de côté'));
    return h('article', {class: 'mf-card', style: 'animation-delay:' + Math.min(idx * 40, 400) + 'ms'},
      h('div', {class: 'mf-top'}, h('div', {style: 'flex:1;min-width:0'}, h('h3', {}, c.name), h('div', {class: 'mf-sub'}, fmtDate(c.date), h('span', {class: 'mf-fr'}, Array.from(new Set(c.parts.concat(c.refs || []).map(function (p) { return p.series; }))).join(' · ')), c.applied ? h('span', {class: 'mf-chip w'}, 'rangé par MouFlossover') : null)),
        h('span', {class: 'mf-badge ' + c.status}, BADGE[c.status])),
      h('div', {class: 'mf-say ' + c.status}, h('b', {}, s[0]), s[1]),
      h('div', {class: 'mf-cap'}, 'Les parties'), parts, inv, wk, issues, acts, plan);
  }

  async function loadWiki() {
    var w = await api('/api/wiki');
    if (w.error) return;
    st.wiki = w; var was = st.wikiRun; st.wikiRun = w.running; updateMeta();
    if (was && !w.running) { toast('Analyse Wikipédia terminée', 'ok'); load(false); }
  }

  async function reprise() {
    try { var j = await api('/api/job'); if (j && j.state === 'running') suivre((j.kind === 'undo' ? 'Annulation' : 'Organisation') + (j.name ? ' de « ' + j.name + ' »' : '')); } catch (e) {}
  }

  function init() {
    $('#mfQ').addEventListener('input', function (e) { st.q = e.target.value; render(); });
    $('#mfRefresh').addEventListener('click', function () { load(true); });
    $('#mfAdd').addEventListener('click', ajouterManuel);
    try { if (localStorage.getItem('mf-help') === 'hidden') $('#mfHelp').style.display = 'none'; } catch (e) {}
    $('#mfHelpX').addEventListener('click', function () { $('#mfHelp').style.display = 'none'; try { localStorage.setItem('mf-help', 'hidden'); } catch (e) {} });
    load(false); loadWiki(); reprise();
    // Mises à jour discrètes : rien tant que la page est cachée (téléphone verrouillé, autre appli), et une relecture dès qu'on revient dessus
    setInterval(function () { if (!document.hidden) load(false); }, 180000);
    var tic = 0;
    setInterval(function () { tic++; if (!document.hidden && (st.wikiRun || tic % 15 === 0)) loadWiki(); }, 8000);     // 8 s pendant l'analyse Wikipédia, sinon toutes les 2 min
    document.addEventListener('visibilitychange', function () { if (!document.hidden) { load(false); loadWiki(); } });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();

// Touche « retour » du téléphone : ferme la fenêtre ouverte au lieu de quitter l'appli
(function () {
  var empile = 0, ignore = 0;
  function ouvertes() { return document.querySelectorAll('.mou-modal.show'); }
  function sync() {
    var n = ouvertes().length;
    while (empile < n) { history.pushState({mou: 1}, ''); empile++; }
    if (empile > n) { var k = empile - n; empile = n; ignore++; history.go(-k); }
  }
  window.addEventListener('popstate', function () {
    if (ignore) { ignore--; return; }
    var liste = ouvertes();
    if (!empile || !liste.length) return;
    empile--;
    var m = liste[liste.length - 1];
    var annuler = m.querySelector('.mou-actions-row .mou-btn.ghost');
    if (m.id === 'mfJob') m.classList.remove('show');
    else if (annuler) annuler.click();
    else m.classList.remove('show');
    if (!document.querySelector('.mou-modal.show')) document.body.style.overflow = '';
  });
  new MutationObserver(sync).observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ['class']});
})();
