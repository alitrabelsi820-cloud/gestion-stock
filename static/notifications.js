/**
 * notifications.js — Bijouterie TRABELSI
 * Petite cloche en haut à droite, point rouge tant que non lu.
 * Vérifié à l'ouverture + toutes les 5 minutes.
 */
(function(){

/* ─── CSS ─────────────────────────────────────────────────────────────────── */
const CSS = `
#notif-wrap {
  position: relative;
  display: inline-flex;
  align-items: center;
  margin-right: 8px;
}
#notif-btn {
  position: relative;
  background: none;
  border: none;
  cursor: pointer;
  padding: 6px 8px;
  border-radius: 8px;
  font-size: 19px;
  line-height: 1;
  transition: background .15s;
  color: #6a6058;
}
#notif-btn:hover { background: #f2ede6; }
#notif-dot {
  position: absolute;
  top: 4px; right: 4px;
  width: 9px; height: 9px;
  background: #e74c3c;
  border-radius: 50%;
  border: 2px solid #fff;
  display: none;
  pointer-events: none;
}
#notif-dot.visible { display: block; }
#notif-panel {
  display: none;
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  width: 340px;
  max-height: 480px;
  background: #fff;
  border: 1px solid #e8e0d4;
  border-radius: 12px;
  box-shadow: 0 6px 32px rgba(0,0,0,.13);
  z-index: 9999;
  flex-direction: column;
  overflow: hidden;
  font-family: 'Segoe UI', Arial, sans-serif;
}
#notif-panel.open { display: flex; }
.np-header {
  padding: 12px 16px;
  font-size: 13px;
  font-weight: 700;
  color: #1a1612;
  border-bottom: 1px solid #f0ebe3;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.np-refresh {
  font-size: 15px;
  cursor: pointer;
  color: #9a8e7e;
  border: none;
  background: none;
  transition: transform .3s;
  padding: 2px 4px;
  border-radius: 4px;
}
.np-refresh:hover { color: #b8923c; transform: rotate(180deg); }
.np-body { overflow-y: auto; flex: 1; }
.np-group-title {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: .8px;
  text-transform: uppercase;
  color: #b8923c;
  padding: 8px 16px 4px;
  background: #faf7f2;
}
.np-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 8px 14px;
  border-bottom: 1px solid #f8f5f0;
  transition: background .12s;
}
.np-item:last-child { border-bottom: none; }
.np-item:hover { background: #fdf8f0; }
.np-icon { font-size: 17px; flex-shrink: 0; margin-top: 1px; }
.np-text { flex: 1; min-width: 0; }
.np-title { font-size: 12px; font-weight: 600; color: #1a1612; line-height: 1.35; }
.np-sub   { font-size: 11px; color: #9a8e7e; margin-top: 1px; line-height: 1.4; }
.np-badge {
  font-size: 10px; font-weight: 700; border-radius: 4px;
  padding: 2px 6px; flex-shrink: 0; align-self: center; white-space: nowrap;
}
.b-red    { background:#fde8e8; color:#c0392b; }
.b-orange { background:#fef3e2; color:#d35400; }
.b-yellow { background:#fefce8; color:#b7791f; }
.b-blue   { background:#e8f0fe; color:#1a56db; }
.np-empty {
  padding: 28px 16px;
  text-align: center;
  color: #9a8e7e;
  font-size: 13px;
}
.np-footer {
  border-top: 1px solid #f0ebe3;
  padding: 8px 14px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
}
.np-footer a {
  text-align: center;
  font-size: 11px;
  color: #7a5c20;
  text-decoration: none;
  padding: 5px;
  border-radius: 6px;
  border: 1px solid #e8e0d4;
  font-weight: 500;
  transition: all .12s;
}
.np-footer a:hover { background: #fdf8f0; border-color: #b8923c; }
.np-time {
  font-size: 10px; color: #c5bdb3; text-align: center;
  padding: 4px 16px 6px;
}
`;

/* ─── HTML ────────────────────────────────────────────────────────────────── */
function inject(){
  const s = document.createElement('style');
  s.textContent = CSS;
  document.head.appendChild(s);

  const wrap = document.createElement('div');
  wrap.id = 'notif-wrap';
  wrap.innerHTML = `
    <button id="notif-btn" onclick="notifToggle()" title="Notifications">
      🔔
      <span id="notif-dot"></span>
    </button>
    <div id="notif-panel">
      <div class="np-header">
        Notifications
        <button class="np-refresh" onclick="notifRefresh(event)" title="Rafraîchir">↻</button>
      </div>
      <div class="np-body" id="np-body"><div class="np-empty">⏳</div></div>
      <div id="np-time" class="np-time"></div>
      <div class="np-footer">
        <a href="/cheques">📋 Chèques</a>
        <a href="/fournisseurs">🏭 Fournisseurs</a>
        <a href="/historique-factures">🧾 Factures</a>
      </div>
    </div>`;

  const logout = document.querySelector('a[href="/logout"]');
  if(logout) logout.parentNode.insertBefore(wrap, logout);
  else document.querySelector('nav')?.appendChild(wrap);

  // Fermer en cliquant dehors
  document.addEventListener('click', e => {
    if(!wrap.contains(e.target))
      document.getElementById('notif-panel')?.classList.remove('open');
  });
}

/* ─── TOGGLE : ouvrir = marquer comme lu ──────────────────────────────────── */
window.notifToggle = function(){
  const panel = document.getElementById('notif-panel');
  panel.classList.toggle('open');
  if(panel.classList.contains('open')){
    // Effacer le point rouge dès l'ouverture
    markSeen();
  }
};

/* ─── VU / PAS VU ─────────────────────────────────────────────────────────── */
// On stocke juste le timestamp de la dernière ouverture
const SEEN_KEY = 'notif_last_seen';
function getLastSeen(){ return parseInt(localStorage.getItem(SEEN_KEY)||'0'); }
function markSeen(){
  localStorage.setItem(SEEN_KEY, Date.now().toString());
  document.getElementById('notif-dot')?.classList.remove('visible');
}

/* ─── REFRESH ─────────────────────────────────────────────────────────────── */
window.notifRefresh = async function(e){
  e?.stopPropagation();
  const btn = e?.currentTarget;
  if(btn){ btn.style.transform='rotate(360deg)'; setTimeout(()=>btn.style.transform='',400); }
  await loadAndRender();
};

/* ─── HELPERS DATE ────────────────────────────────────────────────────────── */
const todayStr    = () => new Date().toISOString().slice(0,10);
const tomorrowStr = () => { const d=new Date(); d.setDate(d.getDate()+1); return d.toISOString().slice(0,10); };
const daysDiff    = s  => { if(!s) return null; return Math.floor((new Date(todayStr())-new Date(s))/86400000); };
const fmtD        = s  => { if(!s) return '—'; const [y,m,j]=s.split('-'); return `${j}/${m}/${y}`; };
const fmtM        = n  => n==null ? '—' : Number(n).toLocaleString('fr-FR')+' MAD';

/* ─── DISMISS NOTIF ISMAIL ────────────────────────────────────────────────── */
window.dismissNotifIsmail = async function(id, btn){
  btn.disabled = true;
  btn.textContent = '✓';
  try{
    await fetch(`/api/notifs/${id}`, {method:'DELETE'});
    await loadAndRender();
  } catch(e){ btn.disabled = false; btn.textContent = 'Lu'; }
};

/* ─── CALCUL DES ALERTES ──────────────────────────────────────────────────── */
async function computeNotifs(){
  const notifs = [];
  try{
    const [ch, fo, fa, ismail] = await Promise.all([
      fetch('/api/cheques').then(r=>r.json()).catch(()=>[]),
      fetch('/api/fournisseurs').then(r=>r.json()).catch(()=>[]),
      fetch('/api/factures').then(r=>r.json()).catch(()=>[]),
      fetch('/api/notifs').then(r=>r.json()).catch(()=>[]),
    ]);

    // ── 0. RAPPELS ISMAIL ────────────────────────────────────────────────────
    for(const n of ismail||[]){
      notifs.push({
        g: 'ismail', p: 0,
        icon: '💎', badge: 'Ismail', bc: 'b-orange',
        title: `Bénéfice Ismail — Article #${n.ref} · ${n.article}`,
        sub: `Vendu à ${n.client} le ${fmtD(n.date)} · Penser à régler Ismail`,
        _id: n.id
      });
    }
    const tod = todayStr(), tom = tomorrowStr();

    // ── 1. CHÈQUES ──────────────────────────────────────────────────────────
    for(const c of ch||[]){
      if(c.statut === 'rejeté'){
        notifs.push({ g:'cheques', p:0, icon:'🚨', badge:'REJETÉ', bc:'b-red',
          title:`Chèque rejeté — ${c.client||'—'}`,
          sub:`N° ${c.numero||'—'} · ${fmtM(c.montant)} · ${c.banque||'—'} · Recontacter le client` });
        continue;
      }
      if(c.statut !== 'en_attente') continue;
      const dates = (c.dates_encaissement?.length>0) ? c.dates_encaissement : (c.date_encaissement?[c.date_encaissement]:[]);
      dates.forEach((dt,i)=>{
        if(!dt) return;
        const nb  = c.nb_cheques>1 ? ` · Chèque ${i+1}/${c.nb_cheques}` : '';
        const mnt = c.nb_cheques>1 ? c.montant/c.nb_cheques : c.montant;
        if(dt < tod){
          const j = daysDiff(dt);
          notifs.push({ g:'cheques', p:1, icon:'🔴', badge:`${j}j retard`, bc:'b-red',
            title:`Encaissement en retard — ${c.client||'—'}${nb}`,
            sub:`${fmtM(mnt)} · Prévu le ${fmtD(dt)} · ${c.banque||'—'}` });
        } else if(dt === tod){
          notifs.push({ g:'cheques', p:2, icon:'🟠', badge:"Aujourd'hui", bc:'b-orange',
            title:`Encaissement aujourd'hui — ${c.client||'—'}${nb}`,
            sub:`${fmtM(mnt)} · N° ${c.numero||'—'} · ${c.banque||'—'}` });
        } else if(dt === tom){
          notifs.push({ g:'cheques', p:3, icon:'🟡', badge:'Demain', bc:'b-yellow',
            title:`Encaissement demain — ${c.client||'—'}${nb}`,
            sub:`${fmtM(mnt)} · N° ${c.numero||'—'} · ${c.banque||'—'}` });
        }
      });
    }

    // ── 2. FOURNISSEURS ─────────────────────────────────────────────────────
    for(const f of fo||[]){
      if(f.statut==='soldé') continue;
      const age = daysDiff(f.date_achat);
      if(!age || age < 14) continue;
      const nom = f.fournisseur||f.client||'—';
      notifs.push({ g:'fournisseurs', p: age>=60?1:2,
        icon: age>=60?'🔴':'🟠', badge:`${age}j`, bc: age>=60?'b-red':'b-orange',
        title:`Dette fournisseur — ${nom}`,
        sub:`Reste: ${fmtM(f.reste)} · Depuis le ${fmtD(f.date_achat)}` });
    }

    // ── 4. FACTURES NON SOLDÉES ─────────────────────────────────────────────
    for(const f of fa||[]){
      const reste = (f.total||0)-(f.avance||0);
      if(reste <= 0) continue;
      const age = daysDiff(f.date||f.created_at);
      if(!age || age < 7) continue;
      notifs.push({ g:'factures', p: age>=30?1:3,
        icon: age>=30?'🔴':'🔵', badge: age>=30?`${age}j`:'À récupérer', bc: age>=30?'b-red':'b-blue',
        title:`Facture non soldée — ${f.client||'—'}`,
        sub:`${f.numero||'—'} · Reste: ${fmtM(reste)} · Émise le ${fmtD(f.date||f.created_at)}` });
    }

  }catch(e){ console.warn('[Notifs]',e); }

  notifs.sort((a,b)=>a.p-b.p);
  return notifs;
}

/* ─── RENDU DU PANNEAU ────────────────────────────────────────────────────── */
const G_LABELS = {
  ismail: '💎 Ismail — À régler',
  cheques:'📋 Chèques',
  fournisseurs:'🏭 Fournisseurs', factures:'🧾 Factures'
};

function render(notifs){
  const body = document.getElementById('np-body');
  if(!body) return;
  if(!notifs.length){
    body.innerHTML = `<div class="np-empty">✅ Tout est en ordre</div>`;
    return;
  }
  const groups = {};
  notifs.forEach(n=>{ (groups[n.g]=groups[n.g]||[]).push(n); });
  let html = '';
  ['ismail','cheques','fournisseurs','factures'].forEach(g=>{
    if(!groups[g]) return;
    html += `<div class="np-group-title">${G_LABELS[g]} · ${groups[g].length}</div>`;
    groups[g].forEach(n=>{
      const dismissBtn = (g === 'ismail')
        ? `<button onclick="dismissNotifIsmail(${n._id}, this)" style="margin-left:6px;font-size:11px;padding:3px 8px;border:1px solid #d35400;background:#fef3e2;color:#d35400;border-radius:5px;cursor:pointer;font-weight:600;white-space:nowrap">Marquer lu</button>`
        : '';
      html += `<div class="np-item">
        <span class="np-icon">${n.icon}</span>
        <div class="np-text">
          <div class="np-title">${n.title}</div>
          <div class="np-sub">${n.sub}</div>
        </div>
        <div style="display:flex;flex-direction:column;align-items:flex-end;gap:4px;flex-shrink:0">
          <span class="np-badge ${n.bc}">${n.badge}</span>
          ${dismissBtn}
        </div>
      </div>`;
    });
  });
  body.innerHTML = html;
}

/* ─── LOAD & RENDER ───────────────────────────────────────────────────────── */
async function loadAndRender(){
  const notifs = await computeNotifs();
  render(notifs);
  // Point rouge uniquement si nouvelles alertes depuis la dernière ouverture
  const dot = document.getElementById('notif-dot');
  if(dot) dot.classList.toggle('visible', notifs.length > 0);
  // Heure de vérif
  const t = document.getElementById('np-time');
  if(t) t.textContent = `Vérifié à ${new Date().toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'})}`;
}

/* ─── INIT ────────────────────────────────────────────────────────────────── */
async function init(){
  inject();
  await loadAndRender();
  setInterval(loadAndRender, 5 * 60 * 1000); // toutes les 5 min
}

if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',init);
else init();

})();
