#!/usr/bin/env python3
"""
Serveur de Gestion de Stock - Joaillerie
Fonctionne sur Mac sans aucune installation supplémentaire.
"""

import base64
import csv
import http.server
import io
import json
import os
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request
import hmac
import hashlib
import webbrowser
from datetime import datetime
from pathlib import Path

# Charger .env local si présent (credentials R2, etc.)
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            _k = _k.strip()
            if _k not in os.environ:
                os.environ[_k] = _v.strip().strip('"').strip("'")

import database as db

BASE_DIR   = Path(__file__).parent
DATA_DIR   = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"

# Port : Railway injecte la variable PORT, sinon 5500 en local
PORT = int(os.environ.get("PORT", 5500))

# Version des assets (CSS/JS) — incrémenter à chaque refonte visuelle.
# Ajoute ?v=ASSET_VERSION aux liens → force le rechargement, ignore le cache.
ASSET_VERSION = "63"

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

def _pwd_match(given, expected):
    """Comparaison en temps constant (anti timing-attack)."""
    return secrets.compare_digest(str(given or ""), str(expected or ""))

# ─── Protection brute-force ───────────────────────────────────────────────────
# {ip: {"count": int, "blocked_until": float}}
_LOGIN_ATTEMPTS: dict = {}
MAX_ATTEMPTS    = 5      # tentatives avant blocage
BLOCK_SECONDS   = 900    # 15 minutes de blocage

def _get_client_ip(headers):
    return (headers.get("X-Forwarded-For") or headers.get("X-Real-IP") or "unknown").split(",")[0].strip()

def _check_brute_force(ip) -> tuple[bool, int]:
    """Retourne (bloqué, secondes_restantes)."""
    import time
    now = time.time()
    rec = _LOGIN_ATTEMPTS.get(ip)
    if rec and rec["count"] >= MAX_ATTEMPTS:
        remaining = int(rec["blocked_until"] - now)
        if remaining > 0:
            return True, remaining
        else:
            del _LOGIN_ATTEMPTS[ip]  # blocage expiré
    return False, 0

def _record_failed_login(ip):
    import time
    now = time.time()
    rec = _LOGIN_ATTEMPTS.setdefault(ip, {"count": 0, "blocked_until": 0})
    rec["count"] += 1
    rec["blocked_until"] = now + BLOCK_SECONDS
    print(f"⚠️  Tentative échouée #{rec['count']} depuis {ip}")

def _reset_login_attempts(ip):
    _LOGIN_ATTEMPTS.pop(ip, None)

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
    return db.get_session_role(token)

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
load_devis       = db.load_devis
insert_devis     = db.insert_devis
delete_devis     = db.delete_devis

def merge_duplicate_factures():
    """Fusionne les factures avec même client + même jour en une seule."""
    factures = load_factures()
    groups = {}
    order  = []
    for f in factures:
        cl = (f.get("client") or "").strip().lower()
        dt = (f.get("date")   or "")[:10]
        if not cl or not dt:
            continue
        key = (cl, dt)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(f)

    new_factures = []
    merged_count = 0
    for key in order:
        facs = groups[key]
        if len(facs) == 1:
            new_factures.append(facs[0])
            continue
        # Fusionner
        merged_count += len(facs) - 1
        base         = facs[0]
        all_articles = []
        total_sum    = 0.0
        avance_sum   = 0.0
        modes        = []
        notes        = []
        tel          = base.get("telephone") or ""
        for f in facs:
            arts = f.get("articles") or []
            all_articles.extend(arts)
            if f.get("prix_global") and f.get("total_global"):
                total_sum += float(f["total_global"])
            else:
                total_sum += float(f.get("total") or 0)
            avance_sum += float(f.get("avance") or 0)
            m = (f.get("mode_paiement") or "").strip()
            if m and m not in modes: modes.append(m)
            n = (f.get("note") or "").strip()
            if n and n not in notes: notes.append(n)
            if not tel and f.get("telephone"): tel = f["telephone"]
        merged = {
            "id":            base["id"],
            "numero":        base.get("numero") or str(base["id"]),
            "client":        base.get("client"),
            "telephone":     tel,
            "email":         base.get("email") or "",
            "ville":         base.get("ville") or "",
            "articles":      all_articles,
            "total":         total_sum,
            "avance":        avance_sum,
            "mode_paiement": ", ".join(modes) if modes else "Espèces",
            "note":          " | ".join(notes),
            "date":          base.get("date"),
            "created_at":    base.get("created_at"),
            "prix_global":   0,
            "total_global":  0.0,
        }
        new_factures.append(merged)

    if merged_count > 0:
        save_factures(new_factures)
        print(f"[MERGE-FAC] {merged_count} facture(s) dupliquée(s) fusionnée(s) (même client + même jour).")
    return merged_count


def auto_generate_missing_factures():
    """Génère automatiquement une facture pour chaque groupe (client, date) de ventes qui n'en a pas encore."""
    ventes   = load_ventes()
    factures = load_factures()

    # Construire l'ensemble des (client_low, date_jour) déjà couverts par une facture
    covered = set()
    for f in factures:
        cl = (f.get("client") or "").strip().lower()
        dt = (f.get("date") or "")[:10]
        if cl and dt:
            covered.add((cl, dt))

    # Grouper les ventes par (client, date_jour) non couvertes
    groups = {}
    for v in ventes:
        raw_client = (v.get("client") or "").strip()
        date_jour  = (v.get("date_vente") or "")[:10]
        if not raw_client or not date_jour:
            continue
        key = (raw_client.lower(), date_jour)
        if key in covered:
            continue
        if key not in groups:
            groups[key] = {"client": raw_client, "date": date_jour, "ventes": []}
        # Préférer le nom le mieux formaté
        if sum(1 for c in raw_client if c.isupper()) > sum(1 for c in groups[key]["client"] if c.isupper()):
            groups[key]["client"] = raw_client
        groups[key]["ventes"].append(v)

    if not groups:
        return 0

    existing = load_factures()
    max_id   = max((f.get("id", 0) for f in existing), default=0)
    new_facs = []

    for key, grp in groups.items():
        max_id += 1
        articles = []
        total    = 0
        for v in grp["ventes"]:
            pierres_parts = []
            for pk in ["d","em","r","s","p_fines","rosaces","em_clb","perles"]:
                val = v.get(pk)
                if val: pierres_parts.append(str(val))
            articles.append({
                "article":  v.get("article") or v.get("designation") or "—",
                "or_grs":  v.get("or_grs") or "",
                "pierres":  ", ".join(pierres_parts) if pierres_parts else "",
                "pv":       v.get("pv") or 0,
                "pa":       v.get("pa") or 0,
            })
            total += v.get("pv") or 0

        tel = next((v.get("telephone","") for v in grp["ventes"] if v.get("telephone")), "")
        fac = {
            "id":            max_id,
            "numero":        str(max_id),
            "client":        grp["client"],
            "telephone":     tel,
            "email":         "",
            "ville":         "",
            "articles":      articles,
            "total":         total,
            "avance":        0,
            "mode_paiement": "Espèces",
            "note":          "",
            "date":          grp["date"],
            "created_at":    datetime.now().isoformat(),
            "prix_global":   0,
            "total_global":  0,
        }
        new_facs.append(fac)
        covered.add(key)   # éviter doublons si même key dans groups

    if new_facs:
        save_factures(existing + new_facs)
        print(f"[AUTO-FAC] {len(new_facs)} facture(s) générée(s) automatiquement.")
    return len(new_facs)


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

# ─── Verrou d'écriture global ────────────────────────────────────────────────
# Sérialise toutes les écritures (POST/PUT/DELETE) pour éviter que deux
# enregistrements simultanés s'écrasent (perte de données). Réentrant.
_WRITE_LOCK = threading.RLock()

# ─── Synchronisation DB ↔ R2 ─────────────────────────────────────────────────
DB_R2_KEY     = "db/gestionstock.db"
_db_sync_lock = threading.Lock()

# Horodatage de la dernière sauvegarde réussie vers R2 (epoch, ou None)
LAST_BACKUP_TS = None
# Statut de la dernière tentative : "ok", "error", ou "local" (pas de R2 configuré)
LAST_BACKUP_STATUS = "local"

def _r2_has_creds():
    return bool(R2_ACCESS_KEY and R2_SECRET_KEY and R2_ACCOUNT_ID and R2_BUCKET_NAME)

def _r2_db_request(method, data=None):
    """Construit une requête AWS SigV4 vers R2 pour la DB (GET ou PUT)."""
    from datetime import datetime as _dt
    now        = _dt.utcnow()
    date_stamp = now.strftime("%Y%m%d")
    amz_date   = now.strftime("%Y%m%dT%H%M%SZ")
    region, service = "auto", "s3"
    host     = f"{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
    endpoint = f"https://{host}/{R2_BUCKET_NAME}/{DB_R2_KEY}"
    payload_hash = hashlib.sha256(data or b"").hexdigest()

    if method == "PUT":
        ctype            = "application/octet-stream"
        headers_to_sign  = "content-type;host;x-amz-content-sha256;x-amz-date"
        canonical_hdrs   = (f"content-type:{ctype}\nhost:{host}\n"
                            f"x-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n")
    else:
        ctype            = None
        headers_to_sign  = "host;x-amz-content-sha256;x-amz-date"
        canonical_hdrs   = (f"host:{host}\n"
                            f"x-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n")

    canonical = f"{method}\n/{R2_BUCKET_NAME}/{DB_R2_KEY}\n\n{canonical_hdrs}\n{headers_to_sign}\n{payload_hash}"
    sts = (f"AWS4-HMAC-SHA256\n{amz_date}\n{date_stamp}/{region}/{service}/aws4_request\n"
           + hashlib.sha256(canonical.encode()).hexdigest())

    def _h(k, m): return hmac.new(k, m.encode(), hashlib.sha256).digest()
    sk  = _h(_h(_h(_h(f"AWS4{R2_SECRET_KEY}".encode(), date_stamp), region), service), "aws4_request")
    sig = hmac.new(sk, sts.encode(), hashlib.sha256).hexdigest()
    auth = (f"AWS4-HMAC-SHA256 Credential={R2_ACCESS_KEY}/{date_stamp}/{region}/{service}/aws4_request,"
            f"SignedHeaders={headers_to_sign},Signature={sig}")

    req = urllib.request.Request(endpoint, data=data, method=method)
    req.add_header("x-amz-date", amz_date)
    req.add_header("x-amz-content-sha256", payload_hash)
    req.add_header("Authorization", auth)
    if ctype:
        req.add_header("Content-Type", ctype)
    return req

def upload_db_to_r2():
    """Upload la DB SQLite vers R2 (thread-safe, appelé en arrière-plan)."""
    global LAST_BACKUP_TS, LAST_BACKUP_STATUS
    if not _r2_has_creds():
        LAST_BACKUP_STATUS = "local"
        return
    with _db_sync_lock:
        try:
            import sqlite3 as _sq
            conn = _sq.connect(str(db.DB_FILE))
            conn.execute("PRAGMA wal_checkpoint(FULL)")
            conn.close()
            data = db.DB_FILE.read_bytes()
            with urllib.request.urlopen(_r2_db_request("PUT", data), timeout=30):
                pass
            LAST_BACKUP_TS = time.time()
            LAST_BACKUP_STATUS = "ok"
            print(f"☁️  DB → R2 ({len(data)//1024} Ko)")
        except Exception as e:
            LAST_BACKUP_STATUS = "error"
            print(f"⚠️  Upload DB R2 : {e}")

def download_db_from_r2():
    """Télécharge la DB depuis R2 au démarrage. Retourne True si succès."""
    if not _r2_has_creds():
        return False
    try:
        with urllib.request.urlopen(_r2_db_request("GET"), timeout=30) as resp:
            data = resp.read()
        if len(data) < 4096:
            print("⚠️  DB R2 trop petite, ignorée")
            return False
        tmp = db.DB_FILE.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(db.DB_FILE)
        # Au démarrage : local == R2, donc considéré comme synchronisé maintenant
        global LAST_BACKUP_TS, LAST_BACKUP_STATUS
        LAST_BACKUP_TS = time.time()
        LAST_BACKUP_STATUS = "ok"
        print(f"☁️  DB ← R2 ({len(data)//1024} Ko) — données à jour")
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("ℹ️  Aucune DB sur R2 — upload initial en cours...")
            threading.Thread(target=upload_db_to_r2, daemon=True).start()
        else:
            print(f"⚠️  Download DB R2 : {e}")
        return False
    except Exception as e:
        print(f"⚠️  Download DB R2 : {e}")
        return False

def push_db_background():
    """Déclenche un upload R2 en thread séparé (non-bloquant)."""
    threading.Thread(target=upload_db_to_r2, daemon=True).start()

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

def _nb_sessions(ventes_list):
    """Nombre de ventes uniques (une session = même client + même jour)."""
    sessions = set()
    for v in ventes_list:
        ck = (v.get("client") or "").strip().lower()
        dk = (v.get("date_vente") or "")[:10]
        if dk:
            sessions.add(f"{ck}|{dk}")
    return len(sessions)

def is_lot(a):
    """Lot (ex : lots de chaînes) : le poids et le coût enregistrés sont DÉJÀ
    ceux du lot entier, et 'quantite' est le NOMBRE de pièces du lot.
    → il ne faut donc pas multiplier poids/coût par la quantité."""
    return str(a.get("ref_code") or "").startswith("chaine_")

def calc_stats(articles):
    qty = lambda a: int(a.get("quantite") or 1)
    # multiplicateur : 1 pour un lot (valeurs déjà totales), sinon la quantité
    m = lambda a: 1 if is_lot(a) else qty(a)
    return {
        # articles hors chaînes (pièces physiques, quantités comprises)
        "nb_articles": sum(qty(a) for a in articles if not is_lot(a)),
        # chaînes comptées à part (somme des lots)
        "nb_chaines": sum(qty(a) for a in articles if is_lot(a)),
        "total_or": round(sum((a["or_grs"] or 0) * m(a) for a in articles), 2),
        "valeur_stock": round(sum((a["pa"] or 0) * m(a) for a in articles), 0),
        "diamants": round(sum((a["d"] or 0) * m(a) for a in articles), 2),
        "emeraudes": round(sum((a["em"] or 0) * m(a) for a in articles), 2),
        "rubis": round(sum((a["r"] or 0) * m(a) for a in articles), 2),
        "saphirs": round(sum((a["s"] or 0) * m(a) for a in articles), 2),
        "rosaces": round(sum((a["rosaces"] or 0) * m(a) for a in articles), 2),
        "em_clb": round(sum((a["em_clb"] or 0) * m(a) for a in articles), 2),
        "perles": round(sum((a["perles"] or 0) * m(a) for a in articles), 2),
    }

def _is_service(v):
    """Vente de type service : exclue du CA produits mais comptée à part."""
    return (v.get("type_vente") or "produit") == "service"

def _is_reparation(v):
    """Vente de type réparation : comptée dans sa propre catégorie."""
    return (v.get("type_vente") or "produit") == "reparation"

# Pierres affichées sur l'étiquette : (champ base de données, abréviation)
# NB : pierres fines (p_fines) et perles (perles) volontairement exclues.
LABEL_STONES = [
    ("d", "D"), ("em", "Em"), ("r", "R"), ("s", "S"),
    ("rosaces", "Ros"), ("em_clb", "EmC"),
]

def build_label_payload(art, include_stones=True):
    """Construit le contenu prêt à imprimer d'une étiquette pour un article.
    Retourne {ref, article, stones:[[abbr, valeur], ...]}."""
    def _fmt(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        if f == 0:
            return None
        # 0.73 → "0.73", 3.0 → "3"
        return f"{f:g}"
    stones = []
    if include_stones:
        for col, abbr in LABEL_STONES:
            val = _fmt(art.get(col))
            if val is not None:
                stones.append([abbr, val])
    return {
        "ref": art.get("id"),
        "article": art.get("article") or "",
        "stones": stones,
    }

def migrate_chain_lots():
    """Remplace l'ancien lot vrac de chaînes (id 10) par 4 lots par couleur
    (audit magasin), vendus au poids, avec un CODE RÉFÉRENCE lisible
    (chaine_jaune, chaine_blanche, chaine_rose, chaine_cartier) au lieu d'un
    numéro. Crée les lots s'ils manquent, pose le code s'il manque. Idempotent."""
    cfg = load_config()
    if cfg.get("chain_lots_v2"):
        return
    articles = load_articles()
    # supprimer l'ancien lot vrac id 10 (une seule fois) — si c'est bien une "Chaîne"
    if not cfg.get("chain_lots_v1"):
        a10 = next((a for a in articles if a.get("id") == 10), None)
        if a10 is not None and a10.get("article") == "Chaîne":
            articles = [a for a in articles if a.get("id") != 10]
            try:
                db.log_audit("deleted", "article", 10,
                             "Chaîne (ancien lot vrac 314,2 g) — remplacé par lots couleur",
                             "system", "", "", snapshot=a10)
            except Exception:
                pass
    taux = float(cfg.get("prix_or_achat") or 1100)
    # (code, nom affiché, nb chaînes, poids total)
    lots = [("chaine_jaune", "Chaîne jaune", 32, 123.4),
            ("chaine_blanche", "Chaîne blanche", 34, 74.9),
            ("chaine_rose", "Chaîne rose", 16, 39.0),
            ("chaine_cartier", "Chaîne Cartier", 4, 14.1)]
    max_id = max((a["id"] for a in articles), default=0)
    today = datetime.now().strftime("%Y-%m-%d")
    for code, name, nb, poids in lots:
        ex = next((a for a in articles if a.get("article") == name or a.get("ref_code") == code), None)
        if ex:
            ex["ref_code"] = code            # poser le code sur un lot déjà créé
        else:
            max_id += 1
            articles.append({
                "id": max_id, "date": today, "article": name, "ref_code": code,
                "or_grs": poids, "pa": round(poids * taux, 2),
                "d": None, "em": None, "r": None, "s": None,
                "p_fines": None, "rosaces": None, "em_clb": None, "perles": None,
                "fabricant": "", "ismail_pierres": 0, "quantite": nb,
                "note": "Lot de chaînes (audit) — vendu au poids",
            })
    save_articles(articles)
    save_config({"chain_lots_v1": 1, "chain_lots_v2": 1})
    print("[MIGRATION] Lots de chaînes : codes chaine_jaune/blanche/rose/cartier posés (créés si manquants).")

def migrate_reprise_stock():
    """Corrige les reprises : bénéfice neutralisé (l'ancienne logique le mettait
    en négatif) et retrait des articles repris qui avaient été ajoutés
    automatiquement au stock (l'utilisateur les ajoute lui-même). Idempotent."""
    # 1. Neutraliser le bénéfice négatif des lignes reprise
    ventes = load_ventes()
    changed_v = 0
    for v in ventes:
        if v.get("type_vente") == "reprise" and float(v.get("benef") or 0) < 0:
            v["benef"] = 0
            changed_v += 1
    if changed_v:
        save_ventes(ventes)
        print(f"[MIGRATION] {changed_v} ligne(s) reprise : bénéfice neutralisé (0).")
    # 2. Retirer les articles repris auto-ajoutés par une version précédente
    articles = load_articles()
    keep = [a for a in articles if not (
        str(a.get("article", "")).startswith("Article repris")
        and str(a.get("note", "")).startswith("Repris de"))]
    if len(keep) != len(articles):
        save_articles(keep)
        print(f"[MIGRATION] {len(articles) - len(keep)} article(s) repris auto-ajouté(s) retiré(s).")

def detect_anomalies(ventes):
    """Détecte les ventes à marge/prix anormaux. Retourne une liste triée
    (plus récentes d'abord) de dicts {vente..., anomalies:[{code,label,severite}]}.
    Les services et réparations sont exclus (économie différente)."""
    out = []
    for v in ventes:
        if _is_service(v) or _is_reparation(v) or (v.get("type_vente") == "reprise"):
            continue
        pv    = float(v.get("pv") or 0)
        pa    = float(v.get("pa") or 0)
        benef = float(v.get("benef") if v.get("benef") is not None else (pv - pa))
        flags = []

        # Vente à perte
        if benef < 0:
            flags.append({"code": "perte", "severite": "alerte",
                          "label": f"Vente à perte ({_fmt_mad(benef)})"})
        # Prix de vente anormalement bas (probable faute de frappe : ex PV=1)
        elif pv > 0 and pa > 0 and pv < pa * 0.5:
            flags.append({"code": "pv_bas", "severite": "alerte",
                          "label": "Prix de vente très bas vs revient"})
        elif 0 < pv <= 100:
            flags.append({"code": "pv_suspect", "severite": "alerte",
                          "label": f"Prix de vente suspect ({_fmt_mad(pv)})"})
        # Marge nulle (PV = PA exactement, possible oubli)
        elif pv > 0 and abs(benef) < 1:
            flags.append({"code": "marge_nulle", "severite": "attention",
                          "label": "Marge nulle"})
        # Marge anormalement élevée (probable faute de frappe sur le PV)
        elif pa > 0 and benef > pa * 8:
            flags.append({"code": "marge_haute", "severite": "attention",
                          "label": f"Marge très élevée (×{round(benef/pa,1)})"})

        # Prix de revient manquant alors qu'il y a un PV
        if pa <= 0 and pv > 0 and not flags:
            flags.append({"code": "pa_manquant", "severite": "attention",
                          "label": "Prix de revient manquant"})

        if flags:
            d = dict(v)
            d["anomalies"] = flags
            d["_severite"] = "alerte" if any(f["severite"] == "alerte" for f in flags) else "attention"
            out.append(d)

    out.sort(key=lambda x: (x.get("date_vente") or ""), reverse=True)
    return out

def ventes_stats(ventes, date_from=None, date_to=None):
    """Stats ventes filtrées par période."""
    filt = ventes
    if date_from:
        filt = [v for v in filt if (v.get("date_vente") or "") >= date_from]
    if date_to:
        filt = [v for v in filt if (v.get("date_vente") or "") <= date_to]
    produits    = [v for v in filt if not _is_service(v) and not _is_reparation(v)]
    services    = [v for v in filt if _is_service(v)]
    reparations = [v for v in filt if _is_reparation(v)]
    return {
        "nb":           _nb_sessions(filt),
        "nb_articles":  len(filt),
        "ca":           round(sum(v.get("pv")    or 0 for v in produits), 0),
        "benef":        round(sum(v.get("benef") or 0 for v in produits), 0),
        "ca_service":   round(sum(v.get("pv")    or 0 for v in services), 0),
        "benef_service":round(sum(v.get("benef") or 0 for v in services), 0),
        "ca_reparation":   round(sum(v.get("pv")    or 0 for v in reparations), 0),
        "benef_reparation":round(sum(v.get("benef") or 0 for v in reparations), 0),
        "ca_total":     round(sum(v.get("pv")    or 0 for v in filt),     0),
        "benef_total":  round(sum(v.get("benef") or 0 for v in filt),     0),
        "or_vendu":     round(sum(v.get("or_grs") or 0 for v in filt),    2),
    }

def monthly_stats(ventes):
    """Regroupe les ventes par mois, retourne liste triée."""
    months = {}
    for v in ventes:
        d = (v.get("date_vente") or "")[:7]
        if not d:
            continue
        if d not in months:
            months[d] = {"mois": d, "nb": 0, "nb_articles": 0, "ca": 0, "benef": 0, "ca_service": 0, "benef_service": 0, "ca_reparation": 0, "benef_reparation": 0, "or_vendu": 0, "_ventes": []}
        months[d]["nb_articles"] += 1
        months[d]["_ventes"].append(v)
        if _is_service(v):
            months[d]["ca_service"]    += v.get("pv")    or 0
            months[d]["benef_service"] += v.get("benef") or 0
        elif _is_reparation(v):
            months[d]["ca_reparation"]    += v.get("pv")    or 0
            months[d]["benef_reparation"] += v.get("benef") or 0
        else:
            months[d]["ca"]    += v.get("pv")    or 0
            months[d]["benef"] += v.get("benef") or 0
        months[d]["or_vendu"] += v.get("or_grs") or 0
    result = sorted(months.values(), key=lambda x: x["mois"], reverse=True)
    for m in result:
        m["nb"] = _nb_sessions(m.pop("_ventes"))
        m["ca"]           = round(m["ca"], 0)
        m["benef"]        = round(m["benef"], 0)
        m["ca_service"]   = round(m["ca_service"], 0)
        m["benef_service"]= round(m["benef_service"], 0)
        m["ca_reparation"]   = round(m["ca_reparation"], 0)
        m["benef_reparation"]= round(m["benef_reparation"], 0)
        m["ca_total"]     = round(m["ca"] + m["ca_service"] + m["ca_reparation"], 0)
        m["benef_total"]  = round(m["benef"] + m["benef_service"] + m["benef_reparation"], 0)
        m["or_vendu"]     = round(m["or_vendu"], 2)
    return result

def annual_stats(ventes):
    """Regroupe les ventes par année."""
    years = {}
    for v in ventes:
        d = (v.get("date_vente") or "")[:4]
        if not d:
            continue
        if d not in years:
            years[d] = {"annee": d, "nb": 0, "nb_articles": 0, "ca": 0, "benef": 0, "_ventes": []}
        years[d]["nb_articles"] += 1
        years[d]["_ventes"].append(v)
        if not _is_service(v):
            years[d]["ca"] += v.get("pv") or 0
        years[d]["benef"] += v.get("benef") or 0
    result = sorted(years.values(), key=lambda x: x["annee"], reverse=True)
    for y in result:
        y["nb"] = _nb_sessions(y.pop("_ventes"))
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
        "quantite": max(1, int(data.get("quantite") or 1)),
        "note": str(data.get("note") or "").strip(),
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
        ca = sum(v.get('pv') or 0 for v in filt if not _is_service(v))
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
        ca_mois = sum(v.get('pv') or 0 for v in filt if not _is_service(v))
        benef_mois = sum(v.get('benef') or 0 for v in filt)
        ca_tot = sum(v.get('pv') or 0 for v in ventes if not _is_service(v))
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
        # Jamais de cache pour les données API → toujours à jour
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(body)
        # Sync DB → R2 après chaque écriture réussie (POST/DELETE)
        if getattr(self, "command", "") in ("POST", "DELETE", "PUT") and status < 300:
            push_db_background()

    # Pages sans sidebar (login, employé, accueil spécial)
    NO_SIDEBAR_PAGES = {'login.html', 'fiche_employe.html'}

    def send_html(self, path):
        try:
            content = path.read_text(encoding="utf-8")
            # Injecter les balises PWA si absentes (standalone iOS)
            pwa_tags = (
                '<meta name="apple-mobile-web-app-capable" content="yes">\n'
                '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">\n'
                '<meta name="apple-mobile-web-app-title" content="Trabelsi">\n'
                '<link rel="manifest" href="/static/manifest.json">\n'
            )
            if 'apple-mobile-web-app-capable' not in content:
                content = content.replace('<meta name="viewport"', pwa_tags + '<meta name="viewport"', 1)

            use_sidebar = path.name not in self.NO_SIDEBAR_PAGES

            if use_sidebar:
                # Injecter sidebar.css dans <head>
                sidebar_css = '<link rel="stylesheet" href="/static/sidebar.css">\n'
                if '/static/sidebar.css' not in content and '</head>' in content:
                    content = content.replace('</head>', sidebar_css + '</head>', 1)
                # Masquer la nav desktop immédiatement (évite le flash avant que sidebar.js tourne)
                hide_nav_css = (
                    '<style>'
                    '@media(min-width:861px){'
                    'body>nav{display:none!important;}'
                    '}'
                    '</style>\n'
                )
                if '</head>' in content:
                    content = content.replace('</head>', hide_nav_css + '</head>', 1)
                # Injecter sidebar.js avant </body>
                sidebar_script = '<script src="/static/sidebar.js"></script>\n'
                if '/static/sidebar.js' not in content and '</body>' in content:
                    content = content.replace('</body>', sidebar_script + '</body>', 1)
            else:
                # Pages sans sidebar : garder le fix nav-links mobile uniquement
                mobile_nav_fix = (
                    '<script>'
                    '(function(){'
                    'var nl=document.querySelector(".nav-links");'
                    'if(!nl)return;'
                    'var _obs=new MutationObserver(function(){});'
                    '})();'
                    '</script>\n'
                )
                if '</body>' in content:
                    content = content.replace('</body>', mobile_nav_fix + '</body>', 1)

            # Désactivation définitive du service worker : il causait des
            # blocages de cache. L'appli est en ligne, pas besoin d'offline.
            # On désenregistre tout SW existant + on vide les caches à chaque chargement.
            sw_killer = (
                '<script>'
                'if("serviceWorker" in navigator){'
                'navigator.serviceWorker.getRegistrations().then(function(rs){'
                'rs.forEach(function(r){r.unregister();});}).catch(function(){});'
                'if(window.caches){caches.keys().then(function(ks){'
                'ks.forEach(function(k){caches.delete(k);});}).catch(function(){});}}'
                '</script>\n'
            )
            if '</head>' in content:
                content = content.replace('</head>', sw_killer + '</head>', 1)

            # Neutraliser les anciens appels d'enregistrement du SW dans les pages
            content = re.sub(
                r'navigator\.serviceWorker\.register\([^)]*\)',
                '/*sw-désactivé*/0',
                content,
            )

            # Neutraliser les teintes dorées codées en dur dans les styles inline
            # (bordures, fonds, ombres) → gris sobre, sur toutes les pages.
            for _gold, _gray in (
                ("rgba(184,146,60,", "rgba(10,10,10,"),
                ("rgba(201,168,76,", "rgba(10,10,10,"),
                ("rgba(196,163,90,", "rgba(10,10,10,"),
            ):
                content = content.replace(_gold, _gray)

            # Cache-busting : ajoute ?v=VERSION à tous les CSS/JS statiques
            # → l'URL change à chaque déploiement, le navigateur recharge toujours.
            content = re.sub(
                r'(/static/[^"\'?#]+\.(?:css|js))(?=["\'?#])',
                lambda m: f"{m.group(1)}?v={ASSET_VERSION}",
                content,
            )

            body = content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(body))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()

    def send_etiquette_zebra(self, params):
        """Page brute auto-imprimante : UNE étiquette bijou haltère 60×12mm.
        Sert du HTML sans aucune injection (sidebar/sw), pour l'imprimante Zebra."""
        try:
            ref = int(params.get("ref", ["0"])[0])
        except Exception:
            ref = 0
        show_price = params.get("price", ["1"])[0] != "0"
        art = next((a for a in load_articles() if a.get("id") == ref), None)
        if not art:
            self.send_response(404); self.end_headers()
            self.wfile.write("Article introuvable".encode("utf-8")); return

        proto = self.headers.get("X-Forwarded-Proto", "http")
        host = self.headers.get("Host", "localhost")
        fiche_url = f"{proto}://{host}/fiche?ref={ref}"
        qr = "/api/qr?scale=3&text=" + urllib.parse.quote(fiche_url, safe="")

        price_html = ""
        pv = art.get("pv")
        if show_price and pv not in (None, ""):
            try:
                price_fmt = f"{int(round(float(pv))):,}".replace(",", " ")
            except Exception:
                price_fmt = str(pv)
            price_html = f'<div class="z-price">{price_fmt} MAD</div>'

        html = (
            '<!doctype html><meta charset="utf-8"><title>Etiquette #' + str(ref) + '</title>'
            '<style>'
            '@page{size:60mm 12mm;margin:0;}'
            'html,body{margin:0;padding:0;}'
            '.zbl{width:60mm;height:12mm;box-sizing:border-box;display:flex;align-items:stretch;'
            'font-family:Arial,Helvetica,sans-serif;color:#000;overflow:hidden;}'
            '.wing{width:24mm;box-sizing:border-box;padding:0.4mm 0.8mm;display:flex;'
            'flex-direction:column;align-items:center;justify-content:center;text-align:center;line-height:1;}'
            '.bridge{width:12mm;}'
            '.z-shop{font-size:5pt;letter-spacing:0.3pt;}'
            '.z-ref{font-size:9pt;font-weight:700;margin:0.2mm 0;}'
            '.z-price{font-size:7pt;font-weight:600;}'
            '.z-qr{width:9mm;height:9mm;display:block;}.z-qr img{width:100%;height:100%;}'
            '@media screen{body{background:#e5e5e5;display:flex;justify-content:center;'
            'padding:30px;}.zbl{background:#fff;box-shadow:0 2px 10px rgba(0,0,0,.2);}}'
            '</style>'
            '<div class="zbl">'
            '<div class="wing"><div class="z-shop">◆ TRABELSI</div>'
            '<div class="z-ref">#' + str(ref) + '</div>' + price_html + '</div>'
            '<div class="bridge"></div>'
            '<div class="wing"><div class="z-qr"><img id="qr" src="' + qr + '" alt="QR"></div>'
            '<div class="z-ref" style="font-size:6pt">#' + str(ref) + '</div></div>'
            '</div>'
            '<script>'
            'function go(){setTimeout(function(){window.print();},250);}'
            'var q=document.getElementById("qr");'
            'if(q&&!q.complete){q.onload=go;q.onerror=go;setTimeout(go,2000);}else{go();}'
            '</script>'
        )
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _actor(self):
        """Retourne (role, ip, device) de l'auteur de la requête courante."""
        try:
            role = get_role(self.headers) or "?"
            ip = _get_client_ip(self.headers)
            dev, br = parse_ua(self.headers.get("User-Agent", ""))
            return role, ip, f"{dev} · {br}"
        except Exception:
            return "?", "", ""

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
            # CSS et JS : jamais de cache (pour que les mises à jour soient immédiates)
            if path.suffix in (".css", ".js"):
                self.send_header("Cache-Control", "no-cache, must-revalidate")
            else:
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

        # ── JS communs ───────────────────────────────────────────────────────────
        if path.endswith(".js") and not path.startswith("/api"):
            self.send_static(STATIC_DIR / path.lstrip("/")); return

        # ── Login ─────────────────────────────────────────────────────────────
        if path == "/login":
            self.send_html(STATIC_DIR / "login.html"); return

        # ── Logout ────────────────────────────────────────────────────────────
        if path == "/logout":
            token = get_session_token(self.headers)
            if token: db.delete_session(token)
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
            if path == "/api/ventes":
                self.send_json(load_ventes()); return
            if path == "/api/credits":
                self.send_json(load_credits()); return
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
        if path == "/facture-libre":
            self.send_html(STATIC_DIR / "facture_libre.html"); return
        if path == "/devis":
            self.send_html(STATIC_DIR / "devis.html"); return
        if path == "/historique-devis":
            self.send_html(STATIC_DIR / "historique_devis.html"); return
        if path == "/clients":
            self.send_html(STATIC_DIR / "clients.html"); return
        if path == "/catalogue":
            self.send_html(STATIC_DIR / "catalogue.html"); return
        if path == "/historique-activite":
            self.send_html(STATIC_DIR / "historique_activite.html"); return
        if path == "/activite-employes":
            self.send_html(STATIC_DIR / "activite_employes.html"); return
        if path == "/etiquette-zebra":
            self.send_etiquette_zebra(params); return
        if path == "/etiquettes":
            self.send_html(STATIC_DIR / "etiquettes.html"); return
        if path == "/reparations":
            self.send_html(STATIC_DIR / "reparations.html"); return
        if path == "/reprise":
            self.send_html(STATIC_DIR / "reprise.html"); return
        if path == "/corbeille":
            self.send_html(STATIC_DIR / "corbeille.html"); return
        if path == "/galerie":
            self.send_html(STATIC_DIR / "galerie.html"); return
        # ── API articles ──────────────────────────────────────────────────────
        if path == "/api/articles":
            self.send_json(load_articles()); return

        if path.startswith("/api/articles/"):
            try:
                ref = int(path.split("/")[-1])
                articles = load_articles()
                found = [a for a in articles if a["id"] == ref]
                # Journaliser la recherche (employé ou admin) — GET, pas de sync R2
                try:
                    article_nom = found[0].get("article", "") if found else ""
                    _is_found = bool(found)
                    if not _is_found:
                        # Vérifier aussi dans les ventes (article déjà vendu)
                        _v = [v for v in load_ventes() if v.get("ref") == ref]
                        if _v:
                            article_nom = _v[-1].get("article", "")
                            _is_found = True
                    _ua = self.headers.get("User-Agent", "")
                    _dev, _br = parse_ua(_ua)
                    db.log_search(
                        get_role(self.headers), ref, article_nom, _is_found,
                        _get_client_ip(self.headers), f"{_dev} · {_br}"
                    )
                except Exception:
                    pass
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

        # ── API journal des recherches employés (admin) ────────────────────────
        if path == "/api/search-logs":
            if not is_admin(self.headers):
                self.send_json({"error": "Accès réservé à l'administrateur"}, 403); return
            qs = urllib.parse.parse_qs(parsed.query)
            limit = int((qs.get("limit", ["100"])[0]) or 100)
            since = qs.get("since", [None])[0]
            since_ts = float(since) if since else None
            # Stats du jour (depuis minuit)
            midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            self.send_json({
                "logs": db.get_search_logs(limit=min(limit, 500), since_ts=since_ts),
                "today": db.search_logs_stats(midnight),
            })
            return

        # ── API journal d'audit (admin) ─────────────────────────────────────────
        if path == "/api/audit-log":
            if not is_admin(self.headers):
                self.send_json({"error": "Accès réservé à l'administrateur"}, 403); return
            qs = urllib.parse.parse_qs(parsed.query)
            entity = qs.get("entity", [None])[0]
            action = qs.get("action", [None])[0]
            self.send_json({"logs": db.get_audit_logs(limit=300, entity=entity, action=action)})
            return

        # ── API corbeille — éléments supprimés restaurables (admin) ─────────────
        if path == "/api/trash":
            if not is_admin(self.headers):
                self.send_json({"error": "Accès réservé à l'administrateur"}, 403); return
            self.send_json({"items": db.get_trash(limit=5000)})
            return

        # ── API génération de QR code (SVG) ─────────────────────────────────────
        if path == "/api/qr":
            qs = urllib.parse.parse_qs(parsed.query)
            text = qs.get("text", [""])[0]
            try:
                scale = max(1, min(20, int(qs.get("scale", ["4"])[0])))
            except Exception:
                scale = 4
            if not text:
                self.send_json({"error": "Paramètre 'text' requis"}, 400); return
            try:
                import segno, io as _io
                qr = segno.make(text, error="m")
                buf = _io.BytesIO()
                qr.save(buf, kind="svg", scale=scale, border=1, dark="#1a1612")
                svg = buf.getvalue()
                self.send_response(200)
                self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
                self.send_header("Content-Length", str(len(svg)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(svg)
            except Exception as e:
                self.send_json({"error": f"QR indisponible : {e}"}, 500)
            return

        # ── API détection d'anomalies ───────────────────────────────────────────
        if path == "/api/anomalies":
            anomalies = detect_anomalies(load_ventes())
            self.send_json({
                "count": len(anomalies),
                "alertes": sum(1 for a in anomalies if a.get("_severite") == "alerte"),
                "anomalies": anomalies[:100],
            })
            return

        # ── File d'impression d'étiquettes (l'agent Mac interroge cette route) ──
        if path == "/api/print-queue":
            self.send_json({"jobs": db.get_pending_print_jobs()})
            return

        # ── API dernière sauvegarde ────────────────────────────────────────────
        if path == "/api/last-backup":
            self.send_json({
                "ts": LAST_BACKUP_TS,                       # epoch (secondes) ou None
                "status": LAST_BACKUP_STATUS,               # "ok" | "error" | "local"
                "iso": (datetime.fromtimestamp(LAST_BACKUP_TS).isoformat()
                        if LAST_BACKUP_TS else None),
            })
            return

        # ── API stats ─────────────────────────────────────────────────────────
        if path == "/api/stats":
            articles = load_articles()
            ventes = load_ventes()
            stats = calc_stats(articles)
            today = datetime.now().strftime("%Y-%m-%d")
            ventes_today = [v for v in ventes if (v.get("date_vente") or "").startswith(today)]
            stats["nb_ventes_total"] = _nb_sessions(ventes)
            stats["nb_articles_vendus_total"] = len(ventes)
            stats["nb_ventes_today"] = _nb_sessions(ventes_today)
            stats["nb_articles_vendus_today"] = len(ventes_today)
            stats["ca_today"] = round(sum(v.get("pv") or 0 for v in ventes_today if not _is_service(v)), 0)
            stats["benef_today"] = round(sum(v.get("benef") or 0 for v in ventes_today), 0)
            stats["ca_total"] = round(sum(v.get("pv") or 0 for v in ventes if not _is_service(v)), 0)
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

        # ── API devis ─────────────────────────────────────────────────────────
        if path == "/api/devis":
            self.send_json(load_devis()); return

        # ── API clients (dossier client) ──────────────────────────────────────
        if path == "/api/clients":
            ventes   = load_ventes()
            credits  = load_credits()
            factures = load_factures()
            cheques  = load_cheques()
            # Agréger par client (clé normalisée = minuscules sans espaces superflus)
            clients_map = {}   # key = nom_low (str normalisé)
            nom_display = {}   # key = nom_low → nom d'affichage (première occurrence la plus propre)
            def _ck(name):
                return (name or "").strip().lower()
            def _best_display(existing, new_name):
                # Préférer la version avec des majuscules (title case) sur tout-minuscules
                n = (new_name or "").strip()
                if not n: return existing
                if not existing: return n
                # Prendre celle qui a le plus de majuscules (= mieux formatée)
                if sum(1 for c in n if c.isupper()) > sum(1 for c in existing if c.isupper()):
                    return n
                return existing
            for v in ventes:
                raw = (v.get("client") or "").strip()
                c = raw.lower()
                if not c: continue
                nom_display[c] = _best_display(nom_display.get(c,""), raw)
                if c not in clients_map:
                    clients_map[c] = {"nom": nom_display[c], "telephone": v.get("telephone","") or "", "nb_achats": 0, "ca_total": 0, "benef_total": 0, "derniere_visite": "", "ventes": [], "factures": [], "credits": [], "cheques": []}
                e = clients_map[c]
                e["nom"] = nom_display[c]
                e["nb_achats"] += 1
                e["ca_total"]  += v.get("pv") or 0
                e["benef_total"] += v.get("benef") or 0
                dv = (v.get("date_vente") or "")[:10]
                if dv > e["derniere_visite"]: e["derniere_visite"] = dv
                if not e["telephone"] and v.get("telephone"): e["telephone"] = v["telephone"]
                e["ventes"].append(v)
            for f in factures:
                raw = (f.get("client") or "").strip()
                c = raw.lower()
                if not c: continue
                nom_display[c] = _best_display(nom_display.get(c,""), raw)
                if c in clients_map:
                    clients_map[c]["nom"] = nom_display[c]
                    clients_map[c]["factures"].append(f)
                else:
                    clients_map[c] = {"nom": nom_display[c], "telephone": f.get("telephone","") or "", "nb_achats": 0, "ca_total": 0, "benef_total": 0, "derniere_visite": (f.get("date") or "")[:10], "ventes": [], "factures": [f], "credits": [], "cheques": []}
            for cr in credits:
                raw = (cr.get("client") or "").strip()
                c = raw.lower()
                if c in clients_map:
                    nom_display[c] = _best_display(nom_display.get(c,""), raw)
                    clients_map[c]["nom"] = nom_display[c]
                    clients_map[c]["credits"].append(cr)
            for ch in cheques:
                raw = (ch.get("client") or "").strip()
                c = raw.lower()
                if c in clients_map:
                    nom_display[c] = _best_display(nom_display.get(c,""), raw)
                    clients_map[c]["nom"] = nom_display[c]
                    clients_map[c]["cheques"].append(ch)
            result = sorted(clients_map.values(), key=lambda x: x["ca_total"], reverse=True)
            # Retirer les listes détaillées pour la liste (trop lourd)
            summary = [{k: v for k, v in cl.items() if k not in ("ventes","factures","credits","cheques")} for cl in result]
            for i, cl in enumerate(result):
                summary[i]["credit_restant"] = sum(c.get("reste",0) for c in cl["credits"] if c.get("statut") != "solde")
                summary[i]["nb_factures"]    = len(cl["factures"])
            self.send_json(summary); return

        if path.startswith("/api/clients/"):
            nom = urllib.parse.unquote(path.split("/api/clients/")[1])
            ventes   = load_ventes()
            credits  = load_credits()
            factures = load_factures()
            cheques  = load_cheques()
            nom_low  = nom.strip().lower()
            detail = {
                "nom": nom,
                "ventes":   [v for v in ventes   if (v.get("client") or "").strip().lower() == nom_low],
                "factures": [f for f in factures  if (f.get("client") or "").strip().lower() == nom_low],
                "credits":  [c for c in credits   if (c.get("client") or "").strip().lower() == nom_low],
                "cheques":  [ch for ch in cheques if (ch.get("client") or "").strip().lower() == nom_low],
            }
            detail["ca_total"]      = sum(v.get("pv",0) or 0 for v in detail["ventes"])
            detail["benef_total"]   = sum(v.get("benef",0) or 0 for v in detail["ventes"])
            detail["credit_restant"]= sum(c.get("reste",0) for c in detail["credits"] if c.get("statut") != "solde")
            detail["telephone"]     = next((v.get("telephone") for v in detail["ventes"] if v.get("telephone")), "")
            self.send_json(detail); return

        # ── Backup complet ────────────────────────────────────────────────────
        if path == "/api/backup":
            backup = {
                "date": datetime.now().isoformat(),
                "articles": load_articles(),
                "ventes": load_ventes(),
                "credits": load_credits(),
                "factures": load_factures(),
                "cheques": load_cheques(),
                "fournisseurs": load_fournisseurs(),
            }
            body = json.dumps(backup, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition", f"attachment; filename=backup_trabelsi_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body); return

        # ── Export CSV ────────────────────────────────────────────────────────
        if path == "/api/export/ventes":
            qs = urllib.parse.parse_qs(parsed.query)
            date_from = qs.get("from", [""])[0]
            date_to   = qs.get("to",   [""])[0]
            ventes = load_ventes()
            if date_from: ventes = [v for v in ventes if (v.get("date_vente") or "") >= date_from]
            if date_to:   ventes = [v for v in ventes if (v.get("date_vente") or "") <= date_to]
            buf = io.StringIO()
            w = csv.writer(buf, delimiter=";")
            w.writerow(["Date vente","Date achat","Réf","Article","OR (grs)","PA","PV","Bénéfice","Client","Mode paiement","Commentaire","Source"])
            for v in sorted(ventes, key=lambda x: x.get("date_vente") or "", reverse=True):
                w.writerow([
                    v.get("date_vente",""), v.get("date_achat",""), v.get("ref",""),
                    v.get("article",""), v.get("or_grs",""), v.get("pa",""),
                    v.get("pv",""), v.get("benef",""), v.get("client",""),
                    v.get("mode_paiement",""), v.get("commentaire",""), v.get("source","")
                ])
            body = ("﻿" + buf.getvalue()).encode("utf-8")
            fname = f"ventes_trabelsi_{datetime.now().strftime('%Y%m%d')}.csv"
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f"attachment; filename={fname}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body); return

        if path == "/api/export/stock":
            articles = load_articles()
            buf = io.StringIO()
            w = csv.writer(buf, delimiter=";")
            w.writerow(["Réf","Date","Article","OR (grs)","PA","Diamants","Émeraudes","Rubis","Saphirs","EM.CLB","Perles","Fabricant","Quantité","Note"])
            for a in articles:
                w.writerow([
                    a.get("id",""), a.get("date",""), a.get("article",""),
                    a.get("or_grs",""), a.get("pa",""), a.get("d",""),
                    a.get("em",""), a.get("r",""), a.get("s",""),
                    a.get("em_clb",""), a.get("perles",""), a.get("fabricant",""),
                    a.get("quantite",1), a.get("note","")
                ])
            body = ("﻿" + buf.getvalue()).encode("utf-8")
            fname = f"stock_trabelsi_{datetime.now().strftime('%Y%m%d')}.csv"
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f"attachment; filename={fname}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body); return

        if path == "/api/export/credits":
            credits = load_credits()
            buf = io.StringIO()
            w = csv.writer(buf, delimiter=";")
            w.writerow(["ID","Client","Contact","Date achat","Article","Montant total","Reste","Statut","Note"])
            for c in credits:
                w.writerow([
                    c.get("id",""), c.get("client",""), c.get("contact",""),
                    c.get("date_achat",""), c.get("article",""), c.get("montant_total",""),
                    c.get("reste",""), c.get("statut",""), c.get("note","")
                ])
            body = ("﻿" + buf.getvalue()).encode("utf-8")
            fname = f"credits_trabelsi_{datetime.now().strftime('%Y%m%d')}.csv"
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f"attachment; filename={fname}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body); return

        # ── API notifs Ismail ─────────────────────────────────────────────────
        if path == "/api/notifs":
            notifs = [n for n in load_notifs() if not n.get("dismissed")]
            # Alertes stock faible (quantite = 1) — générées dynamiquement
            articles = load_articles()
            today_str = datetime.now().strftime("%Y-%m-%d")
            for a in articles:
                qty = int(a.get("quantite") or 1)
                if qty == 1:
                    ref = a.get("id")
                    # Vérifier si une notif stock_faible non-dismissed existe déjà pour cet article
                    already = any(
                        n.get("type") == "stock_faible" and n.get("ref") == ref
                        for n in notifs
                    )
                    if not already:
                        notifs.append({
                            "id": f"sf_{ref}",
                            "type": "stock_faible",
                            "date": today_str,
                            "ref": ref,
                            "article": a.get("article", ""),
                            "client": None,
                            "dismissed": False,
                        })
            self.send_json(notifs); return

        # ── API config (prix de l'or) ─────────────────────────────────────────
        if path == "/api/config":
            self.send_json(load_config()); return

        # ── API Historique d'activité ─────────────────────────────────────────
        if path == "/api/historique":
          try:
            qs = urllib.parse.parse_qs(parsed.query)
            date_from = qs.get("from", [""])[0]
            date_to   = qs.get("to",   [""])[0]

            def in_range(d):
                if not d: return False
                d = str(d)[:10]
                if date_from and d < date_from: return False
                if date_to   and d > date_to:   return False
                return True

            def fmt_mad(v):
                try: return f"{int(float(v or 0)):,}".replace(",", " ") + " MAD"
                except: return "— MAD"

            events = []

            # Identifier les ventes liées à un crédit (client + date uniquement)
            credit_keys = set()
            for c in load_credits():
                key = (
                    str(c.get("client") or "").strip().lower(),
                    str(c.get("date_achat") or "")[:10],
                )
                credit_keys.add(key)

            # Ventes
            for v in load_ventes():
                d = str(v.get("date_vente") or "")[:10]
                if in_range(d):
                    source    = v.get("source") or "stock"
                    pv        = float(v.get("pv") or 0)
                    benef     = float(v.get("benef") or 0)
                    ref_val   = v.get("ref")
                    # Vente libre = source "libre" OU ref=0 (article hors stock)
                    is_libre  = (source == "libre") or (str(ref_val) == "0") or (ref_val == 0)
                    vkey   = (
                        str(v.get("client") or "").strip().lower(),
                        d,
                    )
                    direct = vkey not in credit_keys
                    if is_libre:
                        enc_amount = benef          # vente libre → seulement le bénéfice
                    elif direct:
                        enc_amount = pv             # vente stock payée cash → PV complet
                    else:
                        enc_amount = 0              # sur crédit → compté via paiements crédit
                    events.append({"date": d, "type": "vente",
                        "titre": f"Vente #{ref_val} — {v.get('article') or '—'}",
                        "detail": f"PV : {fmt_mad(pv)}" + (f" · Bénéf : {fmt_mad(benef)}" if is_libre else ""),
                        "montant": pv, "ref": ref_val,
                        "client": v.get("client") or "—",
                        "article": v.get("article") or "—",
                        "enc_amount": enc_amount})

            # Articles ajoutés au stock
            for a in load_articles():
                d = str(a.get("date") or "")[:10]
                if in_range(d):
                    events.append({"date": d, "type": "stock",
                        "titre": f"Article ajouté #{a.get('id')} — {a.get('article') or '—'}",
                        "detail": f"OR : {a.get('or_grs') or '—'} grs · PA : {fmt_mad(a.get('pa'))}",
                        "montant": None, "ref": a.get("id")})

            # Crédits ouverts + paiements
            for c in load_credits():
                date_achat = str(c.get("date_achat") or "")[:10]
                if in_range(date_achat):
                    events.append({"date": date_achat, "type": "credit_ouvert",
                        "titre": f"Crédit ouvert — {c.get('client') or '—'}",
                        "detail": f"Montant : {fmt_mad(c.get('montant_total'))} · Art. : {c.get('article') or '—'}",
                        "montant": c.get("montant_total"), "ref": c.get("id")})
                for p in (c.get("paiements") or []):
                    d = str(p.get("date") or "")[:10]
                    if in_range(d):
                        same_day = (d == date_achat)
                        events.append({"date": d, "type": "credit_paiement",
                            "titre": f"Paiement crédit — {c.get('client') or '—'}",
                            "detail": f"Versement : {fmt_mad(p.get('montant'))} · Mode : {p.get('mode') or '—'}",
                            "montant": p.get("montant"), "ref": c.get("id"),
                            "hidden": same_day})

            # Chèques introduits + encaissés
            for ch in load_cheques():
                d = str(ch.get("created_at") or ch.get("date_cheque") or "")[:10]
                if in_range(d):
                    events.append({"date": d, "type": "cheque_intro",
                        "titre": f"Chèque introduit — {ch.get('client') or '—'}",
                        "detail": f"Montant : {fmt_mad(ch.get('montant'))} · Banque : {ch.get('banque') or '—'}",
                        "montant": ch.get("montant"), "ref": ch.get("id")})
                nb = max(int(ch.get("nb_cheques") or 1), 1)
                montant_unit = (ch.get("montant") or 0) / nb
                for de in (ch.get("dates_encaissement") or []):
                    d = str(de or "")[:10]
                    if in_range(d):
                        events.append({"date": d, "type": "cheque_encaisse",
                            "titre": f"Chèque encaissé — {ch.get('client') or '—'}",
                            "detail": f"Montant : {fmt_mad(montant_unit)} · Banque : {ch.get('banque') or '—'}",
                            "montant": montant_unit, "ref": ch.get("id")})

            # Factures
            for fac in load_factures():
                d = str(fac.get("date") or "")[:10]
                if in_range(d):
                    events.append({"date": d, "type": "facture",
                        "titre": f"Facture — {fac.get('client') or '—'}",
                        "detail": f"N° {fac.get('numero') or '—'} · Total : {fmt_mad(fac.get('total'))}",
                        "montant": fac.get("total"), "ref": fac.get("id")})

            # Devis
            for dv in load_devis():
                d = str(dv.get("date_devis") or "")[:10]
                if in_range(d):
                    events.append({"date": d, "type": "devis",
                        "titre": f"Devis — {dv.get('client') or '—'}",
                        "detail": f"Total : {fmt_mad(dv.get('total_initial'))}",
                        "montant": dv.get("total_initial"), "ref": dv.get("id")})

            # Paiements fournisseurs
            for fo in load_fournisseurs():
                for p in (fo.get("paiements") or []):
                    d = str(p.get("date") or "")[:10]
                    if in_range(d):
                        events.append({"date": d, "type": "fournisseur",
                            "titre": f"Paiement fournisseur — {fo.get('fournisseur') or '—'}",
                            "detail": f"Versement : {fmt_mad(p.get('montant'))} · Mode : {p.get('mode') or '—'}",
                            "montant": p.get("montant"), "ref": fo.get("id")})

            events.sort(key=lambda e: e.get("date") or "", reverse=True)
            self.send_json(events); return
          except Exception as e:
            import traceback; traceback.print_exc()
            self.send_json({"error": str(e)}, 500); return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        # Verrou : une seule écriture à la fois (anti perte de données)
        with _WRITE_LOCK:
            self._handle_POST()

    def _handle_POST(self):
        path = urllib.parse.urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        # ── Login ─────────────────────────────────────────────────────────────
        if path == "/api/login":
            ip = _get_client_ip(self.headers)
            blocked, secs = _check_brute_force(ip)
            if blocked:
                self.send_json({"error": f"Trop de tentatives. Réessayez dans {secs//60+1} min."}, 429); return
            try:
                data = json.loads(body)
            except:
                self.send_json({"error": "Invalide"}, 400); return
            pwd = data.get("password", "")
            if _pwd_match(pwd, MOT_DE_PASSE_ADMIN):
                role = "admin"
            elif _pwd_match(pwd, MOT_DE_PASSE_EMPLOYE):
                role = "employe"
            else:
                _record_failed_login(ip)
                self.send_json({"error": "Mot de passe incorrect"}, 401); return
            _reset_login_attempts(ip)
            token = secrets.token_hex(32)
            db.create_session(token, role)
            db.cleanup_old_sessions(72)  # nettoyage sessions > 72h
            # Employé → redirige vers /fiche après login
            redirect = "/fiche" if role == "employe" else "/accueil"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            # Ajouter Secure si on est en HTTPS (Railway/production)
            is_https = (self.headers.get("X-Forwarded-Proto") == "https"
                        or bool(os.environ.get("RAILWAY_ENVIRONMENT")))
            secure_flag = "; Secure" if is_https else ""
            self.send_header("Set-Cookie", f"session={token}; Path=/; HttpOnly; SameSite=Strict{secure_flag}")
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

        # ── Étiquettes : mettre un article dans la file d'impression ──────────
        if path == "/api/print-label":
            try:
                ref = int(data.get("ref"))
            except (TypeError, ValueError):
                self.send_json({"error": "Référence invalide"}, 400); return
            include_stones = bool(data.get("stones", True))
            try:
                copies = int(data.get("copies", 1))
            except (TypeError, ValueError):
                copies = 1
            copies = max(1, min(copies, 99))
            art = next((a for a in load_articles() if a.get("id") == ref), None)
            if not art:
                self.send_json({"error": "Article introuvable"}, 404); return
            payload = build_label_payload(art, include_stones)
            payload["copies"] = copies
            job_id = db.add_print_job(ref, payload)
            self.send_json({"success": True, "job_id": job_id, "copies": copies})
            return

        # ── Étiquettes : l'agent marque une étiquette comme imprimée ──────────
        if path == "/api/print-queue/done":
            try:
                jid = int(data.get("id"))
            except (TypeError, ValueError):
                self.send_json({"error": "ID invalide"}, 400); return
            db.mark_print_job_done(jid)
            self.send_json({"success": True})
            return

        # ── Étiquettes : vider la file d'attente ──────────────────────────────
        if path == "/api/print-queue/clear":
            db.clear_pending_print_jobs()
            self.send_json({"success": True})
            return

        # ── Restaurer un élément supprimé (corbeille) ─────────────────────────
        if path.startswith("/api/audit/restore/"):
            try:
                aid = int(path.split("/")[-1])
            except:
                self.send_json({"error": "ID invalide"}, 400); return
            entry = db.get_audit_entry(aid)
            if not entry or entry.get("action") != "deleted" or not entry.get("snapshot"):
                self.send_json({"error": "Élément non restaurable"}, 404); return
            if entry.get("restored"):
                self.send_json({"error": "Déjà restauré"}, 409); return
            snap = entry["snapshot"]; ent = entry["entity"]
            try:
                if ent == "vente":
                    items = load_ventes()
                    if not any(v.get("id_vente") == snap.get("id_vente") for v in items):
                        items.append(snap); save_ventes(items)
                elif ent == "article":
                    items = load_articles()
                    if not any(a.get("id") == snap.get("id") for a in items):
                        items.append(snap); save_articles(items)
                elif ent == "credit":
                    items = load_credits()
                    if not any(c.get("id") == snap.get("id") for c in items):
                        items.append(snap); save_credits(items)
                elif ent == "fournisseur":
                    items = load_fournisseurs()
                    if not any(f.get("id") == snap.get("id") for f in items):
                        items.append(snap); save_fournisseurs(items)
                elif ent == "cheque":
                    items = load_cheques()
                    if not any(c.get("id") == snap.get("id") for c in items):
                        items.append(snap); save_cheques(items)
                else:
                    self.send_json({"error": "Type non restaurable"}, 400); return
                db.mark_audit_restored(aid)
                r_, ip_, dev_ = self._actor()
                db.log_audit("restored", ent, entry.get("ref", ""),
                             f"Restauration : {entry.get('summary','')}", r_, ip_, dev_)
                self.send_json({"success": True}); return
            except Exception as e:
                self.send_json({"error": str(e)}, 400); return

        # ── Fusionner deux clients (variations de nom) ────────────────────────
        if path == "/api/clients/merge":
            src = (data.get("from") or "").strip()
            dst = (data.get("to") or "").strip()
            if not src or not dst:
                self.send_json({"error": "Paramètres 'from' et 'to' requis"}, 400); return
            if src.lower() == dst.lower():
                self.send_json({"error": "Les deux noms sont identiques"}, 400); return
            src_l = src.lower()
            def _rename(items, save):
                n = 0
                for it in items:
                    if (it.get("client") or "").strip().lower() == src_l:
                        it["client"] = dst; n += 1
                if n: save(items)
                return n
            ventes = load_ventes();      nv = _rename(ventes, save_ventes)
            factures = load_factures();  _rename(factures, save_factures)
            credits = load_credits();    _rename(credits, save_credits)
            cheques = load_cheques();    _rename(cheques, save_cheques)
            r_, ip_, dev_ = self._actor()
            db.log_audit("modified", "client", dst,
                         f"Fusion client : « {src} » → « {dst} » ({nv} vente(s))",
                         r_, ip_, dev_)
            self.send_json({"success": True, "renamed": nv, "from": src, "to": dst}); return

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
            date_vente_input = str(data.get("date_vente", "")).strip()
            try:
                from datetime import date as _date
                datetime.strptime(date_vente_input, "%Y-%m-%d")
                date_vente_str = date_vente_input
            except Exception:
                date_vente_str = now.strftime("%Y-%m-%d")
            cfg = load_config()

            if is_poids_article(article):
                # ── Vente au poids ──────────────────────────────────────────
                poids_vendu = float(data.get("poids_vendu") or 0)
                if poids_vendu <= 0:
                    self.send_json({"error": "Poids à vendre requis (> 0)"}, 400); return
                stock_actuel = float(article.get("or_grs") or 0)   # poids d'UNE pièce
                qty_actuelle = int(article.get("quantite") or 1)
                stock_total  = round(stock_actuel * qty_actuelle, 3)
                if poids_vendu > stock_total + 0.001:
                    self.send_json({"error": f"Stock insuffisant : {stock_total} grs disponibles"}, 400); return
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
                    "date_vente": date_vente_str,
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
                    "telephone": str(data.get("telephone", "")).strip(),
                    "mode_paiement": str(data.get("mode_paiement", "")).strip(),
                    "avance": float(data["avance"]) if data.get("avance") not in (None, "") else None,
                    "commentaire": str(data.get("note", "")).strip(),
                    "type_vente": str(data.get("type_vente", "produit")).strip() or "produit",
                }
                # Déduire du stock
                if qty_actuelle > 1:
                    # Article en plusieurs pièces (ex : 12 B.O identiques) :
                    # on retire UNE pièce, le poids unitaire reste inchangé.
                    articles[idx]["quantite"] = qty_actuelle - 1
                else:
                    # Pièce unique vendue au poids : on soustrait les grammes
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
                r_, ip_, dev_ = self._actor()
                db.log_audit("created", "vente", vente.get("ref", ""),
                             f"Vente #{vente.get('ref','')} — {vente.get('article','')} · "
                             f"{vente.get('client','?')} · {vente.get('pv',0)} MAD",
                             r_, ip_, dev_)
                merge_duplicate_factures(); auto_generate_missing_factures()
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
                    "date_vente": date_vente_str,
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
                    "telephone": str(data.get("telephone", "")).strip(),
                    "mode_paiement": str(data.get("mode_paiement", "")).strip(),
                    "avance": float(data["avance"]) if data.get("avance") not in (None, "") else None,
                    "commentaire": str(data.get("note", "")).strip(),
                    "type_vente": str(data.get("type_vente", "produit")).strip() or "produit",
                }
                qty = int(articles[idx].get("quantite") or 1)
                if qty > 1:
                    articles[idx]["quantite"] = qty - 1
                else:
                    articles.pop(idx)
                save_articles(articles)
                ventes = load_ventes()
                ventes.append(vente)
                save_ventes(ventes)
                r_, ip_, dev_ = self._actor()
                db.log_audit("created", "vente", vente.get("ref", ""),
                             f"Vente #{vente.get('ref','')} — {vente.get('article','')} · "
                             f"{vente.get('client','?')} · {vente.get('pv',0)} MAD",
                             r_, ip_, dev_)
                merge_duplicate_factures(); auto_generate_missing_factures()
                if article.get("ismail_pierres"):
                    add_notif_ismail(article, vente["client"], article["id"])
                self.send_json({"success": True, "vente": vente}); return

        # ── Vente d'une chaîne depuis un lot (jaune/blanche/rose/cartier) ─────
        # Déduit le nb de chaînes ET le poids du lot. P.R = grammage × taux or.
        if path == "/api/ventes/chaine":
            code = str(data.get("ref_code", "")).strip()
            articles = load_articles()
            idx = next((i for i, a in enumerate(articles) if a.get("ref_code") == code), None)
            if idx is None:
                self.send_json({"error": "Lot de chaînes introuvable"}, 404); return
            lot = articles[idx]
            try:
                nb = int(data.get("quantite") or 0)
            except (TypeError, ValueError):
                nb = 0
            try:
                gram = float(data.get("grammage") or 0)
            except (TypeError, ValueError):
                gram = 0
            try:
                pv = parse_positive(data.get("pv"), "Prix de vente")
            except ValueError as e:
                self.send_json({"error": str(e)}, 400); return
            if nb <= 0:
                self.send_json({"error": "Nombre de chaînes requis (> 0)"}, 400); return
            if gram <= 0:
                self.send_json({"error": "Grammage requis (> 0)"}, 400); return
            stock_nb = int(lot.get("quantite") or 0)
            stock_g  = float(lot.get("or_grs") or 0)
            if nb > stock_nb:
                self.send_json({"error": f"Stock insuffisant : {stock_nb} chaîne(s) dispo"}, 400); return
            if gram > stock_g + 0.001:
                self.send_json({"error": f"Stock insuffisant : {stock_g} g dispo"}, 400); return
            cfg = load_config()
            taux = float(cfg.get("prix_or_achat") or 1100)
            pa = round(gram * taux, 2)
            now = datetime.now()
            date_v = str(data.get("date_vente") or now.strftime("%Y-%m-%d")).strip()
            try: datetime.strptime(date_v, "%Y-%m-%d")
            except: date_v = now.strftime("%Y-%m-%d")
            vente = {
                "id_vente": int(now.timestamp() * 1000),
                "date_achat": lot.get("date"), "date_vente": date_v,
                "ref": lot["id"],
                "article": f"{lot.get('article','Chaîne')} (×{nb})",
                "or_grs": gram, "vente_au_poids": True,
                "prix_or_achat": taux, "pa": pa,
                "d": None, "em": None, "r": None, "s": None,
                "p_fines": None, "rosaces": None, "em_clb": None, "perles": None,
                "pv": pv, "benef": round(pv - pa, 2),
                "client": str(data.get("client", "")).strip(),
                "telephone": str(data.get("telephone", "")).strip(),
                "mode_paiement": str(data.get("mode_paiement", "")).strip(),
                "commentaire": str(data.get("note", "")).strip(),
                "type_vente": "produit",
            }
            # Déduire du lot (nb chaînes + poids). Le lot reste (catégorie permanente).
            articles[idx]["quantite"] = max(0, stock_nb - nb)
            articles[idx]["or_grs"]   = round(max(0.0, stock_g - gram), 3)
            save_articles(articles)
            ventes = load_ventes(); ventes.append(vente); save_ventes(ventes)
            r_, ip_, dev_ = self._actor()
            db.log_audit("created", "vente", lot["id"],
                         f"Vente chaîne {lot.get('article','')} ×{nb} · {gram} g · "
                         f"{vente['client'] or '?'} · {pv} MAD", r_, ip_, dev_)
            self.send_json({"success": True, "vente": vente,
                            "reste_nb": articles[idx]["quantite"],
                            "reste_g": articles[idx]["or_grs"]}); return

        # ── Vente avec REPRISE (rachat + articles rendus) ─────────────────────
        # La cliente achète des articles (déduits du stock, comptés normalement)
        # et rend des articles auxquels on attribue une valeur. Ce qu'elle paie
        # = total acheté − total repris. La reprise réduit le bénéfice (une ligne
        # "reprise" en négatif). Même gestion paiement : total / avance / crédit.
        if path == "/api/ventes/reprise":
            achats   = data.get("achats", [])
            reprises = data.get("reprises", [])
            if not achats:
                self.send_json({"error": "Au moins un article acheté requis"}, 400); return
            client = str(data.get("client", "")).strip()
            tel    = str(data.get("telephone", "")).strip()
            note   = str(data.get("note", "")).strip()
            mode   = str(data.get("mode_paiement", "total")).strip() or "total"
            now    = datetime.now()
            date_v = str(data.get("date_vente") or now.strftime("%Y-%m-%d")).strip()
            try: datetime.strptime(date_v, "%Y-%m-%d")
            except: date_v = now.strftime("%Y-%m-%d")

            articles = load_articles()
            ventes   = load_ventes()
            groupe   = int(now.timestamp() * 1000)      # identifiant de la transaction
            new_ventes = []
            fac_articles = []          # lignes de la facture reprise
            total_achats = 0.0

            # 1) Articles achetés → ventes produit + déduction du stock
            for i, a in enumerate(achats):
                try:
                    ref = int(a.get("ref"))
                except (TypeError, ValueError):
                    self.send_json({"error": "Référence d'achat invalide"}, 400); return
                idx = next((k for k, x in enumerate(articles) if x["id"] == ref), None)
                if idx is None:
                    self.send_json({"error": f"Article #{ref} introuvable en stock"}, 404); return
                art = articles[idx]
                try:
                    pv = parse_positive(a.get("pv"), f"Prix de vente #{ref}")
                except ValueError as e:
                    self.send_json({"error": str(e)}, 400); return
                pa = art.get("pa") or 0
                v = {
                    "id_vente": groupe + i,
                    "date_achat": art.get("date"),
                    "date_vente": date_v,
                    "ref": ref,
                    "article": art.get("article", ""),
                    "or_grs": art.get("or_grs"),
                    "pa": pa,
                    "d": art.get("d"), "em": art.get("em"), "r": art.get("r"), "s": art.get("s"),
                    "p_fines": art.get("p_fines"), "rosaces": art.get("rosaces"),
                    "em_clb": art.get("em_clb"), "perles": art.get("perles"),
                    "pv": pv,
                    "benef": round(pv - pa, 2),
                    "client": client, "telephone": tel,
                    "mode_paiement": mode,
                    "commentaire": note,
                    "type_vente": "produit",
                    "reprise": True,
                    "reprise_groupe": groupe,
                }
                new_ventes.append(v)
                total_achats += pv
                _pierres = ", ".join(str(art.get(pk)) for pk in
                            ["d","em","r","s","p_fines","rosaces","em_clb","perles"] if art.get(pk))
                fac_articles.append({
                    "ref": ref, "article": art.get("article","") or "—",
                    "or_grs": art.get("or_grs") or "", "pierres": _pierres,
                    "pv": pv, "pa": pa, "reprise_rendu": False,
                })
                qty = int(art.get("quantite") or 1)
                if qty > 1:
                    articles[idx]["quantite"] = qty - 1
                else:
                    articles.pop(idx)

            # 2) Articles rendus → total repris. La reprise ne réduit PAS le
            #    bénéfice et n'entre PAS en stock (l'utilisateur ajoute les
            #    articles repris lui-même, au prix qu'il souhaite).
            total_reprise = 0.0
            desc = []
            for r in reprises:
                try:
                    val = float(r.get("valeur") or 0)
                except (TypeError, ValueError):
                    val = 0
                if val <= 0:
                    continue
                total_reprise += val
                rref = str(r.get("ref", "")).strip()
                desc.append((f"#{rref} " if rref else "") + f"{val:g}")
                # ligne facture : article rendu (valeur en négatif)
                fac_articles.append({
                    "ref": rref, "article": "♻️ Article repris" + (f" #{rref}" if rref else ""),
                    "or_grs": "", "pierres": "",
                    "pv": -val, "pa": 0, "reprise_rendu": True,
                })
            # ligne "reprise" dans les ventes (traçabilité) — bénéfice NEUTRE (0)
            if total_reprise > 0:
                new_ventes.append({
                    "id_vente": groupe + 900,
                    "date_achat": date_v, "date_vente": date_v,
                    "ref": 0,
                    "article": "♻️ Reprise : " + ", ".join(desc),
                    "or_grs": None, "pa": 0,
                    "d": None, "em": None, "r": None, "s": None,
                    "p_fines": None, "rosaces": None, "em_clb": None, "perles": None,
                    "pv": 0,
                    "benef": 0,
                    "client": client, "telephone": tel,
                    "mode_paiement": mode, "commentaire": note,
                    "type_vente": "reprise",
                    "reprise": True,
                    "reprise_groupe": groupe,
                })

            save_articles(articles)
            ventes.extend(new_ventes)
            save_ventes(ventes)
            net = round(total_achats - total_reprise, 2)

            # Montant réellement payé selon le mode
            if mode == "total":
                avance_payee = net if net > 0 else 0.0
            elif mode == "avance":
                try: avance_payee = float(data.get("avance") or 0)
                except (TypeError, ValueError): avance_payee = 0.0
                avance_payee = max(0.0, min(avance_payee, max(net, 0)))
            else:  # credit
                avance_payee = 0.0

            r_, ip_, dev_ = self._actor()
            db.log_audit("created", "vente", groupe,
                         f"Reprise — {client or '?'} · acheté {total_achats:g} − repris "
                         f"{total_reprise:g} = net {net:g} MAD", r_, ip_, dev_)

            # 3) Facture reprise (achats positifs + articles rendus négatifs, total = net)
            factures = load_factures()
            annee = now.strftime("%Y")
            seqs = [f.get("numero","") for f in factures if str(f.get("numero","")).startswith(f"FAC-{annee}-")]
            max_seq = 0
            for n in seqs:
                try: max_seq = max(max_seq, int(str(n).split("-")[-1]))
                except (ValueError, IndexError): pass
            numero = f"FAC-{annee}-{str(max_seq+1).zfill(4)}"
            max_fid = max((f.get("id", 0) for f in factures), default=0)
            facture = {
                "id": max_fid + 1, "numero": numero,
                "client": client, "telephone": tel, "email": "", "ville": "",
                "articles": fac_articles, "total": net, "avance": avance_payee,
                "mode_paiement": mode, "note": "Reprise",
                "date": date_v, "created_at": now.isoformat(),
                "reprise": True,
                "total_achats": round(total_achats, 2),
                "total_reprise": round(total_reprise, 2),
            }
            factures.append(facture)
            save_factures(factures)

            # 4) Paiement : avance / crédit → crédit client sur le net
            credit = None
            if mode in ("avance", "credit") and net > 0:
                credits = load_credits()
                paiements = []
                if avance_payee > 0:
                    paiements.append({"montant": avance_payee, "date": date_v, "mode": "reprise"})
                cid = max((c["id"] for c in credits), default=0) + 1
                credit = {
                    "id": cid, "client": client, "contact": tel or None,
                    "date_achat": date_v,
                    "refs": ", ".join(str(v["ref"]) for v in new_ventes if v.get("type_vente") == "produit"),
                    "article": "Reprise", "montant_total": net,
                    "paiements": paiements, "reste": 0, "statut": "rien",
                    "date_solde": None, "note": note,
                }
                recalc_credit(credit)
                credits.append(credit)
                save_credits(credits)

            self.send_json({"success": True, "total_achats": round(total_achats, 2),
                            "total_reprise": round(total_reprise, 2), "net": net,
                            "numero": numero, "credit": credit}); return

        # ── Vente / Facture manuelle (hors stock) ────────────────────────────
        if path == "/api/ventes/manuel":
            lignes = data.get("articles", [])
            if not lignes:
                self.send_json({"error": "Au moins un article requis"}, 400); return
            client     = str(data.get("client", "")).strip()
            tel        = str(data.get("telephone", "")).strip()
            note       = str(data.get("note", "")).strip()
            mode       = str(data.get("mode_paiement", "")).strip()
            type_vente = str(data.get("type_vente", "produit")).strip() or "produit"
            now     = datetime.now()
            date_v  = str(data.get("date_vente") or now.strftime("%Y-%m-%d")).strip()
            try: datetime.strptime(date_v, "%Y-%m-%d")
            except: date_v = now.strftime("%Y-%m-%d")

            ventes = load_ventes()
            new_ventes = []
            fac_articles = []
            total_pv = 0.0
            or_total = 0.0

            for i, ligne in enumerate(lignes):
                pv   = float(ligne.get("pv") or 0)
                pa   = float(ligne.get("pa") or 0)
                benef = round(pv - pa, 2)
                or_grs = float(ligne.get("or_grs") or 0)
                id_v = int(now.timestamp() * 1000) + i
                v = {
                    "id_vente": id_v,
                    "date_achat": date_v,
                    "date_vente": date_v,
                    "ref": 0,
                    "article": str(ligne.get("article","")).strip() or "Article",
                    "or_grs": or_grs or None,
                    "vente_au_poids": False,
                    "prix_or_achat": None,
                    "pa": pa or None,
                    "d":  float(ligne["d"])  if ligne.get("d")  else None,
                    "em": float(ligne["em"]) if ligne.get("em") else None,
                    "r":  float(ligne["r"])  if ligne.get("r")  else None,
                    "s":  float(ligne["s"])  if ligne.get("s")  else None,
                    "p_fines": None, "rosaces": None,
                    "em_clb": float(ligne["em_clb"]) if ligne.get("em_clb") else None,
                    "perles": float(ligne["perles"]) if ligne.get("perles") else None,
                    "pv": pv,
                    "benef": benef,
                    "client": client,
                    "telephone": tel,
                    "mode_paiement": mode,
                    "commentaire": note,
                    "source": "libre",
                    "type_vente": type_vente,
                }
                new_ventes.append(v)
                fac_articles.append({
                    "ref": 0, "article": v["article"], "pv": pv,
                    "or_grs": or_grs,
                    "d": v["d"], "em": v["em"], "r": v["r"], "s": v["s"],
                    "em_clb": v["em_clb"], "perles": v["perles"],
                })
                total_pv += pv
                or_total += or_grs

            ventes.extend(new_ventes)
            save_ventes(ventes)

            # Audit
            r_, ip_, dev_ = self._actor()
            _libelle = {"service": "Service", "reparation": "Réparation"}.get(type_vente, "Vente libre")
            for v in new_ventes:
                db.log_audit("created", "vente", v.get("ref", 0),
                             f"{_libelle} — "
                             f"{v.get('article','')} · {client or '?'} · {v.get('pv',0)} MAD "
                             f"(bénéf {v.get('benef',0)})", r_, ip_, dev_)

            # Service / Réparation : pas de facture de vente générée (compta uniquement)
            if type_vente in ("service", "reparation"):
                self.send_json({"success": True, type_vente: True,
                                "nb_ventes": len(new_ventes)}); return

            # Créer la facture automatiquement
            factures = load_factures()
            annee = now.strftime("%Y")
            num_existants = [f.get("numero","") for f in factures if f.get("numero","").startswith(f"FAC-{annee}-")]
            max_seq = 0
            for n in num_existants:
                try: max_seq = max(max_seq, int(n.split("-")[-1]))
                except: pass
            numero = f"FAC-{annee}-{str(max_seq+1).zfill(4)}"

            # Prix global ?
            prix_global = int(bool(data.get("prix_global")))
            total_global = float(data.get("total_global") or 0)

            facture = {
                "id": int(now.timestamp() * 1000) + 9999,
                "numero": numero,
                "client": client,
                "telephone": tel,
                "email": None, "ville": None,
                "articles": fac_articles,
                "total": total_global if prix_global and total_global > 0 else total_pv,
                "avance": float(data.get("avance") or 0),
                "mode_paiement": mode,
                "note": note,
                "date": date_v,
                "created_at": now.isoformat(),
                "prix_global": prix_global,
                "total_global": total_global,
            }
            factures.append(facture)
            save_factures(factures)
            self.send_json({"success": True, "facture": facture, "nb_ventes": len(new_ventes)}); return

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
            source = str(data.get("source", "")).strip()
            date_vente = str(data.get("date_vente") or now.strftime("%Y-%m-%d")).strip()
            total_val = float(data.get("total_global") or data.get("total") or 0)
            fac_articles = data.get("articles", [])
            facture = {
                "id": new_id,
                "numero": numero,
                "client": str(data.get("client", "")).strip(),
                "telephone": str(data.get("telephone", "")).strip() or None,
                "email": str(data.get("email", "")).strip() or None,
                "ville": str(data.get("ville", "")).strip() or None,
                "articles": fac_articles,
                "total": total_val,
                "avance": float(data.get("avance") or 0),
                "mode_paiement": str(data.get("mode_paiement", "")).strip() or None,
                "note": str(data.get("note", "")).strip(),
                "date": date_vente,
                "created_at": now.isoformat(),
                "source": source,
                "vente_validee": False,
                "prix_global": int(data.get("prix_global") or 0),
                "total_global": float(data.get("total_global") or 0),
            }

            # Facture libre : enregistrer la vente (CA/bénéfice) + déduire le stock
            if source == "libre":
                cfg = load_config()
                prix_or_achat = cfg.get("prix_or_achat", 1000)
                articles_stock = load_articles()
                ventes = load_ventes()
                client_nom = facture["client"]
                stock_touche = False
                new_ventes = []
                for i, a in enumerate(fac_articles):
                    ref     = int(a.get("ref") or 0)
                    pv      = float(a.get("pv") or 0)
                    or_grs  = float(a.get("or_grs") or 0)
                    pa      = float(a.get("pa") or 0)
                    # Déduire du stock si l'article provient du stock (ref connue)
                    if ref > 0:
                        sidx = next((j for j, s in enumerate(articles_stock) if s.get("id") == ref), None)
                        if sidx is not None:
                            sart = articles_stock[sidx]
                            if is_poids_article(sart):
                                # Vente au poids : déduire du stock
                                stock_w = float(sart.get("or_grs") or 0)
                                qty_w = int(sart.get("quantite") or 1)
                                if pa <= 0:
                                    pa = round(or_grs * prix_or_achat, 2)
                                if qty_w > 1:
                                    # Plusieurs pièces identiques : on en retire UNE
                                    # (le poids enregistré est celui d'une pièce)
                                    articles_stock[sidx]["quantite"] = qty_w - 1
                                else:
                                    nouveau = round(stock_w - or_grs, 3)
                                    if nouveau <= 0.001:
                                        articles_stock.pop(sidx)
                                    else:
                                        articles_stock[sidx]["or_grs"] = nouveau
                                        articles_stock[sidx]["pa"] = round(nouveau * prix_or_achat, 2)
                            else:
                                # Article unitaire : décrémenter la quantité
                                if pa <= 0:
                                    pa = float(sart.get("pa") or 0)
                                qty = int(sart.get("quantite") or 1)
                                if qty > 1:
                                    articles_stock[sidx]["quantite"] = qty - 1
                                else:
                                    articles_stock.pop(sidx)
                            stock_touche = True
                    benef = round(pv - pa, 2)
                    new_ventes.append({
                        "id_vente": int(now.timestamp() * 1000) + i,
                        "date_achat": date_vente, "date_vente": date_vente,
                        "ref": ref,
                        "article": str(a.get("article") or "Article"),
                        "or_grs": or_grs or None,
                        "vente_au_poids": False, "prix_or_achat": None,
                        "pa": pa or None,
                        "d":  float(a["d"])  if a.get("d")  else None,
                        "em": float(a["em"]) if a.get("em") else None,
                        "r":  float(a["r"])  if a.get("r")  else None,
                        "s":  float(a["s"])  if a.get("s")  else None,
                        "p_fines": None, "rosaces": None,
                        "em_clb": float(a["em_clb"]) if a.get("em_clb") else None,
                        "perles": float(a["perles"]) if a.get("perles") else None,
                        "pv": pv, "benef": benef,
                        "client": client_nom,
                        "telephone": facture["telephone"] or "",
                        "mode_paiement": facture["mode_paiement"] or "",
                        "commentaire": facture["note"],
                        "source": "libre",
                        "type_vente": "produit",
                        "offert": bool(a.get("offert")),
                    })
                ventes.extend(new_ventes)
                save_ventes(ventes)
                if stock_touche:
                    save_articles(articles_stock)
                facture["vente_validee"] = True
                r_, ip_, dev_ = self._actor()
                db.log_audit("created", "vente", "facture",
                             f"Facture libre {numero} — {client_nom} · {len(new_ventes)} article(s) · {total_val} MAD",
                             r_, ip_, dev_)

            factures.append(facture)
            save_factures(factures)
            self.send_json({"success": True, "facture": facture}); return

        # ── Valider une facture libre comme vente ───────────────────────
        if path.startswith("/api/factures/") and path.endswith("/valider"):
            try:
                fac_id = int(path.split("/")[-2])
                factures = load_factures()
                idx = next((i for i, f in enumerate(factures) if f["id"] == fac_id), None)
                if idx is None:
                    self.send_json({"error": "Facture introuvable"}, 404); return
                fac = factures[idx]
                if fac.get("vente_validee"):
                    self.send_json({"error": "Déjà validée"}); return
                # Créer les ventes
                ventes = load_ventes()
                now2 = datetime.now()
                client = fac.get("client", "")
                date_v = fac.get("date") or now2.strftime("%Y-%m-%d")
                new_ventes = []
                for i, a in enumerate(fac.get("articles", [])):
                    pv  = float(a.get("pv") or 0)
                    pa  = float(a.get("pa") or 0)
                    new_ventes.append({
                        "id_vente": int(now2.timestamp() * 1000) + i,
                        "date_achat": date_v, "date_vente": date_v,
                        "ref": 0,
                        "article": str(a.get("article") or "—"),
                        "or_grs": float(a.get("or_grs") or 0) or None,
                        "vente_au_poids": False, "prix_or_achat": None,
                        "pa": pa or None,
                        "d":  float(a["d"])  if a.get("d")  else None,
                        "em": float(a["em"]) if a.get("em") else None,
                        "r":  float(a["r"])  if a.get("r")  else None,
                        "s":  float(a["s"])  if a.get("s")  else None,
                        "p_fines": None, "rosaces": None,
                        "em_clb": float(a["em_clb"]) if a.get("em_clb") else None,
                        "perles": float(a["perles"]) if a.get("perles") else None,
                        "pv": pv, "benef": round(pv - pa, 2),
                        "client": client,
                        "telephone": fac.get("telephone") or "",
                        "mode_paiement": fac.get("mode_paiement") or "",
                        "commentaire": fac.get("note") or "",
                        "source": "libre",
                    })
                ventes.extend(new_ventes)
                save_ventes(ventes)
                # Marquer la facture comme validée
                factures[idx]["vente_validee"] = True
                save_factures(factures)
                self.send_json({"success": True, "ventes_creees": len(new_ventes)}); return
            except Exception as e:
                self.send_json({"error": str(e)}, 400); return

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

        # ── Enregistrer un devis ─────────────────────────────────────────────
        if path == "/api/devis":
            client = str(data.get("client","")).strip()
            if not client:
                self.send_json({"error": "Client requis"}, 400); return
            now = datetime.now()
            item = {
                "client":        client,
                "telephone":     str(data.get("telephone","")).strip(),
                "date_devis":    str(data.get("date_devis") or now.strftime("%Y-%m-%d")),
                "articles":      data.get("articles", []),
                "total_initial": float(data.get("total_initial") or 0),
                "total_reduit":  float(data.get("total_reduit") or 0),
                "note":          str(data.get("note","")).strip(),
                "created_at":    now.strftime("%Y-%m-%d %H:%M:%S"),
            }
            devis_id = insert_devis(item)
            # Support restauration : si statut=vendu est passé, marquer directement
            statut = str(data.get("statut","")).strip()
            if statut == "vendu":
                dv = str(data.get("date_vente") or now.strftime("%Y-%m-%d"))
                db.mark_devis_vendu(devis_id, dv)
            self.send_json({"success": True, "id": devis_id}); return

        # ── Convertir un devis en vente(s) ───────────────────────────────────
        if path.startswith("/api/devis/") and path.endswith("/vendre"):
            try:
                parts = path.split("/")
                devis_id = int(parts[-2])  # /api/devis/{id}/vendre
            except (ValueError, IndexError):
                self.send_json({"error": "ID invalide"}, 400); return

            # Charger le devis
            all_devis = load_devis()
            devis = next((d for d in all_devis if d["id"] == devis_id), None)
            if not devis:
                self.send_json({"error": "Devis introuvable"}, 404); return

            mode    = str(data.get("mode_paiement", "Espèces")).strip()
            delete_after = bool(data.get("delete_after", True))
            date_vente_input = str(data.get("date_vente", "")).strip()
            refs_manuelles = data.get("refs_manuelles") or []
            now = datetime.now()
            try:
                datetime.strptime(date_vente_input, "%Y-%m-%d")
                date_vente = date_vente_input
            except Exception:
                date_vente = now.strftime("%Y-%m-%d")

            client  = devis.get("client", "")
            tel     = devis.get("telephone", "")

            import copy
            devis_articles = copy.deepcopy(devis.get("articles") or [])
            manuel_idx = 0
            for art in devis_articles:
                if not (art.get("refs") and len(art.get("refs", [])) > 0):
                    if manuel_idx < len(refs_manuelles) and refs_manuelles[manuel_idx]:
                        val = refs_manuelles[manuel_idx]
                        # val peut être un entier ou une liste d'entiers
                        if isinstance(val, list):
                            art["refs"] = [int(r) for r in val if r]
                        else:
                            art["refs"] = [int(val)]
                    manuel_idx += 1

            articles_stock = load_articles()
            ventes_existantes = load_ventes()
            new_ventes = []
            stock_count = 0
            libre_count = 0
            vente_counter = 0

            for art in devis_articles:
                refs      = art.get("refs") or []
                pv_art    = float(art.get("pvr") or 0) if (art.get("pvr") or 0) > 0 else float(art.get("pv") or 0)
                pa_art    = float(art.get("pa") or 0)
                or_grs    = art.get("or_grs") or None
                d_val     = art.get("d") or None
                em_val    = art.get("em") or None
                r_val     = art.get("r") or None
                s_val     = art.get("s") or None
                em_clb    = art.get("em_clb") or None
                perles    = art.get("perles") or None
                art_name  = art.get("article", "")

                if refs:
                    # Ventes avec références stock
                    pv_per_ref = round(pv_art / len(refs), 2) if len(refs) > 1 else pv_art
                    for ref_id in refs:
                        idx = next((i for i, a in enumerate(articles_stock) if a["id"] == int(ref_id)), None)
                        if idx is None:
                            # Ref introuvable en stock → créer une vente libre
                            id_vente = int(now.timestamp() * 1000) + vente_counter
                            vente_counter += 1
                            new_ventes.append({
                                "id_vente": id_vente,
                                "date_achat": date_vente,
                                "date_vente": date_vente,
                                "ref": int(ref_id),
                                "article": art_name,
                                "or_grs": or_grs,
                                "vente_au_poids": False,
                                "prix_or_achat": None,
                                "pa": pa_art or None,
                                "d": d_val, "em": em_val, "r": r_val, "s": s_val,
                                "p_fines": None, "rosaces": None,
                                "em_clb": em_clb, "perles": perles,
                                "pv": pv_per_ref,
                                "benef": round(pv_per_ref - (pa_art or 0), 2),
                                "client": client,
                                "telephone": tel,
                                "mode_paiement": mode,
                                "commentaire": "",
                                "source": "devis",
                            })
                            libre_count += 1
                            continue
                        article = articles_stock[idx]
                        pa_stock = article.get("pa") or 0
                        id_vente = int(now.timestamp() * 1000) + vente_counter
                        vente_counter += 1
                        new_ventes.append({
                            "id_vente": id_vente,
                            "date_achat": article.get("date"),
                            "date_vente": date_vente,
                            "ref": article["id"],
                            "article": art_name or article.get("article", ""),
                            "or_grs": article.get("or_grs"),
                            "vente_au_poids": False,
                            "prix_or_achat": None,
                            "pa": pa_stock,
                            "d": article.get("d"), "em": article.get("em"),
                            "r": article.get("r"), "s": article.get("s"),
                            "p_fines": article.get("p_fines"), "rosaces": article.get("rosaces"),
                            "em_clb": article.get("em_clb"), "perles": article.get("perles"),
                            "pv": pv_per_ref,
                            "benef": round(pv_per_ref - pa_stock, 2),
                            "client": client,
                            "telephone": tel,
                            "mode_paiement": mode,
                            "commentaire": "",
                            "source": "devis",
                        })
                        # Retirer du stock
                        qty = int(articles_stock[idx].get("quantite") or 1)
                        if qty > 1:
                            articles_stock[idx]["quantite"] = qty - 1
                        else:
                            articles_stock.pop(idx)
                        stock_count += 1
                else:
                    # Vente libre (pas de référence stock)
                    id_vente = int(now.timestamp() * 1000) + vente_counter
                    vente_counter += 1
                    new_ventes.append({
                        "id_vente": id_vente,
                        "date_achat": date_vente,
                        "date_vente": date_vente,
                        "ref": 0,
                        "article": art_name,
                        "or_grs": or_grs,
                        "vente_au_poids": False,
                        "prix_or_achat": None,
                        "pa": pa_art or None,
                        "d": d_val, "em": em_val, "r": r_val, "s": s_val,
                        "p_fines": None, "rosaces": None,
                        "em_clb": em_clb, "perles": perles,
                        "pv": pv_art,
                        "benef": round(pv_art - (pa_art or 0), 2),
                        "client": client,
                        "telephone": tel,
                        "mode_paiement": mode,
                        "commentaire": "",
                        "source": "devis",
                    })
                    libre_count += 1

            # Sauvegarder stock + ventes
            save_articles(articles_stock)
            all_ventes = ventes_existantes + new_ventes
            save_ventes(all_ventes)

            # Marquer le devis comme vendu (toujours), supprimer si demandé
            db.mark_devis_vendu(devis_id, date_vente)
            if delete_after:
                delete_devis(devis_id)

            self.send_json({
                "success": True,
                "ventes_crees": len(new_ventes),
                "stock_articles": stock_count,
                "libres": libre_count,
            }); return

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
        with _WRITE_LOCK:
            self._handle_PUT()

    def _handle_PUT(self):
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
                if "pa" in data and data["pa"] not in (None, ""):
                    v["pa"] = float(data["pa"])
                if "pv" in data and data["pv"] not in (None, ""):
                    v["pv"] = float(data["pv"])
                v["benef"] = round((v.get("pv") or 0) - (v.get("pa") or 0), 2)
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
                # Remplacer la liste des paiements (ex: [] pour annuler un solde/avance)
                if "paiements" in data and isinstance(data["paiements"], list):
                    c["paiements"] = data["paiements"]
                    c["date_solde"] = None
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

        # ── Modifier une facture (prix global, avance) ────────────────────────
        if path.startswith("/api/factures/"):
            try:
                id_facture = int(path.split("/")[-1])
                factures = load_factures()
                idx = next((i for i, f in enumerate(factures) if f["id"] == id_facture), None)
                if idx is None:
                    self.send_json({"error": "Facture introuvable"}, 404); return
                f = factures[idx]
                if "prix_global" in data:
                    f["prix_global"] = int(bool(data["prix_global"]))
                if "total_global" in data and data["total_global"] not in (None, ""):
                    f["total_global"] = float(data["total_global"])
                if "avance" in data and data["avance"] not in (None, ""):
                    f["avance"] = float(data["avance"])
                factures[idx] = f
                save_factures(factures)
                self.send_json({"success": True, "facture": f}); return
            except Exception as e:
                self.send_json({"error": str(e)}, 400); return

        self.send_json({"error": "Route inconnue"}, 404)

    def do_DELETE(self):
        with _WRITE_LOCK:
            self._handle_DELETE()

    def _handle_DELETE(self):
        path = urllib.parse.urlparse(self.path).path

        # ── Supprimer un article du stock ─────────────────────────────────────
        if path.startswith("/api/articles/"):
            try:
                ref = int(path.split("/")[-1])
                articles = load_articles()
                idx = next((i for i, a in enumerate(articles) if a["id"] == ref), None)
                if idx is None:
                    self.send_json({"error": "Article introuvable"}, 404); return
                removed = articles.pop(idx)
                save_articles(articles)
                r_, ip_, dev_ = self._actor()
                db.log_audit("deleted", "article", ref,
                             f"Article #{ref} — {removed.get('article','')}",
                             r_, ip_, dev_, snapshot=removed)
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
                removed = ventes.pop(idx)
                save_ventes(ventes)
                r_, ip_, dev_ = self._actor()
                db.log_audit("deleted", "vente", removed.get("ref", id_vente),
                             f"Vente #{removed.get('ref','')} — {removed.get('article','')} · "
                             f"{removed.get('client','?')} · {removed.get('pv',0)} MAD",
                             r_, ip_, dev_, snapshot=removed)
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
                removed = credits.pop(idx)
                save_credits(credits)
                r_, ip_, dev_ = self._actor()
                db.log_audit("deleted", "credit", id_credit,
                             f"Crédit {removed.get('client','?')} — {removed.get('montant_total',0)} MAD",
                             r_, ip_, dev_, snapshot=removed)
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
                removed = fournisseurs.pop(idx)
                save_fournisseurs(fournisseurs)
                r_, ip_, dev_ = self._actor()
                db.log_audit("deleted", "fournisseur", id_f,
                             f"Fournisseur {removed.get('nom') or removed.get('client') or id_f}",
                             r_, ip_, dev_, snapshot=removed)
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
                removed = cheques.pop(idx)
                save_cheques(cheques)
                r_, ip_, dev_ = self._actor()
                db.log_audit("deleted", "cheque", id_cheque,
                             f"Chèque {removed.get('client','?')} — {removed.get('montant',0)} MAD",
                             r_, ip_, dev_, snapshot=removed)
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

        # ── Supprimer un devis ────────────────────────────────────────────────
        if path.startswith("/api/devis/"):
            try:
                devis_id = int(path.split("/")[-1])
                delete_devis(devis_id)
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
    # Sync OneDrive en arrière-plan (sauvegarde locale)
    import subprocess as _sp, pathlib as _pl
    _sync = _pl.Path(__file__).parent / "sync_onedrive.sh"
    if _sync.exists():
        _sp.Popen(["bash", str(_sync)], stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)

    # Synchroniser la DB depuis R2 avant tout (source de vérité unique)
    db.DATA_DIR.mkdir(parents=True, exist_ok=True)
    download_db_from_r2()
    # Initialiser la base de données SQLite (migration JSON → SQLite au premier lancement)
    db.init_db()
    # 0. Corriger les reprises enregistrées avec l'ancienne logique
    migrate_reprise_stock()
    # 0b. Chaînes : remplacer l'ancien lot vrac par les 4 lots couleur (une fois)
    migrate_chain_lots()
    # 1. Fusionner les factures dupliquées (même client + même jour → une seule)
    merge_duplicate_factures()
    # 2. Générer les factures manquantes pour les ventes qui n'en ont pas
    merge_duplicate_factures(); auto_generate_missing_factures()

    is_cloud = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RENDER"))

    print("=" * 50)
    print("  GESTION DE STOCK — Joaillerie")
    print("=" * 50)
    print(f"  Serveur : http://localhost:{PORT}")
    print(f"  Mode    : {'☁️  Cloud' if is_cloud else '💻 Local'}")
    if not is_cloud:
        print(f"  Appuie sur Ctrl+C pour arrêter")
    print("=" * 50)
    if MOT_DE_PASSE_ADMIN in ("7868", "1234", "0000", "admin", ""):
        print("⚠️  SÉCURITÉ : Mot de passe admin trop simple !")
        print("    → Changez MOT_DE_PASSE_ADMIN dans Railway Variables")
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
