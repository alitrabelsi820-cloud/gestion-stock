/**
 * Chatbot Assistant — Gestion de Stock Joaillerie
 * Widget flottant autonome, s'injecte dans n'importe quelle page.
 */
(function () {
  'use strict';

  // ── Styles ──────────────────────────────────────────────────────────────────
  const CSS = `
    #cb-btn {
      position: fixed; bottom: 24px; right: 24px; z-index: 10000;
      width: 56px; height: 56px; border-radius: 50%;
      background: linear-gradient(135deg, #b8923c, #8b6914);
      border: none; cursor: pointer; box-shadow: 0 4px 20px rgba(184,146,60,.5);
      display: flex; align-items: center; justify-content: center;
      font-size: 24px; transition: transform .2s, box-shadow .2s;
      color: #fff;
    }
    #cb-btn:hover { transform: scale(1.1); box-shadow: 0 6px 28px rgba(184,146,60,.7); }
    #cb-btn .cb-badge {
      position: absolute; top: -4px; right: -4px;
      background: #e74c3c; color: #fff; border-radius: 50%;
      width: 18px; height: 18px; font-size: 11px; font-weight: 700;
      display: none; align-items: center; justify-content: center;
    }

    #cb-panel {
      position: fixed; bottom: 92px; right: 24px; z-index: 10001;
      width: 380px; max-width: calc(100vw - 48px);
      background: #1a1600; border: 1px solid #3a2e10;
      border-radius: 16px; box-shadow: 0 8px 40px rgba(0,0,0,.7);
      display: flex; flex-direction: column;
      overflow: hidden;
      transform: scale(.85) translateY(20px);
      opacity: 0; pointer-events: none;
      transition: transform .25s cubic-bezier(.34,1.56,.64,1), opacity .2s;
      max-height: 75vh;
    }
    #cb-panel.open {
      transform: scale(1) translateY(0);
      opacity: 1; pointer-events: all;
    }

    #cb-header {
      background: linear-gradient(135deg, #2a1e05, #1a1400);
      border-bottom: 1px solid #3a2e10;
      padding: 14px 16px; display: flex; align-items: center; gap: 10px;
    }
    #cb-header .cb-avatar {
      width: 36px; height: 36px; border-radius: 50%;
      background: linear-gradient(135deg, #b8923c, #8b6914);
      display: flex; align-items: center; justify-content: center;
      font-size: 18px; flex-shrink: 0;
    }
    #cb-header .cb-title { flex: 1; }
    #cb-header .cb-title strong { display: block; color: #e8d5a3; font-size: 14px; }
    #cb-header .cb-title small { color: #7a6035; font-size: 11px; }
    #cb-header .cb-close {
      background: none; border: none; color: #7a6035; font-size: 18px;
      cursor: pointer; padding: 0 4px; transition: color .15s;
    }
    #cb-header .cb-close:hover { color: #e8d5a3; }

    #cb-messages {
      flex: 1; overflow-y: auto; padding: 16px; display: flex;
      flex-direction: column; gap: 12px;
      scrollbar-width: thin; scrollbar-color: #3a2e10 transparent;
    }
    #cb-messages::-webkit-scrollbar { width: 4px; }
    #cb-messages::-webkit-scrollbar-thumb { background: #3a2e10; border-radius: 2px; }

    .cb-msg { display: flex; gap: 8px; align-items: flex-start; animation: cbFadeIn .2s ease; }
    .cb-msg.user { flex-direction: row-reverse; }
    @keyframes cbFadeIn { from { opacity:0; transform:translateY(6px); } to { opacity:1; transform:none; } }

    .cb-bubble {
      max-width: 85%; padding: 10px 13px; border-radius: 12px;
      font-size: 13px; line-height: 1.55; white-space: pre-wrap;
    }
    .cb-msg.bot .cb-bubble {
      background: #252010; color: #e8d5a3; border-radius: 4px 12px 12px 12px;
      border: 1px solid #3a2e10;
    }
    .cb-msg.user .cb-bubble {
      background: linear-gradient(135deg, #b8923c, #8b6914);
      color: #1a1200; border-radius: 12px 4px 12px 12px;
      font-weight: 500;
    }
    .cb-bubble strong { font-weight: 700; color: #d4a843; }
    .cb-bubble .cb-tag-ref {
      display: inline-block; background: rgba(184,146,60,.15);
      color: #d4a843; border-radius: 4px; padding: 1px 6px;
      font-size: 12px; font-weight: 600; font-family: monospace;
    }

    .cb-avatar-sm {
      width: 28px; height: 28px; border-radius: 50%; flex-shrink: 0;
      background: linear-gradient(135deg, #b8923c, #8b6914);
      display: flex; align-items: center; justify-content: center;
      font-size: 14px; margin-top: 2px;
    }
    .cb-msg.user .cb-avatar-sm {
      background: #2a2010;
      color: #b8923c; font-size: 16px;
    }

    .cb-typing { display: flex; gap: 4px; align-items: center; padding: 10px 13px; }
    .cb-typing span {
      width: 7px; height: 7px; border-radius: 50%; background: #b8923c;
      animation: cbDot 1.2s infinite;
    }
    .cb-typing span:nth-child(2) { animation-delay: .2s; }
    .cb-typing span:nth-child(3) { animation-delay: .4s; }
    @keyframes cbDot {
      0%,80%,100% { transform: scale(.6); opacity: .4; }
      40% { transform: scale(1); opacity: 1; }
    }

    #cb-footer {
      padding: 12px 14px; border-top: 1px solid #2a2010;
      display: flex; gap: 8px; background: #120e00;
    }
    #cb-input {
      flex: 1; background: #1e1800; border: 1px solid #3a2e10;
      border-radius: 8px; padding: 9px 12px;
      color: #e8d5a3; font-size: 13px; outline: none;
      resize: none; font-family: inherit; max-height: 120px;
      transition: border-color .15s;
    }
    #cb-input::placeholder { color: #5a4820; }
    #cb-input:focus { border-color: #b8923c; }
    #cb-send {
      width: 38px; height: 38px; border-radius: 8px; border: none;
      background: linear-gradient(135deg, #b8923c, #8b6914);
      color: #1a1200; cursor: pointer; font-size: 18px;
      display: flex; align-items: center; justify-content: center;
      transition: opacity .15s; flex-shrink: 0; align-self: flex-end;
    }
    #cb-send:hover { opacity: .85; }
    #cb-send:disabled { opacity: .4; cursor: default; }

    .cb-suggestions {
      display: flex; flex-wrap: wrap; gap: 6px; padding: 0 16px 12px;
    }
    .cb-sugg {
      background: #252010; border: 1px solid #3a2e10; color: #a08040;
      border-radius: 20px; padding: 5px 12px; font-size: 11px; cursor: pointer;
      transition: background .15s, color .15s; white-space: nowrap;
    }
    .cb-sugg:hover { background: #3a2e10; color: #e8d5a3; }
  `;

  // ── HTML ─────────────────────────────────────────────────────────────────────
  const HTML = `
    <button id="cb-btn" title="Assistant IA" aria-label="Ouvrir l'assistant">
      💬
      <span class="cb-badge" id="cb-badge">1</span>
    </button>

    <div id="cb-panel" role="dialog" aria-label="Assistant de stock">
      <div id="cb-header">
        <div class="cb-avatar">✨</div>
        <div class="cb-title">
          <strong>Assistant Stock</strong>
          <small>Joaillerie — données en temps réel</small>
        </div>
        <button class="cb-close" id="cb-close" title="Fermer">✕</button>
      </div>
      <div id="cb-messages"></div>
      <div class="cb-suggestions" id="cb-sugg-wrap">
        <button class="cb-sugg" data-q="valeur du stock">💰 Valeur stock</button>
        <button class="cb-sugg" data-q="ventes ce mois">📊 Ventes du mois</button>
        <button class="cb-sugg" data-q="chèques en attente">🏦 Chèques</button>
        <button class="cb-sugg" data-q="crédits actifs">💳 Crédits</button>
        <button class="cb-sugg" data-q="aide">❓ Aide</button>
      </div>
      <div id="cb-footer">
        <textarea id="cb-input" placeholder="Ex: où est le 4313 ?" rows="1"></textarea>
        <button id="cb-send" title="Envoyer">➤</button>
      </div>
    </div>
  `;

  // ── Init ─────────────────────────────────────────────────────────────────────
  function init() {
    // Injecter CSS
    const style = document.createElement('style');
    style.textContent = CSS;
    document.head.appendChild(style);

    // Injecter HTML
    const wrap = document.createElement('div');
    wrap.innerHTML = HTML;
    document.body.appendChild(wrap);

    // Refs
    const btn    = document.getElementById('cb-btn');
    const panel  = document.getElementById('cb-panel');
    const msgs   = document.getElementById('cb-messages');
    const input  = document.getElementById('cb-input');
    const send   = document.getElementById('cb-send');
    const close  = document.getElementById('cb-close');
    const badge  = document.getElementById('cb-badge');
    const suggW  = document.getElementById('cb-sugg-wrap');

    let isOpen   = false;
    let unread   = 0;
    let history  = [];
    let welcomed = false;

    // ── Toggle ──────────────────────────────────────────────────────────────
    function openPanel() {
      isOpen = true;
      panel.classList.add('open');
      btn.style.transform = 'scale(.9)';
      unread = 0; badge.style.display = 'none';
      if (!welcomed) {
        welcomed = true;
        addBot(
          "Bonjour ! 👋 Je suis ton assistant de stock.\n" +
          "Dis-moi ce que tu cherches, par exemple :\n" +
          "• « où est le 4313 »\n" +
          "• « crédit de Kamilia »\n" +
          "• « ventes de mars 2026 »\n\n" +
          "Tape **aide** pour voir tout ce que je sais faire."
        );
      }
      setTimeout(() => input.focus(), 300);
    }

    function closePanel() {
      isOpen = false;
      panel.classList.remove('open');
      btn.style.transform = '';
    }

    btn.addEventListener('click', () => isOpen ? closePanel() : openPanel());
    close.addEventListener('click', closePanel);

    // Fermer en cliquant dehors
    document.addEventListener('click', (e) => {
      if (isOpen && !panel.contains(e.target) && e.target !== btn) closePanel();
    });

    // ── Keyboard shortcut : Ctrl+K ──────────────────────────────────────────
    document.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        isOpen ? closePanel() : openPanel();
      }
      if (isOpen && e.key === 'Escape') closePanel();
    });

    // ── Suggestions rapides ─────────────────────────────────────────────────
    suggW.querySelectorAll('.cb-sugg').forEach(b => {
      b.addEventListener('click', () => {
        sendMessage(b.dataset.q);
        suggW.style.display = 'none';
      });
    });

    // ── Send ────────────────────────────────────────────────────────────────
    send.addEventListener('click', () => sendMessage(input.value.trim()));
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage(input.value.trim());
      }
    });
    input.addEventListener('input', () => {
      input.style.height = 'auto';
      input.style.height = Math.min(input.scrollHeight, 120) + 'px';
    });

    async function sendMessage(text) {
      if (!text) return;
      input.value = '';
      input.style.height = 'auto';
      send.disabled = true;
      suggW.style.display = 'none';

      addUser(text);
      const typing = addTyping();

      try {
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: text, history })
        });
        const data = await res.json();
        typing.remove();
        const reply = data.reply || "Désolé, je n'ai pas pu répondre.";
        addBot(reply);
        history.push({ role: 'user', content: text });
        history.push({ role: 'assistant', content: reply });
        if (history.length > 20) history = history.slice(-20);
        if (!isOpen) { unread++; badge.textContent = unread; badge.style.display = 'flex'; }
      } catch (e) {
        typing.remove();
        addBot("❌ Erreur de connexion au serveur.");
      }
      send.disabled = false;
      input.focus();
    }

    // ── Helpers UI ──────────────────────────────────────────────────────────
    function addUser(text) {
      const div = document.createElement('div');
      div.className = 'cb-msg user';
      div.innerHTML = `
        <div class="cb-avatar-sm">👤</div>
        <div class="cb-bubble">${escHtml(text)}</div>
      `;
      msgs.appendChild(div);
      scrollBottom();
    }

    function addBot(text) {
      const div = document.createElement('div');
      div.className = 'cb-msg bot';
      div.innerHTML = `
        <div class="cb-avatar-sm">✨</div>
        <div class="cb-bubble">${formatReply(text)}</div>
      `;
      msgs.appendChild(div);
      scrollBottom();
      return div;
    }

    function addTyping() {
      const div = document.createElement('div');
      div.className = 'cb-msg bot';
      div.innerHTML = `
        <div class="cb-avatar-sm">✨</div>
        <div class="cb-bubble cb-typing"><span></span><span></span><span></span></div>
      `;
      msgs.appendChild(div);
      scrollBottom();
      return div;
    }

    function scrollBottom() {
      msgs.scrollTop = msgs.scrollHeight;
    }

    function escHtml(s) {
      return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }

    function formatReply(text) {
      // Markdown-like formatting
      return escHtml(text)
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        .replace(/#(\d{2,5})\b/g, '<span class="cb-tag-ref">#$1</span>')
        .replace(/\n/g, '<br>');
    }
  }

  // Lance après chargement DOM
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
