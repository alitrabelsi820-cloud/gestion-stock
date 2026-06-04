/* ═══════════════════════════════════════════════════════════
   SIDEBAR — Trabelsi ERP
   Construit dynamiquement sidebar + topbar, masque l'ancienne nav
   ═══════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  /* ── Sécurité : déconnexion automatique si l'app a été fermée ── */
  if (!sessionStorage.getItem('gs_active')) {
    /* La sessionStorage est vide = app fermée/rechargée à froid → logout */
    fetch('/logout', { method: 'GET', redirect: 'manual' })
      .catch(function(){})
      .finally(function(){ window.location.replace('/login'); });
    return; /* Arrêter le reste du script */
  }

  var SIDEBAR_WIDTH = 240;
  var BREAKPOINT = 861;

  function isDesktop() { return window.innerWidth >= BREAKPOINT; }

  var path = window.location.pathname;

  /* ── Structure de navigation ── */
  var NAV = [
    { type: 'link', icon: '🏠', label: 'Accueil',   href: '/accueil' },
    { type: 'link', icon: '📊', label: 'Dashboard',  href: '/dashboard' },
    { type: 'divider' },
    {
      type: 'group', icon: '📦', label: 'Stock', id: 'stock',
      links: [
        { label: 'Voir le stock',       href: '/stock' },
        { label: 'Ajouter un article',  href: '/ajouter' },
        { label: '📷 Galerie photos',   href: '/galerie' },
        { label: '🏷️ Étiquettes QR',    href: '/etiquettes' },
        { label: 'Catalogue',           href: '/catalogue' },
      ]
    },
    {
      type: 'group', icon: '💰', label: 'Ventes', id: 'ventes',
      links: [
        { label: 'Ventes',              href: '/vendu' },
        { label: 'Factures',            href: '/facture' },
        { label: '📝 Facture Libre',    href: '/facture-libre' },
        { label: '📋 Devis',            href: '/devis' },
        { label: '🗂 Historique Devis', href: '/historique-devis' },
      ]
    },
    {
      type: 'group', icon: '👥', label: 'Clients', id: 'clients',
      links: [
        { label: 'Dossier Clients',     href: '/clients' },
        { label: 'Crédits',             href: '/credit' },
        { label: 'Chèques',             href: '/cheques' },
      ]
    },
    {
      type: 'group', icon: '⚙️', label: 'Gestion', id: 'gestion',
      links: [
        { label: 'Fournisseurs',        href: '/fournisseurs' },
        { label: 'Fiche référence',     href: '/fiche' },
        { label: '📅 Historique',       href: '/historique-activite' },
        { label: '👁️ Activité employés', href: '/activite-employes' },
      ]
    },
  ];

  /* ── Helpers actif ── */
  function isActive(href) {
    if (href === '/accueil' || href === '/') return path === href;
    return path === href;
  }

  function groupHasActive(group) {
    return group.links.some(function (l) { return isActive(l.href); });
  }

  /* ── Créer un élément ── */
  function el(tag, props, children) {
    var e = document.createElement(tag);
    if (props) Object.keys(props).forEach(function (k) {
      if (k === 'className') e.className = props[k];
      else if (k === 'style') e.style.cssText = props[k];
      else if (k === 'onclick') e.onclick = props[k];
      else if (k === 'title') e.title = props[k];
      else if (k === 'href') e.href = props[k];
      else if (k === 'innerHTML') e.innerHTML = props[k];
      else if (k === 'placeholder') e.placeholder = props[k];
      else if (k === 'type') e.type = props[k];
      else if (k === 'autocomplete') e.autocomplete = props[k];
      else if (k === 'id') e.id = props[k];
      else e.setAttribute(k, props[k]);
    });
    if (children) children.forEach(function (c) { if (c) e.appendChild(c); });
    return e;
  }

  /* ══════════════════════════════════════
     BUILD SIDEBAR
  ══════════════════════════════════════ */
  function buildSidebar() {
    var sb = el('div', { id: 'gs-sidebar' });

    /* Logo */
    var logo = el('a', { id: 'gs-sidebar-logo', href: '/accueil' }, [
      el('div', { className: 'gs-logo-diamond', innerHTML: '♦' }),
      el('div', { className: 'gs-logo-name', innerHTML: 'TRABELSI' }),
    ]);
    sb.appendChild(logo);

    /* Zone scrollable */
    var scroll = el('div', { id: 'gs-sidebar-scroll' });
    var section = el('div', { className: 'gs-nav-section' });

    NAV.forEach(function (item) {

      if (item.type === 'divider') {
        section.appendChild(el('div', { className: 'gs-nav-divider' }));
        return;
      }

      if (item.type === 'link') {
        var a = el('a', {
          href: item.href,
          className: 'gs-nav-item' + (isActive(item.href) ? ' active' : ''),
          innerHTML: '<span class="gs-ico">' + item.icon + '</span>' + item.label,
        });
        section.appendChild(a);
        return;
      }

      if (item.type === 'group') {
        var open = groupHasActive(item);

        var btn = el('button', {
          className: 'gs-nav-item' + (open ? ' open' : ''),
          innerHTML: '<span class="gs-ico">' + item.icon + '</span>'
                   + item.label
                   + '<span class="gs-chevron">▶</span>',
          'data-group': item.id,
        });

        var sub = el('div', {
          className: 'gs-submenu' + (open ? ' open' : ''),
          id: 'gs-sub-' + item.id,
        });

        item.links.forEach(function (l) {
          sub.appendChild(el('a', {
            href: l.href,
            className: isActive(l.href) ? 'active' : '',
            innerHTML: l.label,
          }));
        });

        btn.addEventListener('click', function () {
          var isOpen = sub.classList.toggle('open');
          btn.classList.toggle('open', isOpen);
        });

        section.appendChild(btn);
        section.appendChild(sub);
      }
    });

    scroll.appendChild(section);
    sb.appendChild(scroll);

    /* Footer */
    var footer = el('div', { id: 'gs-sidebar-footer' });

    var notifBtn = el('button', {
      className: 'gs-footer-btn',
      id: 'gs-sb-notif',
      title: 'Notifications',
      innerHTML: '🔔',
      onclick: function () {
        var nb = document.getElementById('notif-btn')
               || document.querySelector('[onclick*="notif"]')
               || document.querySelector('.notif-btn');
        if (nb) nb.click();
      }
    });

    var darkBtn = el('button', {
      className: 'gs-footer-btn',
      id: 'gs-sb-dark',
      title: 'Mode sombre',
      innerHTML: '🌙',
      onclick: toggleDark,
    });

    var logoutBtn = el('a', {
      href: '/logout',
      className: 'gs-footer-btn gs-footer-logout',
      title: 'Déconnexion',
      innerHTML: '⎋ Déco',
    });

    footer.appendChild(notifBtn);
    footer.appendChild(darkBtn);
    footer.appendChild(logoutBtn);
    sb.appendChild(footer);

    return sb;
  }

  /* ══════════════════════════════════════
     BUILD TOPBAR
  ══════════════════════════════════════ */
  function buildTopbar() {
    var tb = el('div', { id: 'gs-topbar' });

    /* Search */
    var searchWrap = el('div', { className: 'gs-topbar-search' });
    var searchIco  = el('span', { className: 'gs-search-ico', innerHTML: '🔍' });
    var searchInput = el('input', {
      type: 'text',
      id: 'gs-search-input',
      placeholder: 'Rechercher article, client, vente…',
      autocomplete: 'off',
    });
    var searchResults = el('div', { id: 'gs-search-results' });
    searchWrap.appendChild(searchIco);
    searchWrap.appendChild(searchInput);
    searchWrap.appendChild(searchResults);
    tb.appendChild(searchWrap);

    /* Actions */
    var actions = el('div', { className: 'gs-topbar-actions' });

    /* Indicateur de sauvegarde */
    var backupBtn = el('div', {
      className: 'gs-backup-indic',
      id: 'gs-backup-indic',
      title: 'Vérification de la sauvegarde…',
      innerHTML: '<span class="gs-backup-dot"></span>'
               + '<span class="gs-backup-txt">Vérification…</span>',
    });
    actions.appendChild(backupBtn);

    var notifBtn = el('button', {
      className: 'gs-topbar-btn',
      id: 'gs-tb-notif',
      title: 'Notifications',
      innerHTML: '🔔<span class="gs-notif-badge" id="gs-notif-badge"></span>',
      onclick: function () {
        var nb = document.getElementById('notif-btn')
               || document.querySelector('[onclick*="notif"]')
               || document.querySelector('.notif-btn');
        if (nb) nb.click();
      }
    });

    var darkBtn = el('button', {
      className: 'gs-topbar-btn',
      id: 'gs-tb-dark',
      title: 'Mode sombre',
      innerHTML: '🌙',
      onclick: toggleDark,
    });

    var logoutBtn = el('a', {
      href: '/logout',
      className: 'gs-topbar-logout',
      innerHTML: '⎋ Déconnexion',
    });

    actions.appendChild(notifBtn);
    actions.appendChild(darkBtn);
    actions.appendChild(logoutBtn);
    tb.appendChild(actions);

    return tb;
  }

  /* ── Dark mode ── */
  function toggleDark() {
    document.body.classList.toggle('dark');
    localStorage.setItem('gs_dark', document.body.classList.contains('dark') ? '1' : '0');
    updateDarkBtnIcon();
  }

  function updateDarkBtnIcon() {
    var dark = document.body.classList.contains('dark');
    var icon = dark ? '☀️' : '🌙';
    var btns = [
      document.getElementById('gs-sb-dark'),
      document.getElementById('gs-tb-dark'),
    ];
    btns.forEach(function (b) { if (b) b.innerHTML = icon; });
  }

  /* ── Recherche globale : articles + clients ── */
  function wireSearch() {
    var gsInput   = document.getElementById('gs-search-input');
    var gsResults = document.getElementById('gs-search-results');
    if (!gsInput || !gsResults) return;

    var articles = null;
    var clients  = null;
    var debounce;

    function esc(s) {
      return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    function loadData() {
      if (articles === null) {
        articles = [];
        fetch('/api/articles')
          .then(function (r) { return r.json(); })
          .then(function (d) { articles = d || []; })
          .catch(function () {});
      }
      if (clients === null) {
        clients = [];
        fetch('/api/clients')
          .then(function (r) { return r.json(); })
          .then(function (d) { clients = d || []; })
          .catch(function () {});
      }
    }

    function showResults(q) {
      var qLow = q.toLowerCase();
      var html = '';

      /* Articles : chercher par id/référence ou nom */
      var artHits = (articles || []).filter(function (a) {
        return String(a.id).toLowerCase().indexOf(qLow) !== -1
          || (a.nom       && a.nom.toLowerCase().indexOf(qLow)       !== -1)
          || (a.reference && a.reference.toLowerCase().indexOf(qLow) !== -1);
      }).slice(0, 6);

      if (artHits.length) {
        html += '<div style="padding:6px 14px 3px;font-size:9px;font-weight:700;text-transform:uppercase;'
              + 'letter-spacing:1.5px;color:#9a8e7e;background:#faf8f5;border-bottom:1px solid #f0e8d4;">Articles</div>';
        artHits.forEach(function (a) {
          html += '<a href="/fiche?ref=' + encodeURIComponent(a.id) + '" '
                + 'style="display:flex;align-items:center;gap:10px;padding:9px 14px;text-decoration:none;'
                + 'color:#1a1612;border-bottom:1px solid #f8f4ee;" '
                + 'onmouseover="this.style.background=\'#faf8f5\'" onmouseout="this.style.background=\'\'">'
                + '<span style="font-size:16px;width:22px;text-align:center">💍</span>'
                + '<span style="flex:1;overflow:hidden">'
                + '<span style="font-weight:600;font-size:13px">' + esc(a.nom || a.id) + '</span>'
                + '<span style="color:#9a8e7e;font-size:11px;margin-left:8px">Réf. ' + esc(a.id) + '</span>'
                + '</span>'
                + '<span style="font-size:10px;color:#33332F;font-weight:600;white-space:nowrap">Fiche →</span>'
                + '</a>';
        });
      }

      /* Clients : chercher par nom */
      var cliHits = (clients || []).filter(function (c) {
        return c.nom && c.nom.toLowerCase().indexOf(qLow) !== -1;
      }).slice(0, 6);

      if (cliHits.length) {
        html += '<div style="padding:6px 14px 3px;font-size:9px;font-weight:700;text-transform:uppercase;'
              + 'letter-spacing:1.5px;color:#9a8e7e;background:#faf8f5;border-bottom:1px solid #f0e8d4;">Clients</div>';
        cliHits.forEach(function (c) {
          var safeNom = esc(c.nom).replace(/'/g, '\\&#39;');
          html += '<a href="/clients" '
                + 'onclick="try{localStorage.setItem(\'clientSearch\',\'' + safeNom + '\');}catch(e){}" '
                + 'style="display:flex;align-items:center;gap:10px;padding:9px 14px;text-decoration:none;'
                + 'color:#1a1612;border-bottom:1px solid #f8f4ee;" '
                + 'onmouseover="this.style.background=\'#faf8f5\'" onmouseout="this.style.background=\'\'">'
                + '<span style="font-size:16px;width:22px;text-align:center">👤</span>'
                + '<span style="flex:1;overflow:hidden">'
                + '<span style="font-weight:600;font-size:13px">' + esc(c.nom) + '</span>'
                + (c.telephone ? '<span style="color:#9a8e7e;font-size:11px;margin-left:8px">' + esc(c.telephone) + '</span>' : '')
                + '</span>'
                + '<span style="font-size:10px;color:#33332F;font-weight:600;white-space:nowrap">Dossier →</span>'
                + '</a>';
        });
      }

      if (!artHits.length && !cliHits.length) {
        html = '<div style="padding:18px 14px;text-align:center;color:#9a8e7e;font-size:12px;">'
             + 'Aucun résultat pour « ' + esc(q) + ' »</div>';
      }

      gsResults.innerHTML = html;
      gsResults.style.display = 'block';
    }

    gsInput.addEventListener('focus', loadData);

    gsInput.addEventListener('input', function () {
      var q = this.value.trim();
      clearTimeout(debounce);
      if (q.length < 2) { gsResults.style.display = 'none'; return; }
      debounce = setTimeout(function () { showResults(q); }, 180);
    });

    gsInput.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { gsResults.style.display = 'none'; gsInput.blur(); }
    });

    document.addEventListener('click', function (e) {
      if (!gsInput.parentElement.contains(e.target)) {
        gsResults.style.display = 'none';
      }
    });
  }

  /* ── Synchroniser le badge notification ── */
  function syncNotifBadge() {
    var badge = document.getElementById('gs-notif-badge');
    if (!badge) return;
    var origBadge = document.querySelector('.notif-count') || document.getElementById('notif-count');
    if (origBadge && origBadge.textContent && origBadge.textContent !== '0') {
      badge.classList.add('visible');
    }
  }

  /* ── Indicateur de sauvegarde (dernière sync R2) ── */
  function _relTime(ts) {
    var diff = Math.floor(Date.now() / 1000 - ts);
    if (diff < 0) diff = 0;
    if (diff < 10)   return "à l'instant";
    if (diff < 60)   return 'il y a ' + diff + ' s';
    if (diff < 3600) return 'il y a ' + Math.floor(diff / 60) + ' min';
    if (diff < 86400) {
      var h = Math.floor(diff / 3600);
      return 'il y a ' + h + ' h';
    }
    var d = Math.floor(diff / 86400);
    return 'il y a ' + d + ' j';
  }

  function _fullDate(ts) {
    var dt = new Date(ts * 1000);
    var pad = function (n) { return n < 10 ? '0' + n : '' + n; };
    return pad(dt.getDate()) + '/' + pad(dt.getMonth() + 1) + '/' + dt.getFullYear()
         + ' à ' + pad(dt.getHours()) + ':' + pad(dt.getMinutes());
  }

  function refreshBackupIndicator() {
    var indic = document.getElementById('gs-backup-indic');
    if (!indic) return;
    var dot = indic.querySelector('.gs-backup-dot');
    var txt = indic.querySelector('.gs-backup-txt');

    fetch('/api/last-backup', { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        indic.classList.remove('ok', 'warn', 'err', 'local');

        if (d.status === 'error') {
          indic.classList.add('err');
          txt.textContent = 'Échec sauvegarde';
          indic.title = '⚠️ La dernière sauvegarde a échoué — vérifiez la connexion.';
          return;
        }
        if (d.status === 'local' || !d.ts) {
          indic.classList.add('local');
          txt.textContent = 'Local';
          indic.title = 'Mode local — pas de sauvegarde cloud configurée.';
          return;
        }

        var ageMin = (Date.now() / 1000 - d.ts) / 60;
        indic.classList.add(ageMin > 60 ? 'warn' : 'ok');
        txt.textContent = 'Sauvegardé ' + _relTime(d.ts);
        indic.title = '✅ Données sauvegardées dans le cloud\nDernière sauvegarde : '
                    + _fullDate(d.ts);
      })
      .catch(function () {
        indic.classList.remove('ok', 'warn', 'local');
        indic.classList.add('err');
        txt.textContent = 'Hors ligne';
        indic.title = 'Impossible de contacter le serveur.';
      });
  }

  /* ══════════════════════════════════════
     BOTTOM TAB BAR (mobile ≤860px)
  ══════════════════════════════════════ */
  var MOBILE_TABS = [
    { icon: '🏠', label: 'Accueil',  href: '/accueil' },
    { icon: '📦', label: 'Stock',    id: 'stock', links: [
      { icon: '📦', label: 'Voir le stock',       href: '/stock' },
      { icon: '➕', label: 'Ajouter un article',  href: '/ajouter' },
      { icon: '📷', label: 'Galerie photos',      href: '/galerie' },
      { icon: '🏷️', label: 'Étiquettes QR',       href: '/etiquettes' },
      { icon: '📖', label: 'Catalogue PDF',       href: '/catalogue' },
    ]},
    { icon: '💰', label: 'Ventes',   id: 'ventes', links: [
      { icon: '💰', label: 'Historique ventes',   href: '/vendu' },
      { icon: '🛒', label: 'Nouvelle vente',      href: '/vendu?new=1' },
      { icon: '🧾', label: 'Factures',            href: '/facture' },
      { icon: '📝', label: 'Facture libre',       href: '/facture-libre' },
      { icon: '📋', label: 'Devis',               href: '/devis' },
      { icon: '🗂',  label: 'Historique Devis',   href: '/historique-devis' },
    ]},
    { icon: '👥', label: 'Clients',  id: 'clients', links: [
      { icon: '👥', label: 'Dossier Clients',     href: '/clients' },
      { icon: '💳', label: 'Crédits',             href: '/credit' },
      { icon: '📋', label: 'Chèques',             href: '/cheques' },
    ]},
    { icon: '☰',  label: 'Plus',     id: 'plus', links: [
      { icon: '📊', label: 'Dashboard',           href: '/dashboard' },
      { icon: '🏭', label: 'Fournisseurs',        href: '/fournisseurs' },
      { icon: '🔍', label: 'Fiche référence',     href: '/fiche' },
      { icon: '📅', label: 'Historique activité', href: '/historique-activite' },
      { icon: '👁️', label: 'Activité employés',   href: '/activite-employes' },
      { icon: '⎋',  label: 'Déconnexion',         href: '/logout' },
    ]},
  ];

  var _openSheet = null;

  function closeSheet() {
    var overlay = document.getElementById('gs-sheet-overlay');
    var sheet   = document.getElementById('gs-sheet');
    if (overlay) overlay.classList.remove('visible');
    if (sheet)   sheet.classList.remove('open');
    _openSheet = null;
    var tabs = document.querySelectorAll('.gs-tab-btn');
    tabs.forEach(function(t){ t.classList.remove('sheet-open'); });
  }

  function openSheet(tabId, links) {
    if (_openSheet === tabId) { closeSheet(); return; }
    _openSheet = tabId;

    var sheet = document.getElementById('gs-sheet');
    var overlay = document.getElementById('gs-sheet-overlay');
    if (!sheet || !overlay) return;

    sheet.innerHTML = links.map(function(l) {
      var active = (path === l.href || (l.href !== '/accueil' && path.startsWith(l.href.split('?')[0]))) ? ' gs-sheet-item-active' : '';
      return '<a class="gs-sheet-item' + active + '" href="' + l.href + '">'
        + '<span class="gs-sheet-item-ico">' + l.icon + '</span>'
        + '<span class="gs-sheet-item-label">' + l.label + '</span>'
        + '<span class="gs-sheet-item-arrow">›</span>'
        + '</a>';
    }).join('');

    overlay.classList.add('visible');
    sheet.classList.add('open');

    var btn = document.querySelector('.gs-tab-btn[data-id="' + tabId + '"]');
    if (btn) btn.classList.add('sheet-open');
  }

  function buildBottomBar() {
    var bar = el('div', { id: 'gs-bottombar' });

    MOBILE_TABS.forEach(function(tab) {
      var isDirectActive = tab.href && (path === tab.href || (tab.href !== '/accueil' && path.startsWith(tab.href)));
      var isGroupActive  = !tab.href && tab.links && tab.links.some(function(l){
        return path === l.href || (l.href !== '/accueil' && path.startsWith(l.href.split('?')[0]));
      });
      var active = isDirectActive || isGroupActive;

      var btn = el('button', {
        className: 'gs-tab-btn' + (active ? ' active' : ''),
        'data-id': tab.id || '',
      });
      btn.innerHTML = '<span class="gs-tab-ico">' + tab.icon + '</span>'
        + '<span class="gs-tab-label">' + tab.label + '</span>';

      if (tab.href) {
        btn.addEventListener('click', function(){ window.location.href = tab.href; });
      } else {
        btn.addEventListener('click', function(){ openSheet(tab.id, tab.links); });
      }
      bar.appendChild(btn);
    });

    /* Overlay + sheet */
    var overlay = el('div', { id: 'gs-sheet-overlay' });
    overlay.addEventListener('click', closeSheet);

    var sheet = el('div', { id: 'gs-sheet' });

    document.body.appendChild(overlay);
    document.body.appendChild(sheet);
    document.body.appendChild(bar);
  }

  function initMobile() {
    if (isDesktop()) return;
    if (document.getElementById('gs-bottombar')) return;

    /* Cacher l'ancienne nav */
    var oldNav = document.querySelector('body > nav');
    if (oldNav) oldNav.style.setProperty('display', 'none', 'important');

    /* Restaurer dark mode */
    if (localStorage.getItem('gs_dark') === '1') {
      document.body.classList.add('dark');
    }

    buildBottomBar();
  }

  /* ══════════════════════════════════════
     INITIALISATION
  ══════════════════════════════════════ */
  function init() {
    if (!isDesktop()) return;
    if (document.getElementById('gs-sidebar')) return; /* déjà injecté */

    /* Appliquer classe layout */
    document.body.classList.add('sidebar-layout');

    /* Restaurer dark mode */
    if (localStorage.getItem('gs_dark') === '1') {
      document.body.classList.add('dark');
    }

    /* Cacher l'ancienne nav */
    var oldNav = document.querySelector('body > nav');
    if (oldNav) oldNav.style.setProperty('display', 'none', 'important');

    /* Insérer sidebar et topbar */
    var sidebar = buildSidebar();
    var topbar  = buildTopbar();
    document.body.insertBefore(sidebar, document.body.firstChild);
    document.body.insertBefore(topbar, sidebar.nextSibling);

    /* Icône dark mode initiale */
    updateDarkBtnIcon();

    /* Connecter la recherche */
    wireSearch();

    /* Badge notification (après 300ms pour laisser la page charger) */
    setTimeout(syncNotifBadge, 300);

    /* Indicateur de sauvegarde — au chargement puis toutes les 30 s */
    refreshBackupIndicator();
    setInterval(refreshBackupIndicator, 30000);
  }

  /* ── Lancer au bon moment ── */
  function start() { init(); initMobile(); }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }

  /* ── Gérer le redimensionnement ── */
  var resizeTimer;
  window.addEventListener('resize', function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      var hasSidebar  = !!document.getElementById('gs-sidebar');
      var hasBottomBar = !!document.getElementById('gs-bottombar');
      if (isDesktop()) {
        if (!hasSidebar) init();
        if (hasBottomBar) {
          var bb = document.getElementById('gs-bottombar');
          var ov = document.getElementById('gs-sheet-overlay');
          var sh = document.getElementById('gs-sheet');
          if (bb) bb.remove(); if (ov) ov.remove(); if (sh) sh.remove();
        }
      } else {
        if (hasSidebar) {
          var sb = document.getElementById('gs-sidebar');
          var tb = document.getElementById('gs-topbar');
          if (sb) sb.remove(); if (tb) tb.remove();
          document.body.classList.remove('sidebar-layout');
        }
        if (!hasBottomBar) initMobile();
        var oldNav = document.querySelector('body > nav');
        if (oldNav) {
          if (!isDesktop()) oldNav.style.setProperty('display','none','important');
          else oldNav.style.removeProperty('display');
        }
      }
    }, 80);
  });

})();
