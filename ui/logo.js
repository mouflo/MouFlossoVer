/* MouFlossover : logo aux couleurs du skin en cours.
   Le logo de l'en-tête est une image, qui ne voit pas les variables du skin : on le remplace par le même dessin en SVG
   intégré à la page (fond = --card/--bg, flèche claire = --text, flèche d'accent = --accent), et on adapte aussi l'icône de l'onglet. */
(function () {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';

  function logo() {
    var svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 512 512');
    svg.setAttribute('class', 'mf-logo');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', 'MouFlossover');
    svg.style.cssText = 'width:40px;height:40px;border-radius:10px;flex:none;display:block';
    svg.innerHTML =
      '<defs>' +
      '<linearGradient id="mfLgBg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:var(--card)"/><stop offset="1" style="stop-color:var(--bg)"/></linearGradient>' +
      '<linearGradient id="mfLgAc" x1="0" y1="0" x2="1" y2="1"><stop offset="0" style="stop-color:var(--accent-hover)"/><stop offset="1" style="stop-color:var(--accent)"/></linearGradient>' +
      '</defs>' +
      '<rect width="512" height="512" rx="112" fill="url(#mfLgBg)"/>' +
      '<rect x="6" y="6" width="500" height="500" rx="106" fill="none" style="stroke:var(--text)" stroke-opacity=".10" stroke-width="4"/>' +
      '<g fill="none" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M130 130 L382 382 M290 382 H382 V290" stroke="url(#mfLgAc)" stroke-width="46"/>' +
      '<path d="M130 382 L382 130 M290 130 H382 V222" style="stroke:var(--bg)" stroke-opacity=".9" stroke-width="82"/>' +
      '<path d="M130 382 L382 130 M290 130 H382 V222" style="stroke:var(--text)" stroke-width="46"/>' +
      '</g>';
    return svg;
  }

  function remplacer(essais) {
    var imgs = document.querySelectorAll('.mou-title img[src*="mouflossover.svg"]');
    if (!imgs.length) { if (essais > 0) setTimeout(function () { remplacer(essais - 1); }, 100); return; }
    imgs.forEach(function (img) { img.replaceWith(logo()); });
  }

  function couleur(nom, defaut) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(nom).trim();
    return v || defaut;
  }

  function icone() {
    var c = {card: couleur('--card', '#2a2d31'), bg: couleur('--bg', '#121315'), text: couleur('--text', '#f3f3f3'),
             ac: couleur('--accent', '#52b54b'), ac2: couleur('--accent-hover', '#6ccd65')};
    var s = '<svg xmlns="' + NS + '" viewBox="0 0 512 512">' +
      '<defs><linearGradient id="b" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="' + c.card + '"/><stop offset="1" stop-color="' + c.bg + '"/></linearGradient>' +
      '<linearGradient id="a" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="' + c.ac2 + '"/><stop offset="1" stop-color="' + c.ac + '"/></linearGradient></defs>' +
      '<rect width="512" height="512" rx="112" fill="url(#b)"/>' +
      '<g fill="none" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M130 130 L382 382 M290 382 H382 V290" stroke="url(#a)" stroke-width="46"/>' +
      '<path d="M130 382 L382 130 M290 130 H382 V222" stroke="' + c.bg + '" stroke-opacity=".9" stroke-width="82"/>' +
      '<path d="M130 382 L382 130 M290 130 H382 V222" stroke="' + c.text + '" stroke-width="46"/></g></svg>';
    var lien = document.querySelector('link[rel="icon"][type="image/svg+xml"]');
    if (lien) lien.setAttribute('href', 'data:image/svg+xml,' + encodeURIComponent(s));
  }

  function init() { remplacer(50); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
  window.addEventListener('load', icone);
})();
