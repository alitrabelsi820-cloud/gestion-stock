/* ═══════════════════════════════════════════════════════════
   MOBILE.JS — Hamburger nav + fixes UX mobile
   ═══════════════════════════════════════════════════════════ */

/* ── Variables CSS dark / light ── */
var _DARK_VARS = {
  '--bg':       '#0F0F0D',
  '--bg2':      '#1A1A17',
  '--bg3':      '#141412',
  '--card':     '#1A1A17',
  '--bd':       'rgba(255,255,255,0.08)',
  '--txt':      '#F0EFE8',
  '--muted':    '#7A7A72',
  '--gold-pale':'rgba(196,163,90,0.12)'
};

function _applyTheme(isDark) {
  var root = document.documentElement;
  if (isDark) {
    root.classList.add('dark');
    Object.keys(_DARK_VARS).forEach(function(k) {
      root.style.setProperty(k, _DARK_VARS[k]);
    });
  } else {
    root.classList.remove('dark');
    Object.keys(_DARK_VARS).forEach(function(k) {
      root.style.removeProperty(k);
    });
  }
}

/* Appliquer immédiatement au chargement */
_applyTheme(localStorage.getItem('theme') === 'dark');

/* ── Bouton 🌙 universel — fonctionne sur TOUTES les pages ── */
(function injectDarkToggleGlobal() {
  function doInject() {
    if (document.getElementById('dark-toggle')) return;

    var btn = document.createElement('button');
    btn.id = 'dark-toggle';
    btn.setAttribute('aria-label', 'Mode sombre');
    btn.title = 'Mode sombre / clair';
    btn.innerHTML = document.documentElement.classList.contains('dark') ? '☀️' : '🌙';

    btn.addEventListener('click', function () {
      var isDark = !document.documentElement.classList.contains('dark');
      _applyTheme(isDark);
      localStorage.setItem('theme', isDark ? 'dark' : 'light');
      btn.innerHTML = isDark ? '☀️' : '🌙';
    });

    /* Cas 1 : page avec nav standard → insérer avant le hamburger */
    var nav = document.querySelector('nav');
    if (nav) {
      var hbg = nav.querySelector('.hamburger');
      if (hbg) { nav.insertBefore(btn, hbg); }
      else { nav.appendChild(btn); }
      return;
    }

    /* Cas 2 : page accueil (pas de nav) → bouton fixe en haut à gauche */
    btn.style.cssText = [
      'position:fixed', 'top:12px', 'left:52px', 'z-index:9999',
      'background:rgba(30,25,10,.75)', 'backdrop-filter:blur(8px)',
      'border:1px solid rgba(184,146,60,.35)', 'border-radius:20px',
      'padding:6px 11px', 'font-size:15px', 'cursor:pointer',
      'line-height:1', 'transition:border-color .2s'
    ].join('!important;') + '!important';
    document.body.appendChild(btn);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', doInject);
  } else {
    doInject();
  }
})();

(function () {
  'use strict';

  function init() {

    const nav = document.querySelector('nav');
    if (!nav) return;

    const navLinks = nav.querySelector('.nav-links');
    if (!navLinks) return;

    /* ── 1. Déplacer nav-links dans le body UNIQUEMENT sur mobile ── */
    function moveNavLinks() {
      if (window.innerWidth <= 860 && navLinks.parentElement !== document.body) {
        document.body.appendChild(navLinks);
      } else if (window.innerWidth > 860 && navLinks.parentElement === document.body) {
        nav.appendChild(navLinks);
      }
    }
    moveNavLinks();
    window.addEventListener('resize', moveNavLinks);

    /* ── 2. Cacher le bouton Déconnexion inline sur mobile ── */
    function hideInlineLogout() {
      nav.querySelectorAll('a[href="/logout"], a[href*="logout"]').forEach(function (a) {
        if (a.classList.contains('mob-logout')) return;
        if (a.parentElement === navLinks) return;
        if (window.innerWidth <= 860) {
          a.setAttribute('style', 'display:none!important');
        } else {
          a.removeAttribute('style');
        }
      });
    }

    /* ── 3. Ajouter le lien Déconnexion dans le menu dropdown ── */
    if (!navLinks.querySelector('.mob-logout')) {
      const logoutEl = nav.querySelector('a[href="/logout"], a[href*="logout"]');
      const href = logoutEl ? logoutEl.getAttribute('href') : '/logout';
      const mob = document.createElement('a');
      mob.className = 'mob-logout';
      mob.href = href;
      mob.textContent = '⎋ Déconnexion';
      navLinks.appendChild(mob);
    }

    /* ── 4. Injecter le hamburger si absent ── */
    let hamburger = nav.querySelector('.hamburger');
    if (!hamburger) {
      hamburger = document.createElement('button');
      hamburger.className = 'hamburger';
      hamburger.setAttribute('aria-label', 'Menu');
      hamburger.setAttribute('aria-expanded', 'false');
      hamburger.innerHTML = '☰';
      nav.appendChild(hamburger);
    }

    /* ── 5. Overlay sombre ── */
    let overlay = document.getElementById('nav-overlay-dark');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'nav-overlay-dark';
      overlay.className = 'nav-overlay-dark';
      document.body.appendChild(overlay);
    }

    /* ── 6. Fonctions open / close ── */
    function openMenu() {
      navLinks.classList.add('nav-open');
      overlay.classList.add('open');
      hamburger.innerHTML = '✕';
      hamburger.setAttribute('aria-expanded', 'true');
      document.body.style.overflow = 'hidden';
    }

    function closeMenu() {
      navLinks.classList.remove('nav-open');
      overlay.classList.remove('open');
      hamburger.innerHTML = '☰';
      hamburger.setAttribute('aria-expanded', 'false');
      document.body.style.overflow = '';
      /* Fermer aussi les sous-menus dropdown */
      document.querySelectorAll('.nav-dropdown').forEach(function(d){ d.classList.remove('open'); });
    }

    /* ── 7. Événements ── */
    hamburger.addEventListener('click', function (e) {
      e.stopPropagation();
      navLinks.classList.contains('nav-open') ? closeMenu() : openMenu();
    });

    overlay.addEventListener('click', closeMenu);

    navLinks.querySelectorAll('a').forEach(function (a) {
      /* Click normal (desktop) */
      a.addEventListener('click', function () {
        setTimeout(closeMenu, 100);
      });
      /* touchend pour iOS Safari : les liens dans fixed+overflow ne répondent pas au click */
      a.addEventListener('touchend', function (e) {
        e.preventDefault();
        var href = a.getAttribute('href');
        closeMenu();
        if (href && href !== '#') {
          setTimeout(function () { window.location.href = href; }, 80);
        }
      }, { passive: false });
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') closeMenu();
    });

    /* ── 8. Corrections au chargement et resize ── */
    hideInlineLogout();
    window.addEventListener('resize', hideInlineLogout);

    /* ── 9. Forcer scroll horizontal sur les tableaux ── */
    function fixTableScroll() {
      if (window.innerWidth > 860) return;
      document.querySelectorAll('.tbl-wrap').forEach(function(el) {
        el.style.setProperty('overflow-x', 'auto', 'important');
        el.style.setProperty('overflow-y', 'visible', 'important');
        el.style.setProperty('-webkit-overflow-scrolling', 'touch', 'important');
      });
    }
    fixTableScroll();
    window.addEventListener('resize', fixTableScroll);

  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      setTimeout(init, 50);
    });
  } else {
    setTimeout(init, 50);
  }

})();
