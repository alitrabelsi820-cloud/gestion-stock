/* ═══════════════════════════════════════════════════════════
   MOBILE.JS — Hamburger nav + fixes UX mobile
   ═══════════════════════════════════════════════════════════ */

(function () {
  'use strict';

  function init() {

    const nav = document.querySelector('nav');
    if (!nav) return;

    const navLinks = nav.querySelector('.nav-links');
    if (!navLinks) return;

    /* ── 1. Déplacer nav-links dans le body pour éviter le stacking context de nav ── */
    document.body.appendChild(navLinks);

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
    }

    /* ── 7. Événements ── */
    hamburger.addEventListener('click', function (e) {
      e.stopPropagation();
      navLinks.classList.contains('nav-open') ? closeMenu() : openMenu();
    });

    overlay.addEventListener('click', closeMenu);

    navLinks.querySelectorAll('a').forEach(function (a) {
      a.addEventListener('click', function () {
        setTimeout(closeMenu, 100);
      });
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') closeMenu();
    });

    /* ── 8. Corrections au chargement et resize ── */
    hideInlineLogout();
    window.addEventListener('resize', hideInlineLogout);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      setTimeout(init, 50);
    });
  } else {
    setTimeout(init, 50);
  }

})();
