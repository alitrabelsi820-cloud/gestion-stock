#!/usr/bin/env python3
"""
Serveur de Gestion de Stock - Joaillerie
Fonctionne sur Mac sans aucune installation supplémentaire.
"""

import base64
import http.server
import json
import os
import re
import secrets
import threading
import urllib.parse
import urllib.request
import hmac
import hashlib
import webbrowser
from datetime import datetime
from pathlib import Path

import database as db

BASE_DIR   = Path(__file__).parent
DATA_DIR   = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"

# Port : Railway injecte la variable PORT, sinon 5500 en local
PORT = int(os.environ.get("PORT", 5500))

# ─── Photos : Cloudflare R2 (ou dossier local en fallback) ───────────────────
# En production : définir R2_PUBLIC_URL dans les variables d'environnement Railway
# Ex: https://pub-xxxx.r2.dev  ou  https://photos.bijouterie-trabelsi.com
R2_PUBLIC_URL    = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")
R2_ACCOUNT_ID    = os.environ.get("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY    = os.environ.get("R2_ACCESS_KEY", "")
R2_SECRET_KEY    = os.environ.get("R2_SECRET_KEY", "")
R2_BUCKET_NAME   = os.environ.get("R2_BUCKET_NAME", "bijouterie-photos")

# Dossier local (fallback Mac)
PHOTOS_DIR_LOCAL = Path("/Users/mac/Library/CloudStorage/OneDrive-Personnel(2)/BIjouterie -VF 2/5-Photos(PNG)-1")
PHOTOS_DIR_COMPRESSED = BASE_DIR / "photos_compressed"

# ─── Authentification — deux rôles ────────────────────────────────────────────
# Mots de passe depuis variables d'environnement (ou valeurs par défaut en local)
MOT_DE_PASSE_ADMIN   = os.environ.get("MOT_DE_PASSE_ADMIN",   "7868")
MOT_DE_PASSE_EMPLOYE = os.environ.get("MOT_DE_PASSE_EMPLOYE", "    ")

# token → "admin" | "employe"
SESSIONS = {}

def get_session_token(headers):
    cookie = headers.get("Cookie", "")
    for part in cookie.split(";"):
        part = part.strip()
        if part.startswith("session="):
            return part[len("session="):]
    return None

def get_role(headers):
    """Retourne 'admin', 'employe', ou None si non connecté."""
    token = get_session_token(headers)
    return SESSIONS.get(token)

def is_authenticated(headers):
    return get_role(headers) is not None

def is_admin(headers):
    return get_role(headers) == "admin"

def redirect_login(handler):
    handler.send_response(302)
    handler.send_header("Location", "/login")
    handler.end_headers()



# ─── Chargement / Sauvegarde (délégués à database.py / SQLite) ───────────────

load_articles    = db.load_articles
save_articles    = db.save_articles
load_ventes      = db.load_ventes
save_ventes      = db.save_ventes
load_credits     = db.load_credits
save_credits     = db.save_credits
load_notifs      = db.load_notifs
save_notifs      = db.save_notifs
load_fournisseurs= db.load_fournisseurs
save_fournisseurs= db.save_fournisseurs
load_cheques     = db.load_cheques
save_cheques     = db.save_cheques
load_factures    = db.load_factures
save_factures    = db.save_factures
load_config      = db.load_config
save_config      = db.save_config

def is_poids_article(article):
    """Un article est 'vente au poids' s'il n'a aucune pierre (D/EM/R/S/p_fines/rosaces/em_clb/perles)."""
    pierres = ["d", "em", "r", "s", "p_fines", "rosaces", "em_clb", "perles"]
    return not any(article.get(p) for p in pierres)

def recalc_credit(c):
    """Recalcule reste et statut d'un crédit/fournisseur à partir des paiements."""
    total_paye = round(sum(p.get("montant", 0) for p in c.get("paiements", [])), 2)
    reste = round(c.get("montant_total", 0) - total_paye, 2)
    c["reste"] = max(reste, 0)
    if c["reste"] <= 0:
        c["statut"] = "solde"
    elif total_paye > 0:
        c["statut"] = "avance"
    else:
        c["statut"] = "rien"
    return c

def find_photo_url(ref):
    """Retourne une URL signée R2 (7 jours) si les credentials sont configurés, sinon None."""
    if not (R2_ACCESS_KEY and R2_SECRET_KEY and R2_ACCOUNT_ID):
        return None
    try:
        import boto3, warnings
        warnings.filterwarnings("ignore")
        s3 = boto3.client(
            "s3",
            endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
            aws_access_key_id=R2_ACCESS_KEY,
            aws_secret_access_key=R2_SECRET_KEY,
            region_name="auto",
        )
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": R2_BUCKET_NAME, "Key": f"{ref}.jpg"},
            ExpiresIn=7 * 24 * 3600,  # 7 jours
        )
        return url
    except Exception:
        return None

def _upload_to_r2(filename, data_bytes):
    """Upload un fichier vers Cloudflare R2 via l'API S3-compatible."""
    from datetime import datetime as dt
    now = dt.utcnow()
    date_stamp = now.strftime("%Y%m%d")
    amz_date   = now.strftime("%Y%m%dT%H%M%SZ")
    region     = "auto"
    service    = "s3"
    host       = f"{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
    endpoint   = f"https://{host}/{R2_BUCKET_NAME}/{filename}"

    content_type = "image/jpeg"
    payload_hash = hashlib.sha256(data_bytes).hexdigest()

    headers_to_sign = f"content-type;host;x-amz-content-sha256;x-amz-date"
    canonical = (
        f"PUT\n/{R2_BUCKET_NAME}/{filename}\n\n"
        f"content-type:{content_type}\nhost:{host}\n"
        f"x-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n\n"
        f"{headers_to_sign}\n{payload_hash}"
    )
    string_to_sign = (
        f"AWS4-HMAC-SHA256\n{amz_date}\n"
        f"{date_stamp}/{region}/{service}/aws4_request\n"
        + hashlib.sha256(canonical.encode()).hexdigest()
    )
    def _hmac(key, msg):
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()
    signing_key = _hmac(
        _hmac(_hmac(_hmac(f"AWS4{R2_SECRET_KEY}".encode(), date_stamp), region), service),
        "aws4_request"
    )
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    auth = (
        f"AWS4-HMAC-SHA256 Credential={R2_ACCESS_KEY}/{date_stamp}/{region}/{service}/aws4_request,"
        f"SignedHeaders={headers_to_sign},Signature={signature}"
    )
    req = urllib.request.Request(endpoint, data=data_bytes, method="PUT")
    req.add_header("Content-Type", content_type)
    req.add_header("x-amz-date", amz_date)
    req.add_header("x-amz-content-sha256", payload_hash)
    req.add_header("Authorization", auth)
    with urllib.request.urlopen(req) as resp:
        if resp.status not in (200, 204):
            raise Exception(f"R2 upload failed: {resp.status}")

def find_photo_local(ref):
    """Cherche la photo localement (Mac) — fallback si pas de R2."""
    # D'abord dans photos_compressed (JPEG)
    p = PHOTOS_DIR_COMPRESSED / f"{ref}.jpg"
    if p.exists():
        return p
    # Puis dans OneDrive (PNG original)
    if PHOTOS_DIR_LOCAL.exists():
        p = PHOTOS_DIR_LOCAL / f"{ref}.png"
        if p.exists():
            return p
        for zpad in range(1, 6):
            p = PHOTOS_DIR_LOCAL / f"{str(ref).zfill(zpad)}.png"
            if p.exists():
                return p
    return None

def calc_stats(articles):
    return {
        "nb_articles": len(articles),
        "total_or": round(sum(a["or_grs"] or 0 for a in articles), 2),
        "valeur_stock": round(sum(a["pa"] or 0 for a in articles), 0),
        "diamants": round(sum(a["d"] or 0 for a in articles), 2),
        "emeraudes": round(sum(a["em"] or 0 for a in articles), 2),
        "rubis": round(sum(a["r"] or 0 for a in articles), 2),
        "saphirs": round(sum(a["s"] or 0 for a in articles), 2),
        "rosaces": round(sum(a["rosaces"] or 0 for a in articles), 2),
        "em_clb": round(sum(a["em_clb"] or 0 for a in articles), 2),
        "perles": round(sum(a["perles"] or 0 for a in articles), 2),
    }

def ventes_stats(ventes, date_from=None, date_to=None):
    """Stats ventes filtrées par période."""
    filt = ventes
    if date_from:
        filt = [v for v in filt if (v.get("date_vente") or "") >= date_from]
    if date_to:
        filt = [v for v in filt if (v.get("date_vente") or "") <= date_to]
    return {
        "nb": len(filt),
        "ca": round(sum(v.get("pv") or 0 for v in filt), 0),
        "benef": round(sum(v.get("benef") or 0 for v in filt), 0),
        "or_vendu": round(sum(v.get("or_grs") or 0 for v in filt), 2),
    }

def monthly_stats(ventes):
    """Regroupe les ventes par mois, retourne liste triée."""
    months = {}
    for v in ventes:
        d = (v.get("date_vente") or "")[:7]
        if not d:
            continue
        if d not in months:
            months[d] = {"mois": d, "nb": 0, "ca": 0, "benef": 0, "or_vendu": 0}
        months[d]["nb"] += 1
        months[d]["ca"] += v.get("pv") or 0
        months[d]["benef"] += v.get("benef") or 0
        months[d]["or_vendu"] += v.get("or_grs") or 0
    result = sorted(months.values(), key=lambda x: x["mois"], reverse=True)
    for m in result:
        m["ca"] = round(m["ca"], 0)
        m["benef"] = round(m["benef"], 0)
        m["or_vendu"] = round(m["or_vendu"], 2)
    return result

def annual_stats(ventes):
    """Regroupe les ventes par année."""
    years = {}
    for v in ventes:
        d = (v.get("date_vente") or "")[:4]
        if not d:
            continue
        if d not in years:
            years[d] = {"annee": d, "nb": 0, "ca": 0, "benef": 0}
        years[d]["nb"] += 1
        years[d]["ca"] += v.get("pv") or 0
        years[d]["benef"] += v.get("benef") or 0
    result = sorted(years.values(), key=lambda x: x["annee"], reverse=True)
    for y in result:
        y["ca"] = round(y["ca"], 0)
        y["benef"] = round(y["benef"], 0)
    return result

def parse_float(val, min_val=None, max_val=None):
    """Convertit une valeur en float, None si vide. Rejette les valeurs hors limites."""
    if val in (None, ""):
        return None
    try:
        f = float(val)
        if min_val is not None and f < min_val:
            return None
        if max_val is not None and f > max_val:
            return None
        return f
    except:
        return None

def parse_positive(val, field="Montant"):
    """Parse un float strictement positif, lève ValueError si invalide."""
    if val in (None, ""):
        raise ValueError(f"{field} manquant")
    try:
        f = float(val)
    except:
        raise ValueError(f"{field} invalide")
    if f <= 0:
        raise ValueError(f"{field} doit être supérieur à 0")
    if f > 100_000_000:
        raise ValueError(f"{field} semble incorrect (trop élevé)")
    return f

def build_article(data, ref_override=None):
    """Construit un dict article depuis données POST/PUT."""
    ref = ref_override if ref_override is not None else data.get("id")
    return {
        "id": int(ref),
        "date": data.get("date") or datetime.now().strftime("%Y-%m-%d"),
        "article": str(data.get("article", "")).strip(),
        "or_grs": parse_float(data.get("or_grs")),
        "pa": parse_float(data.get("pa")),
        "d": parse_float(data.get("d")),
        "em": parse_float(data.get("em")),
        "r": parse_float(data.get("r")),
        "s": parse_float(data.get("s")),
        "p_fines": parse_float(data.get("p_fines")),
        "rosaces": parse_float(data.get("rosaces")),
        "em_clb": parse_float(data.get("em_clb")),
        "perles": parse_float(data.get("perles")),
        "fabricant": (str(data["fabricant"]).strip() if data.get("fabricant") else None),
        "ismail_pierres": bool(data.get("ismail_pierres", False)),
    }


# ─── Logs d'accès ─────────────────────────────────────────────────────────────

ACCESS_LOGS = []   # stocké en mémoire (réinitialisé au redémarrage)
MAX_LOGS = 300

# Chemins ignorés — jamais loggés
SKIP_PATHS = {'/logs', '/favicon.ico', '/login', '/logout'}

PAGE_LABELS = {
    '/': 'Accueil', '/stock': 'Stock', '/vendu': 'Ventes',
    '/credit': 'Crédits', '/fournisseurs': 'Fournisseurs',
    '/dashboard': 'Dashboard', '/fiche': 'Fiche article',
    '/facture': 'Facture', '/employe': 'Vue employé',
}

def parse_ua(ua):
    """Extrait appareil + navigateur depuis le User-Agent."""
    u = ua.lower()
    # Appareil
    if 'iphone' in u:   device = '📱 iPhone'
    elif 'ipad' in u:   device = '📟 iPad'
    elif 'android' in u and 'mobile' in u: device = '📱 Android'
    elif 'android' in u: device = '📟 Tablette Android'
    else:               device = '💻 Ordinateur'
    # Navigateur
    if 'edg/' in u:       browser = 'Edge'
    elif 'opr/' in u or 'opera' in u: browser = 'Opera'
    elif 'chrome/' in u:  browser = 'Chrome'
    elif 'firefox/' in u: browser = 'Firefox'
    elif 'safari/' in u:  browser = 'Safari'
    else:                 browser = 'Navigateur inconnu'
    return device, browser

def record_log(ip, port, path, ua):
    """Enregistre un accès réel — déduplique les visites répétées (5 min)."""
    if path.startswith('/api/') or path.startswith('/static/'):
        return
    if path in SKIP_PATHS:
        return
    now = datetime.now()
    # Déduplique : même IP + même page dans les 5 dernières minutes → on ignore
    cutoff = now.timestamp() - 300
    for e in reversed(ACCESS_LOGS):
        if e["raw_ts"] < cutoff:
            break
        if e["ip"] == ip and e["page"] == path:
            return
    device, browser = parse_ua(ua)
    ACCESS_LOGS.append({
        "ts":     now.strftime("%d/%m/%Y %H:%M:%S"),
        "raw_ts": now.timestamp(),
        "ip":     ip,
        "port":   port,
        "page":   path,
        "device": device,
        "browser":browser,
    })
    if len(ACCESS_LOGS) > MAX_LOGS:
        ACCESS_LOGS.pop(0)


# ─── Chatbot ──────────────────────────────────────────────────────────────────

def _fmt_mad(v):
    """Formate un nombre en MAD."""
    try: return f"{int(v):,} MAD".replace(",", " ")
    except: return str(v)

def _fmt_date(d):
    """Formate une date ISO en français."""
    if not d: return "—"
    try:
        dt = datetime.strptime(d[:10], "%Y-%m-%d")
        mois = ["jan","fév","mar","avr","mai","jun","jul","aoû","sep","oct","nov","déc"]
        return f"{dt.day} {mois[dt.month-1]} {dt.year}"
    except: return d

def handle_chat(message):
    """Assistant intelligent pour la gestion de stock joaillerie."""
    msg = message.lower()
    arts = load_articles()
    ventes = load_ventes()
    credits = load_credits()
    cheques = load_cheques()
    fournisseurs = load_fournisseurs()

    # ── Extraire les références numériques (2-5 chiffres) ─────────────────────
    refs = []
    for m in re.finditer(r'#?(\d{2,5})', message):
        n = int(m.group(1))
        if n < 99999:
            refs.append(n)
    ref = refs[0] if refs else None

    # ── Extraire un nom de client / fournisseur ────────────────────────────────
    # Chercher des mots capitalisés ou après "de", "client", "pour"
    name_match = re.search(
        r'(?:client|de|pour|du|crédits?|chèques?\s+de|ismail|driss|hicham)\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s]{1,30})',
        message, re.IGNORECASE)
    client_query = name_match.group(1).strip().lower() if name_match else None

    # ── AIDE ──────────────────────────────────────────────────────────────────
    if any(w in msg for w in ['aide', 'help', 'question', 'quoi demander', 'que peux-tu', 'que sais-tu']):
        return (
            "Je peux t'aider avec :\n"
            "• **Article** : « où est le 4313 » / « article 4313 »\n"
            "• **Ventes** : « quand a été vendu le 4313 » / « ventes de mars »\n"
            "• **Stock** : « combien d'articles » / « valeur du stock » / « articles Bague »\n"
            "• **Client** : « historique de Kamilia » / « crédit de Samira »\n"
            "• **Chèques** : « chèques de Ali » / « chèques non encaissés »\n"
            "• **Stats** : « bénéfice du mois » / « CA mars 2026 »\n"
            "• **Ismail** : « articles Ismail » / « articles Driss »\n"
            "Pose-moi ta question !"
        )

    # ── ARTICLE SPÉCIFIQUE ────────────────────────────────────────────────────
    if ref and any(w in msg for w in ['article', 'réf', 'ref', 'où', 'ou est', 'cherche', 'trouv', 'stock']):
        art = next((a for a in arts if a['id'] == ref), None)
        if art:
            pierres = []
            for k,lbl in [('d','Diamants'),('em','Émeraudes'),('r','Rubis'),('s','Saphirs'),
                          ('p_fines','P.Fines'),('rosaces','Rosaces'),('em_clb','Ém.Col.'),('perles','Perles')]:
                if art.get(k): pierres.append(f"{lbl}: {art[k]} cts")
            ismail = " 💎 Ismail" if art.get('ismail_pierres') else ""
            fab = f" | Fabricant: {art['fabricant']}" if art.get('fabricant') else ""
            return (
                f"✅ L'article #{ref} est **en stock**.\n"
                f"Type: {art.get('article','—')}{ismail}{fab}\n"
                f"OR: {art.get('or_grs','—')} grs | PA: {_fmt_mad(art.get('pa',0))}\n"
                f"Entré le: {_fmt_date(art.get('date'))}"
                + (f"\nPierres: {', '.join(pierres)}" if pierres else "")
            )
        # Pas en stock → chercher dans les ventes
        ventes_ref = [v for v in ventes if v.get('ref') == ref]
        if ventes_ref:
            v = ventes_ref[-1]
            return (
                f"📦 L'article #{ref} a été **vendu**.\n"
                f"Type: {v.get('article','—')}\n"
                f"Vendu le: {_fmt_date(v.get('date_vente'))} | PV: {_fmt_mad(v.get('pv',0))}\n"
                f"Bénéfice: {_fmt_mad(v.get('benef',0))} | Client: {v.get('client') or '—'}"
            )
        return f"❌ L'article #{ref} est introuvable en stock et dans les ventes."

    # ── VENTE D'UN ARTICLE SPÉCIFIQUE ─────────────────────────────────────────
    if ref and any(w in msg for w in ['vendu', 'vente', 'vendue', 'quand', 'prix de vente', 'pv']):
        ventes_ref = [v for v in ventes if v.get('ref') == ref]
        if not ventes_ref:
            # Peut-être encore en stock
            art = next((a for a in arts if a['id'] == ref), None)
            if art:
                return f"L'article #{ref} ({art.get('article','')}) n'a pas encore été vendu — il est toujours en stock."
            return f"Aucune vente trouvée pour l'article #{ref}."
        lines = [f"📋 Vente(s) de l'article #{ref} :"]
        for v in ventes_ref:
            lines.append(
                f"• {_fmt_date(v.get('date_vente'))} — PV: {_fmt_mad(v.get('pv',0))} | "
                f"Bénéf: {_fmt_mad(v.get('benef',0))} | Client: {v.get('client') or '—'}"
            )
        return "\n".join(lines)

    # ── RECHERCHE SANS REF : juste le numéro ─────────────────────────────────
    if ref and not any(w in msg for w in ['vente','vendu','stock','article','réf','ref','crédit','chèque','client','mois','benefice','ca ']):
        art = next((a for a in arts if a['id'] == ref), None)
        if art:
            return (
                f"✅ #{ref} est **en stock** — {art.get('article','—')}, "
                f"{art.get('or_grs','—')} grs, PA: {_fmt_mad(art.get('pa',0))}, "
                f"entré le {_fmt_date(art.get('date'))}"
            )
        ventes_ref = [v for v in ventes if v.get('ref') == ref]
        if ventes_ref:
            v = ventes_ref[-1]
            return (
                f"📦 #{ref} a été **vendu** le {_fmt_date(v.get('date_vente'))} "
                f"pour {_fmt_mad(v.get('pv',0))} (client: {v.get('client') or '—'})"
            )
        return f"❌ Aucun article #{ref} trouvé."

    # ── STOCK GÉNÉRAL ─────────────────────────────────────────────────────────
    if any(w in msg for w in ['valeur du stock', 'total stock', 'stock total', 'valeur stock']):
        total_pa = sum(a.get('pa') or 0 for a in arts)
        total_or = sum(a.get('or_grs') or 0 for a in arts)
        return (
            f"💰 Valeur totale du stock :\n"
            f"• {len(arts)} article(s)\n"
            f"• OR total : {total_or:,.1f} grs\n"
            f"• PA total : {_fmt_mad(total_pa)}"
        )

    if any(w in msg for w in ['combien d\'article', 'nb article', 'nombre d\'article', 'articles en stock']):
        types = {}
        for a in arts:
            t = a.get('article','?')
            types[t] = types.get(t, 0) + 1
        top = sorted(types.items(), key=lambda x: -x[1])[:5]
        lines = [f"📦 {len(arts)} article(s) en stock :"]
        for t,n in top:
            lines.append(f"  • {t} : {n}")
        return "\n".join(lines)

    # ── RECHERCHE PAR TYPE D'ARTICLE ──────────────────────────────────────────
    types_connus = list(set(a.get('article','') for a in arts))
    type_trouve = None
    for t in types_connus:
        if t and t.lower() in msg:
            type_trouve = t
            break
    if type_trouve:
        filtres = [a for a in arts if a.get('article','').lower() == type_trouve.lower()]
        if not filtres:
            return f"Aucun article de type « {type_trouve} » en stock."
        lines = [f"📦 {len(filtres)} article(s) « {type_trouve} » en stock :"]
        for a in filtres[:10]:
            lines.append(f"  • #{a['id']} — {a.get('or_grs','?')} grs, PA: {_fmt_mad(a.get('pa',0))}")
        if len(filtres) > 10:
            lines.append(f"  ... et {len(filtres)-10} autres.")
        return "\n".join(lines)

    # ── ARTICLES ISMAIL / FABRICANT ───────────────────────────────────────────
    if 'ismail' in msg and any(w in msg for w in ['article', 'stock', 'ref', 'réf', 'liste']):
        filtres = [a for a in arts if a.get('ismail_pierres')]
        if not filtres:
            return "Aucun article en stock avec des pierres d'Ismail."
        lines = [f"💎 {len(filtres)} article(s) avec pierres d'Ismail :"]
        for a in filtres[:15]:
            lines.append(f"  • #{a['id']} — {a.get('article','?')}, {a.get('or_grs','?')} grs, PA: {_fmt_mad(a.get('pa',0))}")
        return "\n".join(lines)

    for fab in ['driss', 'hicham']:
        if fab in msg:
            filtres = [a for a in arts if (a.get('fabricant','') or '').lower() == fab]
            if not filtres:
                return f"Aucun article en stock fabriqué par {fab.capitalize()}."
            lines = [f"🔨 {len(filtres)} article(s) de {fab.capitalize()} :"]
            for a in filtres[:15]:
                lines.append(f"  • #{a['id']} — {a.get('article','?')}, {a.get('or_grs','?')} grs")
            return "\n".join(lines)

    # ── VENTES PAR PÉRIODE ────────────────────────────────────────────────────
    mois_fr = {
        'janvier':'01','février':'02','fevrier':'02','mars':'03','avril':'04',
        'mai':'05','juin':'06','juillet':'07','août':'08','aout':'08',
        'septembre':'09','octobre':'10','novembre':'11','décembre':'12','decembre':'12'
    }
    mois_trouve = None
    annee_trouve = None
    for m_nom, m_num in mois_fr.items():
        if m_nom in msg:
            mois_trouve = m_num
            break
    annee_match = re.search(r'20(2[0-9])', msg)
    if annee_match:
        annee_trouve = annee_match.group(0)

    if mois_trouve or annee_trouve or any(w in msg for w in ['ce mois', 'mois-ci', 'mois dernier', 'aujourd']):
        now = datetime.now()
        if 'mois dernier' in msg:
            m = now.month - 1 or 12
            y = now.year if now.month > 1 else now.year - 1
            prefix = f"{y}-{m:02d}"
        elif any(w in msg for w in ['ce mois', 'mois-ci', 'aujourd']):
            prefix = now.strftime("%Y-%m")
        elif mois_trouve and annee_trouve:
            prefix = f"{annee_trouve}-{mois_trouve}"
        elif mois_trouve:
            prefix = f"{now.year}-{mois_trouve}"
        elif annee_trouve:
            prefix = annee_trouve[:4]
        else:
            prefix = now.strftime("%Y-%m")

        filt = [v for v in ventes if (v.get('date_vente','') or '').startswith(prefix)]
        if not filt:
            return f"Aucune vente trouvée pour la période « {prefix} »."
        ca = sum(v.get('pv') or 0 for v in filt)
        benef = sum(v.get('benef') or 0 for v in filt)
        lines = [f"📊 {len(filt)} vente(s) pour {prefix} :"]
        lines.append(f"• CA : {_fmt_mad(ca)} | Bénéfice : {_fmt_mad(benef)}")
        for v in filt[-5:]:
            lines.append(f"  • #{v.get('ref')} {v.get('article','?')} — {_fmt_mad(v.get('pv',0))} ({_fmt_date(v.get('date_vente'))})")
        return "\n".join(lines)

    # ── DERNIÈRES VENTES ──────────────────────────────────────────────────────
    if any(w in msg for w in ['dernière', 'dernier', 'dernières', 'récentes', 'recentes', 'ventes récentes']):
        n = 5
        n_match = re.search(r'(\d+)\s*(dernière|vente)', msg)
        if n_match: n = min(int(n_match.group(1)), 20)
        recent = sorted(ventes, key=lambda v: v.get('date_vente',''), reverse=True)[:n]
        lines = [f"📋 {n} dernière(s) vente(s) :"]
        for v in recent:
            lines.append(
                f"  • #{v.get('ref')} {v.get('article','?')} — {_fmt_mad(v.get('pv',0))} "
                f"le {_fmt_date(v.get('date_vente'))} | Client: {v.get('client') or '—'}"
            )
        return "\n".join(lines)

    # ── HISTORIQUE CLIENT ─────────────────────────────────────────────────────
    def search_client(query):
        """Retourne toutes les ventes/crédits/chèques d'un client."""
        q = query.lower()
        v_client = [v for v in ventes if q in (v.get('client','') or '').lower()]
        c_client = [c for c in credits if q in (c.get('client','') or '').lower()]
        ch_client = [ch for ch in cheques if q in (ch.get('client','') or '').lower()]
        return v_client, c_client, ch_client

    if client_query or any(w in msg for w in ['historique', 'client']):
        query = client_query or ''
        if not query:
            # Essayer d'extraire un nom sans mot-clé
            words = [w for w in message.split() if len(w) > 3 and w[0].isupper()]
            query = words[0].lower() if words else ''
        if query:
            v_cl, c_cl, ch_cl = search_client(query)
            if not v_cl and not c_cl and not ch_cl:
                return f"Aucun résultat trouvé pour « {query} »."
            lines = [f"👤 Historique de « {query.title()} » :"]
            if v_cl:
                ca = sum(v.get('pv') or 0 for v in v_cl)
                lines.append(f"\n🛍️ {len(v_cl)} vente(s) — Total: {_fmt_mad(ca)}")
                for v in v_cl[-4:]:
                    lines.append(f"  • #{v.get('ref')} {v.get('article','?')} — {_fmt_mad(v.get('pv',0))} le {_fmt_date(v.get('date_vente'))}")
            if c_cl:
                lines.append(f"\n💳 {len(c_cl)} crédit(s) :")
                for c in c_cl:
                    lines.append(f"  • {_fmt_mad(c.get('montant_total',0))} — Reste: {_fmt_mad(c.get('reste',0))} ({c.get('statut','?')})")
            if ch_cl:
                lines.append(f"\n🏦 {len(ch_cl)} chèque(s) :")
                for ch in ch_cl:
                    st = '✅ Encaissé' if ch.get('statut')=='encaisse' else '⏳ En attente'
                    lines.append(f"  • {_fmt_mad(ch.get('montant',0))} — {st}")
            return "\n".join(lines)

    # ── CRÉDITS ───────────────────────────────────────────────────────────────
    if any(w in msg for w in ['crédit', 'credit', 'reste', 'solde', 'doit', 'dettes']):
        if client_query:
            filtres = [c for c in credits if client_query in (c.get('client','') or '').lower()]
        else:
            filtres = [c for c in credits if c.get('statut') != 'soldé']
        if not filtres:
            return "Aucun crédit actif trouvé."
        total_reste = sum(c.get('reste') or 0 for c in filtres)
        lines = [f"💳 {len(filtres)} crédit(s)" + (f" pour « {client_query} »" if client_query else " actifs") + f" — Total restant: {_fmt_mad(total_reste)}"]
        for c in filtres[:8]:
            lines.append(
                f"  • {c.get('client','?')} — {_fmt_mad(c.get('montant_total',0))} | "
                f"Reste: {_fmt_mad(c.get('reste',0))} ({c.get('statut','?')})"
            )
        return "\n".join(lines)

    # ── CHÈQUES ───────────────────────────────────────────────────────────────
    if any(w in msg for w in ['chèque', 'cheque', 'chèques', 'encaissé', 'encaisse']):
        if client_query:
            filtres = [ch for ch in cheques if client_query in (ch.get('client','') or '').lower()]
        elif any(w in msg for w in ['non encaissé', 'en attente', 'attente']):
            filtres = [ch for ch in cheques if ch.get('statut') != 'encaisse']
        elif any(w in msg for w in ['encaissé', 'encaisse']):
            filtres = [ch for ch in cheques if ch.get('statut') == 'encaisse']
        else:
            filtres = cheques
        if not filtres:
            return "Aucun chèque trouvé."
        total = sum(ch.get('montant') or 0 for ch in filtres)
        lines = [f"🏦 {len(filtres)} chèque(s) — Total: {_fmt_mad(total)}"]
        for ch in filtres[:8]:
            st = '✅' if ch.get('statut') == 'encaisse' else '⏳'
            lines.append(f"  {st} {ch.get('client','?')} — {_fmt_mad(ch.get('montant',0))}")
        return "\n".join(lines)

    # ── BÉNÉFICE / CA ─────────────────────────────────────────────────────────
    if any(w in msg for w in ['bénéfice', 'benefice', 'ca ', 'chiffre', 'total ventes', 'revenu']):
        now = datetime.now()
        prefix = now.strftime("%Y-%m")
        filt = [v for v in ventes if (v.get('date_vente','') or '').startswith(prefix)]
        ca_mois = sum(v.get('pv') or 0 for v in filt)
        benef_mois = sum(v.get('benef') or 0 for v in filt)
        ca_tot = sum(v.get('pv') or 0 for v in ventes)
        benef_tot = sum(v.get('benef') or 0 for v in ventes)
        return (
            f"📈 Statistiques financières :\n"
            f"• Ce mois ({prefix}) : CA {_fmt_mad(ca_mois)} | Bénéf {_fmt_mad(benef_mois)} ({len(filt)} ventes)\n"
            f"• Tout temps : CA {_fmt_mad(ca_tot)} | Bénéf {_fmt_mad(benef_tot)} ({len(ventes)} ventes)"
        )

    # ── RÉPONSE PAR DÉFAUT ────────────────────────────────────────────────────
    return (
        "Je n'ai pas compris ta question 🤔\n"
        "Essaie par exemple :\n"
        "• « où est le 4313 »\n"
        "• « ventes de mars 2026 »\n"
        "• « crédit de Kamilia »\n"
        "• « chèques en attente »\n"
        "Tape **aide** pour voir tout ce que je sais faire."
    )


# ─── Serveur HTTP ─────────────────────────────────────────────────────────────

class Handler(http.server.SimpleHTTPRequestHandler):

    def log_message(self, format, *args):
        pass

    def send_logs_page(self):
        rows = ""
        for e in reversed(ACCESS_LOGS):
            is_local = e["ip"] in ("127.0.0.1", "::1")
            who      = "Toi (Mac)" if is_local else e["ip"]
            role     = "👤 Employé" if e.get("role") == "employe" else "🔑 Admin"
            page     = PAGE_LABELS.get(e["page"], e["page"])
            # Ligne en jaune clair si connexion externe
            if not is_local:
                row_style = ' style="background:#fffbea;font-weight:500;"'
            else:
                row_style = ''
            rows += (
                f'<tr{row_style}>'
                f'<td>{e["ts"]}</td>'
                f'<td>{e["device"]}</td>'
                f'<td>{e["browser"]}</td>'
                f'<td>{who}</td>'
                f'<td>{role}</td>'
                f'<td>{page}</td>'
                f'</tr>\n'
            )

        nb       = len(ACCESS_LOGS)
        external = sum(1 for e in ACCESS_LOGS if e["ip"] not in ("127.0.0.1", "::1"))
        local    = nb - external

        html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>Logs — TRABELSI</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:system-ui,sans-serif;background:#f8f5f0;padding:30px;color:#1a1612;}}
h1{{font-size:20px;margin-bottom:4px;color:#7a5c20;}}
.note{{font-size:11px;color:#b0a090;margin-bottom:20px;}}
.stats{{display:flex;gap:12px;margin-bottom:24px;flex-wrap:wrap;}}
.stat{{background:#fff;border-radius:8px;padding:12px 18px;box-shadow:0 1px 5px rgba(0,0,0,.08);min-width:110px;}}
.stat-val{{font-size:28px;font-weight:700;color:#7a5c20;}}
.stat.ext .stat-val{{color:#e67e22;}}
.stat-lbl{{font-size:11px;color:#9a8e7e;margin-top:2px;}}
.toolbar{{display:flex;gap:10px;margin-bottom:14px;align-items:center;}}
button{{padding:7px 14px;border-radius:6px;border:1px solid #d4c4a0;background:#fff;cursor:pointer;font-size:12px;}}
button:hover{{background:#fdf8f0;}}
button.active{{background:#7a5c20;color:#fff;border-color:#7a5c20;}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 2px 10px rgba(0,0,0,.07);font-size:13px;}}
th{{background:#7a5c20;color:#fff;padding:10px 13px;text-align:left;font-weight:600;font-size:12px;}}
td{{padding:9px 13px;border-bottom:1px solid #f0e8d8;}}
tr:last-child td{{border-bottom:none;}}
tr:hover td{{background:#fdf8f0;}}
.empty{{text-align:center;padding:40px;color:#9a8e7e;font-style:italic;}}
.badge-emp{{display:inline-block;background:#e8f4fd;color:#2471a3;border-radius:4px;padding:2px 7px;font-size:11px;}}
.badge-adm{{display:inline-block;background:#fef9e7;color:#7a5c20;border-radius:4px;padding:2px 7px;font-size:11px;}}
.badge-ext{{display:inline-block;background:#fdebd0;color:#a04000;border-radius:4px;padding:2px 7px;font-size:11px;font-weight:600;}}
</style></head><body>
<h1>🔍 Journal des connexions — TRABELSI</h1>
<p class="note">Accessible uniquement depuis ce Mac · Visites dédupliquées (une entrée / 5 min par page)</p>

<div class="stats">
  <div class="stat"><div class="stat-val">{nb}</div><div class="stat-lbl">Visites totales</div></div>
  <div class="stat ext"><div class="stat-val">{external}</div><div class="stat-lbl">Connexions externes</div></div>
  <div class="stat"><div class="stat-val">{local}</div><div class="stat-lbl">Depuis ce Mac</div></div>
</div>

<div class="toolbar">
  <button class="active" onclick="filter('all',this)">Tout voir</button>
  <button onclick="filter('ext',this)">🌐 Externes seulement</button>
  <button onclick="filter('emp',this)">👤 Employés</button>
  <button style="margin-left:auto;" onclick="location.reload()">🔄 Actualiser</button>
</div>

<table id="tbl">
<thead><tr>
  <th>Heure</th><th>Appareil</th><th>Navigateur</th><th>IP</th><th>Rôle</th><th>Page visitée</th>
</tr></thead>
<tbody id="tbody">{rows if rows else '<tr><td colspan="6" class="empty">Aucune visite depuis le démarrage du serveur.</td></tr>'}</tbody>
</table>

<script>
const rows = document.querySelectorAll('#tbody tr[style], #tbody tr:not([style])');
function filter(type, btn) {{
  document.querySelectorAll('.toolbar button').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  rows.forEach(r => {{
    const ip = r.cells[3]?.textContent || '';
    const role = r.cells[4]?.textContent || '';
    if(type === 'all') r.style.display='';
    else if(type === 'ext') r.style.display = (!ip.startsWith('127.') && ip !== 'Toi (Mac)') ? '' : 'none';
    else if(type === 'emp') r.style.display = role.includes('Employé') ? '' : 'none';
  }});
}}
</script>
</body></html>"""
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, path):
        try:
            content = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(content))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()

    def send_static(self, path):
        """Sert un fichier statique (png, json, js, ico)."""
        MIME = {
            ".png": "image/png", ".ico": "image/x-icon",
            ".json": "application/json", ".js": "application/javascript",
            ".css": "text/css", ".svg": "image/svg+xml",
        }
        try:
            content = path.read_bytes()
            mime = MIME.get(path.suffix, "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", len(content))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        # ── Enregistrer l'accès ───────────────────────────────────────────────
        ip = self.client_address[0]
        ua = self.headers.get('User-Agent', '')
        record_log(ip, PORT, path, ua)

        # ── Page logs (localhost uniquement) ──────────────────────────────────
        if path == "/logs":
            if ip not in ("127.0.0.1", "::1"):
                self.send_response(403); self.end_headers()
                self.wfile.write(b"Acces refuse"); return
            self.send_logs_page(); return

        # ── Fichiers statiques — pas de protection ────────────────────────────
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            self.send_static(STATIC_DIR / rel); return

        # ── JS communs (chatbot, etc.) ────────────────────────────────────────
        if path.endswith(".js") and not path.startswith("/api"):
            self.send_static(STATIC_DIR / path.lstrip("/")); return

        # ── Login ─────────────────────────────────────────────────────────────
        if path == "/login":
            self.send_html(STATIC_DIR / "login.html"); return

        # ── Logout ────────────────────────────────────────────────────────────
        if path == "/logout":
            token = get_session_token(self.headers)
            if token: SESSIONS.pop(token, None)
            self.send_response(302)
            self.send_header("Set-Cookie", "session=; Max-Age=0; Path=/")
            self.send_header("Location", "/login")
            self.end_headers(); return

        # ── Protection : toutes les autres routes nécessitent d'être connecté ─
        if not is_authenticated(self.headers):
            redirect_login(self); return

        role = get_role(self.headers)

        # ── Employé : accès limité à /fiche uniquement ────────────────────────
        if role == "employe":
            if path in ("/", "/fiche"):
                self.send_html(STATIC_DIR / "fiche_employe.html"); return
            if path == "/api/articles":
                self.send_json(load_articles()); return
            if path.startswith("/api/articles/"):
                try:
                    ref = int(path.split("/")[-1])
                    arts = load_articles()
                    found = [a for a in arts if a["id"] == ref]
                    if found: self.send_json(found[0])
                    else: self.send_json({"error": "Article introuvable"}, 404)
                except:
                    self.send_json({"error": "Référence invalide"}, 400)
                return
            if path.startswith("/api/photo/"):
                try:
                    ref = int(path.split("/")[-1])
                    url = find_photo_url(ref)
                    if url:
                        self.send_response(302)
                        self.send_header("Location", url)
                        self.end_headers(); return
                    photo = find_photo_local(ref)
                    if photo is None:
                        self.send_response(404); self.end_headers(); return
                    content = photo.read_bytes()
                    ctype = "image/jpeg" if str(photo).endswith(".jpg") else "image/png"
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", len(content))
                    self.end_headers()
                    self.wfile.write(content)
                except:
                    self.send_response(500); self.end_headers()
                return
            # Toute autre route → accès refusé pour l'employé
            self.send_response(302)
            self.send_header("Location", "/fiche")
            self.end_headers(); return

        # ── Pages HTML Admin ──────────────────────────────────────────────────
        if path in ("/", "/index.html", "/accueil"):
            self.send_html(STATIC_DIR / "accueil.html"); return
        if path == "/stock":
            self.send_html(STATIC_DIR / "stock.html"); return
        if path == "/ajouter":
            self.send_html(STATIC_DIR / "ajouter.html"); return
        if path == "/vente":
            self.send_html(STATIC_DIR / "vente.html"); return
        if path == "/vendu":
            self.send_html(STATIC_DIR / "vendu.html"); return
        if path == "/dashboard":
            self.send_html(STATIC_DIR / "dashboard.html"); return
        if path == "/fiche":
            self.send_html(STATIC_DIR / "fiche.html"); return
        if path == "/credit":
            self.send_html(STATIC_DIR / "credit.html"); return
        if path == "/fournisseurs":
            self.send_html(STATIC_DIR / "fournisseurs.html"); return
        if path == "/mamoun":
            self.send_html(STATIC_DIR / "mamoun.html"); return
        if path == "/facture":
            self.send_html(STATIC_DIR / "facture.html"); return
        if path == "/cheques":
            self.send_html(STATIC_DIR / "cheques.html"); return
        if path == "/historique-factures":
            self.send_html(STATIC_DIR / "historique_factures.html"); return
        if path == "/catalogue":
            self.send_html(STATIC_DIR / "catalogue.html"); return

        # ── API articles ──────────────────────────────────────────────────────
        if path == "/api/articles":
            self.send_json(load_articles()); return

        if path.startswith("/api/articles/"):
            try:
                ref = int(path.split("/")[-1])
                articles = load_articles()
                found = [a for a in articles if a["id"] == ref]
                if found: self.send_json(found[0])
                else: self.send_json({"error": "Article introuvable"}, 404)
            except:
                self.send_json({"error": "Référence invalide"}, 400)
            return

        # Ancienne route (compatibilité)
        if path.startswith("/api/article/"):
            try:
                ref = int(path.split("/")[-1])
                articles = load_articles()
                found = [a for a in articles if a["id"] == ref]
                if found:
                    self.send_json(found[0]); return
                # Chercher aussi dans les ventes
                ventes = load_ventes()
                found_v = [v for v in ventes if v["ref"] == ref]
                if found_v:
                    self.send_json({"vendu": True, **found_v[-1]}); return
                self.send_json({"error": "Article introuvable"}, 404)
            except:
                self.send_json({"error": "Référence invalide"}, 400)
            return

        # ── API stats ─────────────────────────────────────────────────────────
        if path == "/api/stats":
            articles = load_articles()
            ventes = load_ventes()
            stats = calc_stats(articles)
            today = datetime.now().strftime("%Y-%m-%d")
            ventes_today = [v for v in ventes if (v.get("date_vente") or "").startswith(today)]
            stats["nb_ventes_total"] = len(ventes)
            stats["nb_ventes_today"] = len(ventes_today)
            stats["ca_today"] = round(sum(v.get("pv") or 0 for v in ventes_today), 0)
            stats["benef_today"] = round(sum(v.get("benef") or 0 for v in ventes_today), 0)
            stats["ca_total"] = round(sum(v.get("pv") or 0 for v in ventes), 0)
            stats["benef_total"] = round(sum(v.get("benef") or 0 for v in ventes), 0)
            self.send_json(stats); return

        if path == "/api/stats/advanced":
            ventes = load_ventes()
            date_from = params.get("from", [None])[0]
            date_to = params.get("to", [None])[0]
            result = {
                "period": ventes_stats(ventes, date_from, date_to),
                "monthly": monthly_stats(ventes),
                "annual": annual_stats(ventes),
            }
            self.send_json(result); return

        # ── API ventes ────────────────────────────────────────────────────────
        if path == "/api/ventes":
            self.send_json(load_ventes()); return

        if path.startswith("/api/ventes/"):
            try:
                id_vente = int(path.split("/")[-1])
                ventes = load_ventes()
                found = [v for v in ventes if v["id_vente"] == id_vente]
                if found: self.send_json(found[0])
                else: self.send_json({"error": "Vente introuvable"}, 404)
            except:
                self.send_json({"error": "ID invalide"}, 400)
            return

        # ── API photo ─────────────────────────────────────────────────────────
        if path.startswith("/api/photo/"):
            try:
                ref = int(path.split("/")[-1])
                # Cloud : redirection vers R2
                url = find_photo_url(ref)
                if url:
                    self.send_response(302)
                    self.send_header("Location", url)
                    self.end_headers()
                    return
                # Local : lecture fichier
                photo = find_photo_local(ref)
                if photo is None:
                    self.send_response(404); self.end_headers(); return
                content = photo.read_bytes()
                ctype = "image/jpeg" if str(photo).endswith(".jpg") else "image/png"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", len(content))
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()
                self.wfile.write(content)
            except Exception:
                self.send_response(500); self.end_headers()
            return

        # ── API crédits clients ───────────────────────────────────────────────
        if path == "/api/credits":
            self.send_json(load_credits()); return

        if path == "/api/credits/stats":
            credits = load_credits()
            ouverts = [c for c in credits if c["statut"] in ("rien", "avance")]
            self.send_json({
                "total_du": round(sum(c.get("reste", 0) for c in ouverts), 0),
                "nb_ouverts": len(ouverts),
                "total_avances": round(sum(sum(p.get("montant",0) for p in c.get("paiements",[])) for c in credits if c["statut"] == "avance"), 0),
                "nb_soldes": len([c for c in credits if c["statut"] == "solde"]),
            }); return

        if path.startswith("/api/credits/"):
            parts = path.split("/")
            try:
                id_credit = int(parts[3])
                credits = load_credits()
                found = [c for c in credits if c["id"] == id_credit]
                if found: self.send_json(found[0])
                else: self.send_json({"error": "Crédit introuvable"}, 404)
            except:
                self.send_json({"error": "ID invalide"}, 400)
            return

        # ── API fournisseurs ──────────────────────────────────────────────────
        if path == "/api/fournisseurs":
            self.send_json(load_fournisseurs()); return

        if path == "/api/fournisseurs/stats":
            fournisseurs = load_fournisseurs()
            ouverts = [f for f in fournisseurs if f["statut"] in ("rien", "avance")]
            self.send_json({
                "total_du": round(sum(f.get("reste", 0) for f in ouverts), 0),
                "nb_ouverts": len(ouverts),
                "nb_soldes": len([f for f in fournisseurs if f["statut"] == "solde"]),
            }); return

        if path.startswith("/api/fournisseurs/"):
            parts = path.split("/")
            try:
                id_f = int(parts[3])
                fournisseurs = load_fournisseurs()
                found = [f for f in fournisseurs if f["id"] == id_f]
                if found: self.send_json(found[0])
                else: self.send_json({"error": "Fournisseur introuvable"}, 404)
            except:
                self.send_json({"error": "ID invalide"}, 400)
            return

        # ── API chèques ───────────────────────────────────────────────────────
        if path == "/api/cheques":
            self.send_json(load_cheques()); return

        # ── API factures ──────────────────────────────────────────────────────
        if path == "/api/factures":
            self.send_json(load_factures()); return

        # ── API notifs Ismail ─────────────────────────────────────────────────
        if path == "/api/notifs":
            notifs = [n for n in load_notifs() if not n.get("dismissed")]
            self.send_json(notifs); return

        # ── API config (prix de l'or) ─────────────────────────────────────────
        if path == "/api/config":
            self.send_json(load_config()); return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        # ── Login ─────────────────────────────────────────────────────────────
        if path == "/api/login":
            try:
                data = json.loads(body)
            except:
                self.send_json({"error": "Invalide"}, 400); return
            pwd = data.get("password", "")
            if pwd == MOT_DE_PASSE_ADMIN:
                role = "admin"
            elif pwd == MOT_DE_PASSE_EMPLOYE:
                role = "employe"
            else:
                self.send_json({"error": "Mot de passe incorrect"}, 401); return
            token = secrets.token_hex(32)
            SESSIONS[token] = role
            # Employé → redirige vers /fiche après login
            redirect = "/fiche" if role == "employe" else "/accueil"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", f"session={token}; Path=/; HttpOnly; SameSite=Strict")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "redirect": redirect, "role": role}).encode())
            return

        # ── Protection POST (admin uniquement pour les routes sensibles) ──────
        if not is_authenticated(self.headers):
            self.send_json({"error": "Non authentifié"}, 401); return
        if not is_admin(self.headers):
            self.send_json({"error": "Accès réservé à l'administrateur"}, 403); return

        try:
            data = json.loads(body) if body else {}
        except:
            self.send_json({"error": "JSON invalide"}, 400); return

        # ── Ajouter un article ────────────────────────────────────────────────
        if path == "/api/articles":
            articles = load_articles()
            ref = data.get("id")
            if not ref:
                self.send_json({"error": "Référence manquante"}, 400); return
            if any(a["id"] == int(ref) for a in articles):
                self.send_json({"error": f"La référence {ref} existe déjà"}, 409); return
            article = build_article(data)
            articles.append(article)
            save_articles(articles)
            self.send_json({"success": True, "article": article}); return

        # ── Enregistrer une vente ─────────────────────────────────────────────
        if path == "/api/ventes":
            ref = data.get("ref")
            if not ref:
                self.send_json({"error": "Référence manquante"}, 400); return
            articles = load_articles()
            idx = next((i for i, a in enumerate(articles) if a["id"] == int(ref)), None)
            if idx is None:
                self.send_json({"error": "Article introuvable"}, 404); return

            article = articles[idx]
            now = datetime.now()
            cfg = load_config()

            if is_poids_article(article):
                # ── Vente au poids ──────────────────────────────────────────
                poids_vendu = float(data.get("poids_vendu") or 0)
                if poids_vendu <= 0:
                    self.send_json({"error": "Poids à vendre requis (> 0)"}, 400); return
                stock_actuel = float(article.get("or_grs") or 0)
                if poids_vendu > stock_actuel + 0.001:
                    self.send_json({"error": f"Stock insuffisant : {stock_actuel} grs disponibles"}, 400); return
                prix_or_achat = cfg.get("prix_or_achat", 1000)
                pa = round(poids_vendu * prix_or_achat, 2)
                # PV saisi manuellement, sinon fallback sur prix_or_vente
                pv_manual = data.get("pv")
                if pv_manual not in (None, "", 0):
                    pv = round(float(pv_manual), 2)
                else:
                    pv = round(poids_vendu * cfg.get("prix_or_vente", 1100), 2)
                benef = round(pv - pa, 2)
                vente = {
                    "id_vente": int(now.timestamp() * 1000),
                    "date_achat": article.get("date"),
                    "date_vente": now.strftime("%Y-%m-%d"),
                    "ref": article["id"],
                    "article": article.get("article", ""),
                    "or_grs": poids_vendu,
                    "vente_au_poids": True,
                    "prix_or_achat": prix_or_achat,
                    "pa": pa,
                    "d": None, "em": None, "r": None, "s": None,
                    "p_fines": None, "rosaces": None, "em_clb": None, "perles": None,
                    "pv": pv,
                    "benef": benef,
                    "client": str(data.get("client", "")).strip(),
                    "mode_paiement": str(data.get("mode_paiement", "")).strip(),
                    "commentaire": str(data.get("note", "")).strip(),
                }
                # Soustraire le poids vendu du stock
                nouveau_poids = round(stock_actuel - poids_vendu, 3)
                if nouveau_poids <= 0.001:
                    articles.pop(idx)  # Article épuisé → retirer du stock
                else:
                    articles[idx]["or_grs"] = nouveau_poids
                    # Recalculer le PA au nouveau poids
                    articles[idx]["pa"] = round(nouveau_poids * prix_or_achat, 2)
                save_articles(articles)
                ventes = load_ventes()
                ventes.append(vente)
                save_ventes(ventes)
                if article.get("ismail_pierres"):
                    add_notif_ismail(article, vente["client"], article["id"])
                self.send_json({"success": True, "vente": vente, "poids_vendu": poids_vendu,
                                "stock_restant": nouveau_poids if nouveau_poids > 0.001 else 0}); return
            else:
                # ── Vente normale (article entier retiré du stock) ──────────
                try:
                    pv = parse_positive(data.get("pv"), "Prix de vente")
                except ValueError as e:
                    self.send_json({"error": str(e)}, 400); return
                pa = article.get("pa") or 0
                benef = pv - pa
                vente = {
                    "id_vente": int(now.timestamp() * 1000),
                    "date_achat": article.get("date"),
                    "date_vente": now.strftime("%Y-%m-%d"),
                    "ref": article["id"],
                    "article": article["article"],
                    "or_grs": article.get("or_grs"),
                    "pa": pa,
                    "d": article.get("d"),
                    "em": article.get("em"),
                    "r": article.get("r"),
                    "s": article.get("s"),
                    "p_fines": article.get("p_fines"),
                    "rosaces": article.get("rosaces"),
                    "em_clb": article.get("em_clb"),
                    "perles": article.get("perles"),
                    "pv": pv,
                    "benef": benef,
                    "client": str(data.get("client", "")).strip(),
                    "mode_paiement": str(data.get("mode_paiement", "")).strip(),
                    "commentaire": str(data.get("note", "")).strip(),
                }
                articles.pop(idx)
                save_articles(articles)
                ventes = load_ventes()
                ventes.append(vente)
                save_ventes(ventes)
                if article.get("ismail_pierres"):
                    add_notif_ismail(article, vente["client"], article["id"])
                self.send_json({"success": True, "vente": vente}); return

        # ── Créer un crédit client ────────────────────────────────────────────
        if path == "/api/credits":
            credits = load_credits()
            now = datetime.now()
            try:
                montant_total_val = parse_positive(data.get("montant_total"), "Montant total")
            except ValueError as e:
                self.send_json({"error": str(e)}, 400); return
            avance_raw = data.get("avance") or 0
            avance = float(avance_raw) if avance_raw not in (None, "") else 0
            if avance < 0:
                self.send_json({"error": "L'avance ne peut pas être négative"}, 400); return
            if avance > montant_total_val:
                self.send_json({"error": "L'avance ne peut pas dépasser le montant total"}, 400); return
            paiements = []
            if avance > 0:
                paiements.append({
                    "montant": avance,
                    "date": data.get("date_avance") or now.strftime("%Y-%m-%d"),
                    "mode": str(data.get("mode_paiement", "")).strip(),
                })
            new_id = max((c["id"] for c in credits), default=0) + 1
            credit = {
                "id": new_id,
                "client": str(data.get("client", "")).strip(),
                "contact": str(data.get("contact", "")).strip() or None,
                "date_achat": data.get("date_achat") or now.strftime("%Y-%m-%d"),
                "refs": str(data.get("refs", "")).strip() or None,
                "article": str(data.get("article", "")).strip() or None,
                "montant_total": montant_total_val,
                "paiements": paiements,
                "reste": 0,
                "statut": "rien",
                "date_solde": None,
                "note": str(data.get("note", "")).strip(),
            }
            recalc_credit(credit)
            credits.append(credit)
            save_credits(credits)
            self.send_json({"success": True, "credit": credit}); return

        # ── Ajouter un paiement sur un crédit client ──────────────────────────
        if path.startswith("/api/credits/") and path.endswith("/paiement"):
            parts = path.split("/")
            try:
                id_credit = int(parts[3])
                credits = load_credits()
                idx = next((i for i, c in enumerate(credits) if c["id"] == id_credit), None)
                if idx is None:
                    self.send_json({"error": "Crédit introuvable"}, 404); return
                montant = float(data.get("montant") or 0)
                if montant <= 0:
                    self.send_json({"error": "Montant invalide"}, 400); return
                now = datetime.now()
                credits[idx]["paiements"].append({
                    "montant": montant,
                    "date": data.get("date") or now.strftime("%Y-%m-%d"),
                    "mode": str(data.get("mode", "")).strip(),
                })
                recalc_credit(credits[idx])
                if credits[idx]["statut"] == "solde" and not credits[idx].get("date_solde"):
                    credits[idx]["date_solde"] = now.strftime("%Y-%m-%d")
                save_credits(credits)
                self.send_json({"success": True, "credit": credits[idx]}); return
            except Exception as e:
                self.send_json({"error": str(e)}, 400); return

        # ── Créer un paiement fournisseur ─────────────────────────────────────
        if path == "/api/fournisseurs":
            fournisseurs = load_fournisseurs()
            now = datetime.now()
            try:
                montant_total_fourn = parse_positive(data.get("montant_total"), "Montant total")
            except ValueError as e:
                self.send_json({"error": str(e)}, 400); return
            avance_raw = data.get("avance") or 0
            avance = float(avance_raw) if avance_raw not in (None, "") else 0
            if avance < 0:
                self.send_json({"error": "L'avance ne peut pas être négative"}, 400); return
            if avance > montant_total_fourn:
                self.send_json({"error": "L'avance ne peut pas dépasser le montant total"}, 400); return
            paiements = []
            if avance > 0:
                paiements.append({
                    "montant": avance,
                    "date": data.get("date_avance") or now.strftime("%Y-%m-%d"),
                    "mode": str(data.get("mode_paiement", "")).strip(),
                })
            new_id = max((f["id"] for f in fournisseurs), default=0) + 1
            fourn = {
                "id": new_id,
                "fournisseur": str(data.get("fournisseur", "")).strip(),
                "contact": str(data.get("contact", "")).strip() or None,
                "date_commande": data.get("date_commande") or now.strftime("%Y-%m-%d"),
                "num_commande": str(data.get("num_commande", "")).strip() or None,
                "article": str(data.get("article", "")).strip() or None,
                "montant_total": montant_total_fourn,
                "paiements": paiements,
                "reste": 0,
                "statut": "rien",
                "date_solde": None,
                "note": str(data.get("note", "")).strip(),
            }
            recalc_credit(fourn)
            fournisseurs.append(fourn)
            save_fournisseurs(fournisseurs)
            self.send_json({"success": True, "fournisseur": fourn}); return

        # ── Ajouter un paiement fournisseur ───────────────────────────────────
        if path.startswith("/api/fournisseurs/") and path.endswith("/paiement"):
            parts = path.split("/")
            try:
                id_f = int(parts[3])
                fournisseurs = load_fournisseurs()
                idx = next((i for i, f in enumerate(fournisseurs) if f["id"] == id_f), None)
                if idx is None:
                    self.send_json({"error": "Fournisseur introuvable"}, 404); return
                montant = float(data.get("montant") or 0)
                if montant <= 0:
                    self.send_json({"error": "Montant invalide"}, 400); return
                now = datetime.now()
                fournisseurs[idx]["paiements"].append({
                    "montant": montant,
                    "date": data.get("date") or now.strftime("%Y-%m-%d"),
                    "mode": str(data.get("mode", "")).strip(),
                })
                recalc_credit(fournisseurs[idx])
                if fournisseurs[idx]["statut"] == "solde" and not fournisseurs[idx].get("date_solde"):
                    fournisseurs[idx]["date_solde"] = now.strftime("%Y-%m-%d")
                save_fournisseurs(fournisseurs)
                self.send_json({"success": True, "fournisseur": fournisseurs[idx]}); return
            except Exception as e:
                self.send_json({"error": str(e)}, 400); return

        # ── Enregistrer un chèque ─────────────────────────────────────────────
        if path == "/api/cheques":
            cheques = load_cheques()
            now = datetime.now()
            try:
                montant_cheque = parse_positive(data.get("montant"), "Montant du chèque")
            except ValueError as e:
                self.send_json({"error": str(e)}, 400); return
            new_id = max((c["id"] for c in cheques), default=0) + 1
            cheque = {
                "id": new_id,
                "client": str(data.get("client", "")).strip(),
                "ref_article": str(data.get("ref_article", "")).strip() or None,
                "montant": montant_cheque,
                "numero": str(data.get("numero", "")).strip(),
                "nb_cheques": int(data.get("nb_cheques") or 1),
                "banque": str(data.get("banque", "")).strip(),
                "date_cheque": data.get("date_cheque") or now.strftime("%Y-%m-%d"),
                "date_encaissement": data.get("date_encaissement") or None,
                "dates_encaissement": data.get("dates_encaissement") or [],
                "numeros_cheques": data.get("numeros_cheques") or [],
                "statuts_cheques": data.get("statuts_cheques") or [],
                "statut": data.get("statut") or "en_attente",
                "credit_id": int(data["credit_id"]) if data.get("credit_id") not in (None, "", 0, "0") else None,
                "note": str(data.get("note", "")).strip(),
                "created_at": now.strftime("%Y-%m-%d"),
            }
            cheques.append(cheque)
            save_cheques(cheques)
            self.send_json({"success": True, "cheque": cheque}); return

        # ── Enregistrer une facture ───────────────────────────────────────────
        if path == "/api/factures":
            factures = load_factures()
            now = datetime.now()
            annee = now.strftime("%Y")
            # Numéro auto FAC-YYYY-XXXX
            num_existants = [f.get("numero", "") for f in factures if f.get("numero", "").startswith(f"FAC-{annee}-")]
            max_seq = 0
            for n in num_existants:
                try: max_seq = max(max_seq, int(n.split("-")[-1]))
                except: pass
            numero = data.get("numero") or f"FAC-{annee}-{str(max_seq+1).zfill(4)}"
            new_id = int(now.timestamp() * 1000)
            facture = {
                "id": new_id,
                "numero": numero,
                "client": str(data.get("client", "")).strip(),
                "telephone": str(data.get("telephone", "")).strip() or None,
                "email": str(data.get("email", "")).strip() or None,
                "ville": str(data.get("ville", "")).strip() or None,
                "articles": data.get("articles", []),
                "total": float(data.get("total") or 0),
                "avance": float(data.get("avance") or 0),
                "mode_paiement": str(data.get("mode_paiement", "")).strip() or None,
                "note": str(data.get("note", "")).strip(),
                "date": now.strftime("%Y-%m-%d"),
                "created_at": now.isoformat(),
            }
            factures.append(facture)
            save_factures(factures)
            self.send_json({"success": True, "facture": facture}); return

        # ── Upload photo article ────────────────────────────────────────
        if path == "/api/photo/upload":
            ref = data.get("ref")
            photo_b64 = data.get("photo_base64")
            if not ref or not photo_b64:
                self.send_json({"error": "Données manquantes"}, 400); return
            try:
                photo_bytes = base64.b64decode(photo_b64)
                filename = f"{int(ref)}.jpg"
                if R2_ACCESS_KEY and R2_SECRET_KEY:
                    # Upload vers Cloudflare R2
                    _upload_to_r2(filename, photo_bytes)
                else:
                    # Fallback local : dossier photos_compressed
                    PHOTOS_DIR_COMPRESSED.mkdir(parents=True, exist_ok=True)
                    (PHOTOS_DIR_COMPRESSED / filename).write_bytes(photo_bytes)
                self.send_json({"success": True}); return
            except Exception as e:
                self.send_json({"error": str(e)}, 400); return

        # ── Chatbot ───────────────────────────────────────────────────────────
        if path == "/api/chat":
            msg = data.get("message", "").strip()
            if not msg:
                self.send_json({"reply": "Je n'ai pas compris ta question."}); return
            reply = handle_chat(msg)
            self.send_json({"reply": reply}); return

        # ── Import base de données complète (migration one-shot) ──────────────
        if path == "/api/seed":
            try:
                db.seed_all(data)
                self.send_json({"success": True, "message": "Base de données importée avec succès"}); return
            except Exception as e:
                self.send_json({"error": str(e)}, 500); return

        self.send_json({"error": "Route inconnue"}, 404)

    def do_PUT(self):
        path = urllib.parse.urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body) if body else {}
        except:
            self.send_json({"error": "JSON invalide"}, 400); return

        # ── Modifier la configuration (prix de l'or) ──────────────────────────
        if path == "/api/config":
            cfg = load_config()
            if "prix_or_achat" in data:
                v = float(data["prix_or_achat"])
                if v <= 0: self.send_json({"error": "Prix invalide"}, 400); return
                cfg["prix_or_achat"] = v
            if "prix_or_vente" in data:
                v = float(data["prix_or_vente"])
                if v <= 0: self.send_json({"error": "Prix invalide"}, 400); return
                cfg["prix_or_vente"] = v
            save_config(cfg)
            # Recalculer le PA de tous les articles au poids
            articles = load_articles()
            for a in articles:
                if is_poids_article(a) and a.get("or_grs"):
                    a["pa"] = round(float(a["or_grs"]) * cfg["prix_or_achat"], 2)
            save_articles(articles)
            self.send_json({"success": True, "config": cfg}); return

        # ── Modifier un article ───────────────────────────────────────────────
        if path.startswith("/api/articles/"):
            try:
                ref = int(path.split("/")[-1])
                articles = load_articles()
                idx = next((i for i, a in enumerate(articles) if a["id"] == ref), None)
                if idx is None:
                    self.send_json({"error": "Article introuvable"}, 404); return
                # Mettre à jour les champs (garder l'id)
                updated = build_article(data, ref_override=ref)
                articles[idx] = updated
                save_articles(articles)
                self.send_json({"success": True, "article": updated}); return
            except Exception as e:
                self.send_json({"error": str(e)}, 400); return

        # ── Modifier une vente ────────────────────────────────────────────────
        if path.startswith("/api/ventes/"):
            try:
                id_vente = int(path.split("/")[-1])
                ventes = load_ventes()
                idx = next((i for i, v in enumerate(ventes) if v["id_vente"] == id_vente), None)
                if idx is None:
                    self.send_json({"error": "Vente introuvable"}, 404); return
                # Mettre à jour seulement les champs éditables
                v = ventes[idx]
                if "pv" in data and data["pv"] not in (None, ""):
                    v["pv"] = float(data["pv"])
                    v["benef"] = round(v["pv"] - (v.get("pa") or 0), 2)
                if "client" in data:
                    v["client"] = str(data["client"]).strip()
                if "commentaire" in data:
                    v["commentaire"] = str(data["commentaire"]).strip()
                if "date_vente" in data and data["date_vente"]:
                    v["date_vente"] = data["date_vente"]
                ventes[idx] = v
                save_ventes(ventes)
                self.send_json({"success": True, "vente": v}); return
            except Exception as e:
                self.send_json({"error": str(e)}, 400); return

        # ── Modifier un crédit client ─────────────────────────────────────────
        if path.startswith("/api/credits/"):
            try:
                id_credit = int(path.split("/")[-1])
                credits = load_credits()
                idx = next((i for i, c in enumerate(credits) if c["id"] == id_credit), None)
                if idx is None:
                    self.send_json({"error": "Crédit introuvable"}, 404); return
                c = credits[idx]
                for field in ["client", "contact", "refs", "article", "note"]:
                    if field in data: c[field] = str(data[field]).strip() or None
                for field in ["date_achat", "date_solde"]:
                    if field in data: c[field] = data[field] or None
                if "montant_total" in data and data["montant_total"] not in (None, ""):
                    c["montant_total"] = float(data["montant_total"])
                recalc_credit(c)
                credits[idx] = c
                save_credits(credits)
                self.send_json({"success": True, "credit": c}); return
            except Exception as e:
                self.send_json({"error": str(e)}, 400); return

        # ── Modifier un fournisseur ───────────────────────────────────────────
        if path.startswith("/api/fournisseurs/"):
            try:
                id_f = int(path.split("/")[-1])
                fournisseurs = load_fournisseurs()
                idx = next((i for i, f in enumerate(fournisseurs) if f["id"] == id_f), None)
                if idx is None:
                    self.send_json({"error": "Fournisseur introuvable"}, 404); return
                f = fournisseurs[idx]
                for field in ["fournisseur", "contact", "num_commande", "article", "note"]:
                    if field in data: f[field] = str(data[field]).strip() or None
                for field in ["date_commande", "date_solde"]:
                    if field in data: f[field] = data[field] or None
                if "montant_total" in data and data["montant_total"] not in (None, ""):
                    f["montant_total"] = float(data["montant_total"])
                recalc_credit(f)
                fournisseurs[idx] = f
                save_fournisseurs(fournisseurs)
                self.send_json({"success": True, "fournisseur": f}); return
            except Exception as e:
                self.send_json({"error": str(e)}, 400); return

        # ── Modifier un chèque ────────────────────────────────────────────────
        if path.startswith("/api/cheques/"):
            try:
                id_cheque = int(path.split("/")[-1])
                cheques = load_cheques()
                idx = next((i for i, c in enumerate(cheques) if c["id"] == id_cheque), None)
                if idx is None:
                    self.send_json({"error": "Chèque introuvable"}, 404); return
                c = cheques[idx]
                for field in ["numero", "banque", "client", "note", "ref_article"]:
                    if field in data: c[field] = str(data[field]).strip() or None
                for field in ["date_cheque", "date_encaissement"]:
                    if field in data: c[field] = data[field] or None
                if "montant" in data and data["montant"] not in (None, ""):
                    c["montant"] = float(data["montant"])
                if "nb_cheques" in data and data["nb_cheques"] not in (None, ""):
                    c["nb_cheques"] = int(data["nb_cheques"])
                if "dates_encaissement" in data:
                    c["dates_encaissement"] = data["dates_encaissement"] or []
                if "numeros_cheques" in data:
                    c["numeros_cheques"] = data["numeros_cheques"] or []
                if "statuts_cheques" in data:
                    c["statuts_cheques"] = data["statuts_cheques"] or []
                if "statut" in data: c["statut"] = data["statut"]
                if "credit_id" in data:
                    c["credit_id"] = int(data["credit_id"]) if data["credit_id"] not in (None, "", 0, "0") else None
                # ── Synchroniser le crédit lié ───────────────────────────────
                credit_id = c.get("credit_id")
                if credit_id and "statuts_cheques" in data:
                    credits_list = load_credits()
                    cidx = next((i for i, cr in enumerate(credits_list) if cr["id"] == credit_id), None)
                    if cidx is not None:
                        cr = credits_list[cidx]
                        source_prefix = f"chq_{id_cheque}_"
                        cr["paiements"] = [p for p in cr.get("paiements", []) if not str(p.get("source", "")).startswith(source_prefix)]
                        nb = c.get("nb_cheques", 1) or 1
                        montant_par = round(c.get("montant", 0) / nb, 2)
                        statuts_list = c.get("statuts_cheques", [])
                        dates_enc = c.get("dates_encaissement", [])
                        now2 = datetime.now().strftime("%Y-%m-%d")
                        for i, st in enumerate(statuts_list):
                            if st == "encaisse":
                                date_pai = dates_enc[i] if i < len(dates_enc) and dates_enc[i] else now2
                                cr["paiements"].append({
                                    "montant": montant_par,
                                    "date": date_pai,
                                    "mode": ("Chèque " + c.get("banque", "")).strip(),
                                    "source": f"chq_{id_cheque}_{i}",
                                })
                        recalc_credit(cr)
                        credits_list[cidx] = cr
                        save_credits(credits_list)
                cheques[idx] = c
                save_cheques(cheques)
                self.send_json({"success": True, "cheque": c}); return
            except Exception as e:
                self.send_json({"error": str(e)}, 400); return

        self.send_json({"error": "Route inconnue"}, 404)

    def do_DELETE(self):
        path = urllib.parse.urlparse(self.path).path

        # ── Supprimer un article du stock ─────────────────────────────────────
        if path.startswith("/api/articles/"):
            try:
                ref = int(path.split("/")[-1])
                articles = load_articles()
                idx = next((i for i, a in enumerate(articles) if a["id"] == ref), None)
                if idx is None:
                    self.send_json({"error": "Article introuvable"}, 404); return
                articles.pop(idx)
                save_articles(articles)
                self.send_json({"success": True}); return
            except:
                self.send_json({"error": "Référence invalide"}, 400); return

        # ── Annuler une vente (remet l'article en stock) ──────────────────────
        if path.startswith("/api/ventes/"):
            try:
                id_vente = int(path.split("/")[-1])
                ventes = load_ventes()
                idx = next((i for i, v in enumerate(ventes) if v["id_vente"] == id_vente), None)
                if idx is None:
                    self.send_json({"error": "Vente introuvable"}, 404); return
                vente = ventes[idx]
                # Reconstruire l'article depuis la vente
                article = {
                    "id": vente["ref"],
                    "date": vente.get("date_achat") or vente.get("date_vente"),
                    "article": vente.get("article", ""),
                    "or_grs": vente.get("or_grs"),
                    "pa": vente.get("pa"),
                    "d": vente.get("d"),
                    "em": vente.get("em"),
                    "r": vente.get("r"),
                    "s": vente.get("s"),
                    "p_fines": vente.get("p_fines"),
                    "rosaces": vente.get("rosaces"),
                    "em_clb": vente.get("em_clb"),
                    "perles": vente.get("perles"),
                }
                # Remettre l'article en stock (vérifier doublon)
                articles = load_articles()
                if not any(a["id"] == article["id"] for a in articles):
                    articles.append(article)
                    save_articles(articles)
                # Supprimer la vente
                ventes.pop(idx)
                save_ventes(ventes)
                self.send_json({"success": True, "article_restored": article}); return
            except Exception as e:
                self.send_json({"error": str(e)}, 400); return

        # ── Supprimer un crédit client ────────────────────────────────────────
        if path.startswith("/api/credits/"):
            try:
                id_credit = int(path.split("/")[-1])
                credits = load_credits()
                idx = next((i for i, c in enumerate(credits) if c["id"] == id_credit), None)
                if idx is None:
                    self.send_json({"error": "Crédit introuvable"}, 404); return
                credits.pop(idx)
                save_credits(credits)
                self.send_json({"success": True}); return
            except:
                self.send_json({"error": "ID invalide"}, 400); return

        # ── Supprimer un fournisseur ──────────────────────────────────────────
        if path.startswith("/api/fournisseurs/"):
            try:
                id_f = int(path.split("/")[-1])
                fournisseurs = load_fournisseurs()
                idx = next((i for i, f in enumerate(fournisseurs) if f["id"] == id_f), None)
                if idx is None:
                    self.send_json({"error": "Fournisseur introuvable"}, 404); return
                fournisseurs.pop(idx)
                save_fournisseurs(fournisseurs)
                self.send_json({"success": True}); return
            except:
                self.send_json({"error": "ID invalide"}, 400); return

        # ── Supprimer un chèque ───────────────────────────────────────────────
        if path.startswith("/api/cheques/"):
            try:
                id_cheque = int(path.split("/")[-1])
                cheques = load_cheques()
                idx = next((i for i, c in enumerate(cheques) if c["id"] == id_cheque), None)
                if idx is None:
                    self.send_json({"error": "Chèque introuvable"}, 404); return
                cheques.pop(idx)
                save_cheques(cheques)
                self.send_json({"success": True}); return
            except:
                self.send_json({"error": "ID invalide"}, 400); return

        # ── Supprimer une facture ─────────────────────────────────────────────
        if path.startswith("/api/factures/"):
            try:
                id_facture = int(path.split("/")[-1])
                factures = load_factures()
                idx = next((i for i, f in enumerate(factures) if f["id"] == id_facture), None)
                if idx is None:
                    self.send_json({"error": "Facture introuvable"}, 404); return
                factures.pop(idx)
                save_factures(factures)
                self.send_json({"success": True}); return
            except:
                self.send_json({"error": "ID invalide"}, 400); return

        # ── Rejeter (dismiss) une notif Ismail ────────────────────────────────
        if path.startswith("/api/notifs/"):
            try:
                notif_id = int(path.split("/")[-1])
                notifs = load_notifs()
                idx = next((i for i, n in enumerate(notifs) if n["id"] == notif_id), None)
                if idx is None:
                    self.send_json({"error": "Notif introuvable"}, 404); return
                notifs[idx]["dismissed"] = True
                save_notifs(notifs)
                self.send_json({"success": True}); return
            except:
                self.send_json({"error": "ID invalide"}, 400); return

        self.send_json({"error": "Route inconnue"}, 404)


# ─── Lancement ────────────────────────────────────────────────────────────────

def open_browser():
    import time
    time.sleep(1.2)
    webbrowser.open(f"http://localhost:{PORT}")

def backup_loop():
    """Backup automatique : au démarrage puis toutes les 24h."""
    import time
    from backup import run_backup, sync_photos_from_r2
    while True:
        try:
            stamp, files = run_backup()
            print(f"  [Backup] Sauvegarde effectuée : {stamp}")
        except Exception as e:
            print(f"  [Backup] Erreur : {e}")
        # Sync photos R2 → photos_compressed/ toutes les heures
        for _ in range(24):
            try:
                sync_photos_from_r2()
            except Exception as e:
                print(f"  [Sync photos] Erreur : {e}")
            time.sleep(3600)  # toutes les heures

if __name__ == "__main__":
    # Initialiser la base de données SQLite (migration JSON → SQLite au premier lancement)
    db.init_db()

    is_cloud = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RENDER"))

    print("=" * 50)
    print("  GESTION DE STOCK — Joaillerie")
    print("=" * 50)
    print(f"  Serveur : http://localhost:{PORT}")
    print(f"  Mode    : {'☁️  Cloud' if is_cloud else '💻 Local'}")
    if not is_cloud:
        print(f"  Appuie sur Ctrl+C pour arrêter")
    print("=" * 50)

    os.chdir(STATIC_DIR)

    if not is_cloud:
        # En local : backup auto + ouverture navigateur
        threading.Thread(target=backup_loop, daemon=True).start()
        threading.Thread(target=open_browser, daemon=True).start()

    # Un seul serveur, un seul port
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Serveur arrêté.")
