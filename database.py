"""
database.py — Couche SQLite pour GestionStock
Remplace le stockage JSON par une vraie base de données SQLite.
Migration automatique des JSON existants au premier lancement.
"""

import sqlite3
import json
import shutil
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager

BASE_DIR = Path(__file__).parent
DATA_DIR  = BASE_DIR / "data"
DB_FILE   = DATA_DIR / "gestionstock.db"

# Fichiers JSON d'origine (pour migration)
ARTICLES_FILE    = DATA_DIR / "articles.json"
VENTES_FILE      = DATA_DIR / "ventes.json"
CREDITS_FILE     = DATA_DIR / "credits.json"
FOURNISSEURS_FILE= DATA_DIR / "fournisseurs.json"
CHEQUES_FILE     = DATA_DIR / "cheques.json"
FACTURES_FILE    = DATA_DIR / "factures.json"
CONFIG_FILE      = DATA_DIR / "config.json"
NOTIFS_FILE      = DATA_DIR / "notifs.json"


# ─── Connexion ────────────────────────────────────────────────────────────────

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # écriture concurrente plus sûre
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─── Initialisation des tables ────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id            INTEGER PRIMARY KEY,
    date          TEXT,
    article       TEXT,
    or_grs        REAL,
    pa            REAL,
    d             REAL,
    em            REAL,
    r             REAL,
    s             REAL,
    p_fines       REAL,
    rosaces       REAL,
    em_clb        REAL,
    perles        REAL,
    fabricant     TEXT,
    ismail_pierres INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ventes (
    id_vente       INTEGER PRIMARY KEY,
    date_achat     TEXT,
    date_vente     TEXT,
    ref            INTEGER,
    article        TEXT,
    or_grs         REAL,
    vente_au_poids INTEGER DEFAULT 0,
    prix_or_achat  REAL,
    pa             REAL,
    d              REAL,
    em             REAL,
    r              REAL,
    s              REAL,
    p_fines        REAL,
    rosaces        REAL,
    em_clb         REAL,
    perles         REAL,
    pv             REAL,
    benef          REAL,
    client         TEXT,
    mode_paiement  TEXT,
    commentaire    TEXT
);

CREATE TABLE IF NOT EXISTS credits (
    id            INTEGER PRIMARY KEY,
    client        TEXT,
    contact       TEXT,
    date_achat    TEXT,
    refs          TEXT,
    article       TEXT,
    montant_total REAL,
    paiements     TEXT DEFAULT '[]',
    reste         REAL,
    statut        TEXT DEFAULT 'rien',
    date_solde    TEXT,
    note          TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS fournisseurs (
    id            INTEGER PRIMARY KEY,
    fournisseur   TEXT,
    contact       TEXT,
    date_commande TEXT,
    num_commande  TEXT,
    article       TEXT,
    montant_total REAL,
    paiements     TEXT DEFAULT '[]',
    reste         REAL,
    statut        TEXT DEFAULT 'rien',
    date_solde    TEXT,
    note          TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS cheques (
    id                  INTEGER PRIMARY KEY,
    client              TEXT,
    ref_article         TEXT,
    montant             REAL,
    numero              TEXT,
    nb_cheques          INTEGER DEFAULT 1,
    banque              TEXT,
    date_cheque         TEXT,
    date_encaissement   TEXT,
    dates_encaissement  TEXT DEFAULT '[]',
    numeros_cheques     TEXT DEFAULT '[]',
    statuts_cheques     TEXT DEFAULT '[]',
    statut              TEXT DEFAULT 'en_attente',
    credit_id           INTEGER,
    note                TEXT DEFAULT '',
    created_at          TEXT
);

CREATE TABLE IF NOT EXISTS factures (
    id             INTEGER PRIMARY KEY,
    numero         TEXT,
    client         TEXT,
    telephone      TEXT,
    email          TEXT,
    ville          TEXT,
    articles       TEXT DEFAULT '[]',
    total          REAL,
    avance         REAL DEFAULT 0,
    mode_paiement  TEXT,
    note           TEXT DEFAULT '',
    date           TEXT,
    created_at     TEXT
);

CREATE TABLE IF NOT EXISTS notifs (
    id        INTEGER PRIMARY KEY,
    type      TEXT,
    date      TEXT,
    ref       INTEGER,
    article   TEXT,
    client    TEXT,
    dismissed INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value REAL
);
"""


def _json_load(path, default):
    if not Path(path).exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _migrate_json(conn):
    """Importe les données JSON existantes dans SQLite (one-time)."""
    print("[DB] Migration JSON → SQLite...")

    # Articles (gestion des IDs dupliqués : nouvel ID unique pour les doublons)
    arts = _json_load(ARTICLES_FILE, [])
    seen_ids = set()
    max_id = max((a.get("id", 0) for a in arts), default=0)
    inserted = 0
    for a in arts:
        aid = a.get("id")
        if aid in seen_ids:
            max_id += 1
            aid = max_id
        seen_ids.add(aid)
        conn.execute("""
            INSERT OR IGNORE INTO articles
            (id,date,article,or_grs,pa,d,em,r,s,p_fines,rosaces,em_clb,perles,fabricant,ismail_pierres)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (aid, a.get("date"), a.get("article"),
              a.get("or_grs"), a.get("pa"), a.get("d"), a.get("em"),
              a.get("r"), a.get("s"), a.get("p_fines"), a.get("rosaces"),
              a.get("em_clb"), a.get("perles"), a.get("fabricant"),
              1 if a.get("ismail_pierres") else 0))
        inserted += 1
    print(f"  articles : {inserted} (dont {inserted - len(set(a.get('id') for a in arts))} doublons résolus)")

    # Ventes
    ventes = _json_load(VENTES_FILE, [])
    for v in ventes:
        conn.execute("""
            INSERT OR IGNORE INTO ventes
            (id_vente,date_achat,date_vente,ref,article,or_grs,vente_au_poids,
             prix_or_achat,pa,d,em,r,s,p_fines,rosaces,em_clb,perles,
             pv,benef,client,mode_paiement,commentaire)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (v.get("id_vente"), v.get("date_achat"), v.get("date_vente"),
              v.get("ref"), v.get("article"), v.get("or_grs"),
              1 if v.get("vente_au_poids") else 0,
              v.get("prix_or_achat"), v.get("pa"),
              v.get("d"), v.get("em"), v.get("r"), v.get("s"),
              v.get("p_fines"), v.get("rosaces"), v.get("em_clb"), v.get("perles"),
              v.get("pv"), v.get("benef"), v.get("client"),
              v.get("mode_paiement"), v.get("commentaire")))
    print(f"  ventes   : {len(ventes)}")

    # Crédits
    credits = _json_load(CREDITS_FILE, [])
    for c in credits:
        conn.execute("""
            INSERT OR IGNORE INTO credits
            (id,client,contact,date_achat,refs,article,montant_total,
             paiements,reste,statut,date_solde,note)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (c.get("id"), c.get("client"), c.get("contact"),
              c.get("date_achat"), c.get("refs"), c.get("article"),
              c.get("montant_total"), json.dumps(c.get("paiements", []), ensure_ascii=False),
              c.get("reste"), c.get("statut","rien"),
              c.get("date_solde"), c.get("note","")))
    print(f"  credits  : {len(credits)}")

    # Fournisseurs
    fours = _json_load(FOURNISSEURS_FILE, [])
    for f in fours:
        conn.execute("""
            INSERT OR IGNORE INTO fournisseurs
            (id,fournisseur,contact,date_commande,num_commande,article,montant_total,
             paiements,reste,statut,date_solde,note)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (f.get("id"), f.get("fournisseur"), f.get("contact"),
              f.get("date_commande"), f.get("num_commande"), f.get("article"),
              f.get("montant_total"), json.dumps(f.get("paiements", []), ensure_ascii=False),
              f.get("reste"), f.get("statut","rien"),
              f.get("date_solde"), f.get("note","")))
    print(f"  fournisseurs: {len(fours)}")

    # Chèques
    cheques = _json_load(CHEQUES_FILE, [])
    for ch in cheques:
        conn.execute("""
            INSERT OR IGNORE INTO cheques
            (id,client,ref_article,montant,numero,nb_cheques,banque,
             date_cheque,date_encaissement,dates_encaissement,
             numeros_cheques,statuts_cheques,statut,credit_id,note,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (ch.get("id"), ch.get("client"), ch.get("ref_article"),
              ch.get("montant"), ch.get("numero"), ch.get("nb_cheques",1),
              ch.get("banque"), ch.get("date_cheque"), ch.get("date_encaissement"),
              json.dumps(ch.get("dates_encaissement",[]), ensure_ascii=False),
              json.dumps(ch.get("numeros_cheques",[]), ensure_ascii=False),
              json.dumps(ch.get("statuts_cheques",[]), ensure_ascii=False),
              ch.get("statut","en_attente"), ch.get("credit_id"),
              ch.get("note",""), ch.get("created_at")))
    print(f"  cheques  : {len(cheques)}")

    # Factures
    factures = _json_load(FACTURES_FILE, [])
    for fac in factures:
        conn.execute("""
            INSERT OR IGNORE INTO factures
            (id,numero,client,telephone,email,ville,articles,
             total,avance,mode_paiement,note,date,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (fac.get("id"), fac.get("numero"), fac.get("client"),
              fac.get("telephone"), fac.get("email"), fac.get("ville"),
              json.dumps(fac.get("articles",[]), ensure_ascii=False),
              fac.get("total"), fac.get("avance",0),
              fac.get("mode_paiement"), fac.get("note",""),
              fac.get("date"), fac.get("created_at")))
    print(f"  factures : {len(factures)}")

    # Notifications
    notifs = _json_load(NOTIFS_FILE, [])
    for n in notifs:
        conn.execute("""
            INSERT OR IGNORE INTO notifs
            (id,type,date,ref,article,client,dismissed)
            VALUES (?,?,?,?,?,?,?)
        """, (n.get("id"), n.get("type"), n.get("date"),
              n.get("ref"), n.get("article"), n.get("client"),
              1 if n.get("dismissed") else 0))
    print(f"  notifs   : {len(notifs)}")

    # Config
    cfg = _json_load(CONFIG_FILE, {"prix_or_achat": 1000, "prix_or_vente": 1100})
    for k, v in cfg.items():
        conn.execute("INSERT OR IGNORE INTO config (key,value) VALUES (?,?)", (k, v))
    print(f"  config   : {len(cfg)} clés")

    print("[DB] Migration terminée.")


def init_db():
    """Crée la base de données et migre les JSON si c'est la première fois."""
    DATA_DIR.mkdir(exist_ok=True)
    first_run = not DB_FILE.exists()
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        if first_run:
            _migrate_json(conn)


# ─── ARTICLES ─────────────────────────────────────────────────────────────────

def _row_to_article(row):
    return {
        "id": row["id"], "date": row["date"], "article": row["article"],
        "or_grs": row["or_grs"], "pa": row["pa"],
        "d": row["d"], "em": row["em"], "r": row["r"], "s": row["s"],
        "p_fines": row["p_fines"], "rosaces": row["rosaces"],
        "em_clb": row["em_clb"], "perles": row["perles"],
        "fabricant": row["fabricant"],
        "ismail_pierres": bool(row["ismail_pierres"]),
    }

def load_articles():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM articles ORDER BY id").fetchall()
    return [_row_to_article(r) for r in rows]

def save_articles(articles):
    """Remplace tous les articles (compatibilité avec l'ancienne interface)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM articles")
        conn.executemany("""
            INSERT INTO articles
            (id,date,article,or_grs,pa,d,em,r,s,p_fines,rosaces,em_clb,perles,fabricant,ismail_pierres)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, [(a["id"], a.get("date"), a.get("article"),
               a.get("or_grs"), a.get("pa"), a.get("d"), a.get("em"),
               a.get("r"), a.get("s"), a.get("p_fines"), a.get("rosaces"),
               a.get("em_clb"), a.get("perles"), a.get("fabricant"),
               1 if a.get("ismail_pierres") else 0) for a in articles])


# ─── VENTES ───────────────────────────────────────────────────────────────────

def _row_to_vente(row):
    return {
        "id_vente": row["id_vente"], "date_achat": row["date_achat"],
        "date_vente": row["date_vente"], "ref": row["ref"],
        "article": row["article"], "or_grs": row["or_grs"],
        "vente_au_poids": bool(row["vente_au_poids"]),
        "prix_or_achat": row["prix_or_achat"],
        "pa": row["pa"], "d": row["d"], "em": row["em"],
        "r": row["r"], "s": row["s"], "p_fines": row["p_fines"],
        "rosaces": row["rosaces"], "em_clb": row["em_clb"], "perles": row["perles"],
        "pv": row["pv"], "benef": row["benef"], "client": row["client"],
        "mode_paiement": row["mode_paiement"], "commentaire": row["commentaire"],
    }

def load_ventes():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM ventes ORDER BY id_vente").fetchall()
    return [_row_to_vente(r) for r in rows]

def save_ventes(ventes):
    with get_conn() as conn:
        conn.execute("DELETE FROM ventes")
        conn.executemany("""
            INSERT INTO ventes
            (id_vente,date_achat,date_vente,ref,article,or_grs,vente_au_poids,
             prix_or_achat,pa,d,em,r,s,p_fines,rosaces,em_clb,perles,
             pv,benef,client,mode_paiement,commentaire)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, [(v["id_vente"], v.get("date_achat"), v.get("date_vente"),
               v.get("ref"), v.get("article"), v.get("or_grs"),
               1 if v.get("vente_au_poids") else 0, v.get("prix_or_achat"),
               v.get("pa"), v.get("d"), v.get("em"), v.get("r"), v.get("s"),
               v.get("p_fines"), v.get("rosaces"), v.get("em_clb"), v.get("perles"),
               v.get("pv"), v.get("benef"), v.get("client"),
               v.get("mode_paiement"), v.get("commentaire")) for v in ventes])


# ─── CRÉDITS ──────────────────────────────────────────────────────────────────

def _row_to_credit(row):
    return {
        "id": row["id"], "client": row["client"], "contact": row["contact"],
        "date_achat": row["date_achat"], "refs": row["refs"],
        "article": row["article"], "montant_total": row["montant_total"],
        "paiements": json.loads(row["paiements"] or "[]"),
        "reste": row["reste"], "statut": row["statut"],
        "date_solde": row["date_solde"], "note": row["note"] or "",
    }

def load_credits():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM credits ORDER BY id").fetchall()
    return [_row_to_credit(r) for r in rows]

def save_credits(credits):
    with get_conn() as conn:
        conn.execute("DELETE FROM credits")
        conn.executemany("""
            INSERT INTO credits
            (id,client,contact,date_achat,refs,article,montant_total,
             paiements,reste,statut,date_solde,note)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, [(c["id"], c.get("client"), c.get("contact"),
               c.get("date_achat"), c.get("refs"), c.get("article"),
               c.get("montant_total"),
               json.dumps(c.get("paiements",[]), ensure_ascii=False),
               c.get("reste"), c.get("statut","rien"),
               c.get("date_solde"), c.get("note","")) for c in credits])


# ─── FOURNISSEURS ─────────────────────────────────────────────────────────────

def _row_to_fournisseur(row):
    return {
        "id": row["id"], "fournisseur": row["fournisseur"],
        "contact": row["contact"], "date_commande": row["date_commande"],
        "num_commande": row["num_commande"], "article": row["article"],
        "montant_total": row["montant_total"],
        "paiements": json.loads(row["paiements"] or "[]"),
        "reste": row["reste"], "statut": row["statut"],
        "date_solde": row["date_solde"], "note": row["note"] or "",
    }

def load_fournisseurs():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM fournisseurs ORDER BY id").fetchall()
    return [_row_to_fournisseur(r) for r in rows]

def save_fournisseurs(fournisseurs):
    with get_conn() as conn:
        conn.execute("DELETE FROM fournisseurs")
        conn.executemany("""
            INSERT INTO fournisseurs
            (id,fournisseur,contact,date_commande,num_commande,article,montant_total,
             paiements,reste,statut,date_solde,note)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, [(f["id"], f.get("fournisseur"), f.get("contact"),
               f.get("date_commande"), f.get("num_commande"), f.get("article"),
               f.get("montant_total"),
               json.dumps(f.get("paiements",[]), ensure_ascii=False),
               f.get("reste"), f.get("statut","rien"),
               f.get("date_solde"), f.get("note","")) for f in fournisseurs])


# ─── CHÈQUES ──────────────────────────────────────────────────────────────────

def _row_to_cheque(row):
    return {
        "id": row["id"], "client": row["client"],
        "ref_article": row["ref_article"], "montant": row["montant"],
        "numero": row["numero"], "nb_cheques": row["nb_cheques"],
        "banque": row["banque"], "date_cheque": row["date_cheque"],
        "date_encaissement": row["date_encaissement"],
        "dates_encaissement": json.loads(row["dates_encaissement"] or "[]"),
        "numeros_cheques": json.loads(row["numeros_cheques"] or "[]"),
        "statuts_cheques": json.loads(row["statuts_cheques"] or "[]"),
        "statut": row["statut"], "credit_id": row["credit_id"],
        "note": row["note"] or "", "created_at": row["created_at"],
    }

def load_cheques():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM cheques ORDER BY id").fetchall()
    return [_row_to_cheque(r) for r in rows]

def save_cheques(cheques):
    with get_conn() as conn:
        conn.execute("DELETE FROM cheques")
        conn.executemany("""
            INSERT INTO cheques
            (id,client,ref_article,montant,numero,nb_cheques,banque,
             date_cheque,date_encaissement,dates_encaissement,
             numeros_cheques,statuts_cheques,statut,credit_id,note,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, [(ch["id"], ch.get("client"), ch.get("ref_article"),
               ch.get("montant"), ch.get("numero"), ch.get("nb_cheques",1),
               ch.get("banque"), ch.get("date_cheque"), ch.get("date_encaissement"),
               json.dumps(ch.get("dates_encaissement",[]), ensure_ascii=False),
               json.dumps(ch.get("numeros_cheques",[]), ensure_ascii=False),
               json.dumps(ch.get("statuts_cheques",[]), ensure_ascii=False),
               ch.get("statut","en_attente"), ch.get("credit_id"),
               ch.get("note",""), ch.get("created_at")) for ch in cheques])


# ─── FACTURES ─────────────────────────────────────────────────────────────────

def _row_to_facture(row):
    return {
        "id": row["id"], "numero": row["numero"], "client": row["client"],
        "telephone": row["telephone"], "email": row["email"],
        "ville": row["ville"],
        "articles": json.loads(row["articles"] or "[]"),
        "total": row["total"], "avance": row["avance"] or 0,
        "mode_paiement": row["mode_paiement"],
        "note": row["note"] or "", "date": row["date"],
        "created_at": row["created_at"],
    }

def load_factures():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM factures ORDER BY id").fetchall()
    return [_row_to_facture(r) for r in rows]

def save_factures(factures):
    with get_conn() as conn:
        conn.execute("DELETE FROM factures")
        conn.executemany("""
            INSERT INTO factures
            (id,numero,client,telephone,email,ville,articles,
             total,avance,mode_paiement,note,date,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, [(fac["id"], fac.get("numero"), fac.get("client"),
               fac.get("telephone"), fac.get("email"), fac.get("ville"),
               json.dumps(fac.get("articles",[]), ensure_ascii=False),
               fac.get("total"), fac.get("avance",0),
               fac.get("mode_paiement"), fac.get("note",""),
               fac.get("date"), fac.get("created_at")) for fac in factures])


# ─── NOTIFICATIONS ────────────────────────────────────────────────────────────

def _row_to_notif(row):
    return {
        "id": row["id"], "type": row["type"], "date": row["date"],
        "ref": row["ref"], "article": row["article"], "client": row["client"],
        "dismissed": bool(row["dismissed"]),
    }

def load_notifs():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM notifs ORDER BY id").fetchall()
    return [_row_to_notif(r) for r in rows]

def save_notifs(notifs):
    with get_conn() as conn:
        conn.execute("DELETE FROM notifs")
        conn.executemany("""
            INSERT INTO notifs (id,type,date,ref,article,client,dismissed)
            VALUES (?,?,?,?,?,?,?)
        """, [(n["id"], n.get("type"), n.get("date"),
               n.get("ref"), n.get("article"), n.get("client"),
               1 if n.get("dismissed") else 0) for n in notifs])


# ─── CONFIG ───────────────────────────────────────────────────────────────────

def load_config():
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM config").fetchall()
    cfg = {r["key"]: r["value"] for r in rows}
    if not cfg:
        return {"prix_or_achat": 1000, "prix_or_vente": 1100}
    return cfg

def save_config(cfg):
    with get_conn() as conn:
        for k, v in cfg.items():
            conn.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (k, v)
            )


# ─── Migration one-shot depuis JSON (seed Railway) ────────────────────────────

def seed_all(payload):
    """Importe toutes les données depuis un payload JSON (migration vers Railway)."""
    with get_conn() as conn:
        # Vider les tables existantes
        for table in ("articles","ventes","credits","fournisseurs","cheques","factures","notifs","config"):
            conn.execute(f"DELETE FROM {table}")

        for a in payload.get("articles", []):
            conn.execute("""INSERT OR REPLACE INTO articles
                (id,date,article,or_grs,pa,d,em,r,s,p_fines,rosaces,em_clb,perles,fabricant,ismail_pierres)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                a.get("id"), a.get("date"), a.get("article"), a.get("or_grs"),
                a.get("pa"), a.get("d"), a.get("em"), a.get("r"), a.get("s"),
                a.get("p_fines"), a.get("rosaces"), a.get("em_clb"), a.get("perles"),
                a.get("fabricant"), int(bool(a.get("ismail_pierres", 0)))
            ))

        for v in payload.get("ventes", []):
            conn.execute("""INSERT OR REPLACE INTO ventes
                (id_vente,date_achat,date_vente,ref,article,or_grs,vente_au_poids,prix_or_achat,
                 pa,d,em,r,s,p_fines,rosaces,em_clb,perles,pv,benef,client,mode_paiement,commentaire)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                v.get("id_vente"), v.get("date_achat"), v.get("date_vente"), v.get("ref"),
                v.get("article"), v.get("or_grs"), int(bool(v.get("vente_au_poids"))), v.get("prix_or_achat"),
                v.get("pa"), v.get("d"), v.get("em"), v.get("r"), v.get("s"),
                v.get("p_fines"), v.get("rosaces"), v.get("em_clb"), v.get("perles"),
                v.get("pv"), v.get("benef"), v.get("client"), v.get("mode_paiement"), v.get("commentaire")
            ))

        for c in payload.get("credits", []):
            conn.execute("""INSERT OR REPLACE INTO credits
                (id,client,telephone,ref_article,article,montant_total,avance,reste,statut,date_vente,date_solde,notes,paiements)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                c.get("id"), c.get("client"), c.get("telephone"), c.get("ref_article"),
                c.get("article"), c.get("montant_total"), c.get("avance"), c.get("reste"),
                c.get("statut"), c.get("date_vente"), c.get("date_solde"), c.get("notes"),
                json.dumps(c.get("paiements") or [])
            ))

        for f in payload.get("fournisseurs", []):
            conn.execute("""INSERT OR REPLACE INTO fournisseurs
                (id,nom,telephone,montant_total,avance,reste,statut,date_achat,date_solde,notes,articles,paiements)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
                f.get("id"), f.get("nom"), f.get("telephone"), f.get("montant_total"),
                f.get("avance"), f.get("reste"), f.get("statut"), f.get("date_achat"),
                f.get("date_solde"), f.get("notes"),
                json.dumps(f.get("articles") or []),
                json.dumps(f.get("paiements") or [])
            ))

        for ch in payload.get("cheques", []):
            conn.execute("""INSERT OR REPLACE INTO cheques
                (id,client,ref_article,montant,numero,nb_cheques,banque,date_cheque,
                 date_encaissement,dates_encaissement,numeros_cheques,statuts_cheques,statut,credit_id,note,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                ch.get("id"), ch.get("client"), ch.get("ref_article"), ch.get("montant"),
                ch.get("numero"), ch.get("nb_cheques"), ch.get("banque"), ch.get("date_cheque"),
                ch.get("date_encaissement"), json.dumps(ch.get("dates_encaissement") or []),
                json.dumps(ch.get("numeros_cheques") or []), json.dumps(ch.get("statuts_cheques") or []),
                ch.get("statut"), ch.get("credit_id"), ch.get("note"), ch.get("created_at")
            ))

        for fac in payload.get("factures", []):
            conn.execute("""INSERT OR REPLACE INTO factures
                (id,numero,client,telephone,email,ville,articles,total,avance,mode_paiement,note,date,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                fac.get("id"), fac.get("numero"), fac.get("client"), fac.get("telephone"),
                fac.get("email"), fac.get("ville"), json.dumps(fac.get("articles") or []),
                fac.get("total"), fac.get("avance"), fac.get("mode_paiement"),
                fac.get("note"), fac.get("date"), fac.get("created_at")
            ))

        for n in payload.get("notifs", []):
            conn.execute("INSERT OR REPLACE INTO notifs (id,message,type,date,dismissed) VALUES (?,?,?,?,?)", (
                n.get("id"), n.get("message"), n.get("type"), n.get("date"), int(bool(n.get("dismissed")))
            ))

        cfg = payload.get("config", {})
        for k, v in cfg.items():
            conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (k, v))


# ─── Point d'entrée ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    print(f"Base de données : {DB_FILE}")
    print(f"  Articles  : {len(load_articles())}")
    print(f"  Ventes    : {len(load_ventes())}")
    print(f"  Crédits   : {len(load_credits())}")
    print(f"  Fourniss. : {len(load_fournisseurs())}")
    print(f"  Chèques   : {len(load_cheques())}")
    print(f"  Factures  : {len(load_factures())}")
    print(f"  Notifs    : {len(load_notifs())}")
    print(f"  Config    : {load_config()}")
