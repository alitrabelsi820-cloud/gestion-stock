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
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # écriture concurrente plus sûre
    conn.execute("PRAGMA busy_timeout=15000") # attendre si la base est verrouillée
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
    ismail_pierres INTEGER DEFAULT 0,
    quantite      INTEGER DEFAULT 1,
    note          TEXT DEFAULT ''
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
    commentaire    TEXT,
    type_vente     TEXT DEFAULT 'produit'
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

CREATE TABLE IF NOT EXISTS devis (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    client        TEXT,
    telephone     TEXT,
    date_devis    TEXT,
    articles      TEXT DEFAULT '[]',
    total_initial REAL DEFAULT 0,
    total_reduit  REAL DEFAULT 0,
    note          TEXT DEFAULT '',
    created_at    TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    role       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_used  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS print_queue (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ref        INTEGER,
    payload    TEXT DEFAULT '{}',
    status     TEXT DEFAULT 'pending',
    created_at TEXT,
    printed_at TEXT
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
    # Group duplicates: merge identical ones into quantite, keep different ones
    from collections import defaultdict
    seen_first = {}  # id → first article inserted
    for a in arts:
        aid = a.get("id")
        fields = ['or_grs','pa','d','em','r','s','p_fines','rosaces','em_clb','perles','article']
        if aid in seen_first:
            prev = seen_first[aid]
            if all(a.get(f) == prev.get(f) for f in fields):
                # Identical duplicate → increment quantite
                conn.execute("UPDATE articles SET quantite = quantite + 1 WHERE id = ?", (aid,))
                inserted += 1
                continue
            else:
                # Different article → new unique ID
                max_id += 1
                aid = max_id
        seen_first.setdefault(a.get("id"), a)
        seen_ids.add(aid)
        conn.execute("""
            INSERT OR IGNORE INTO articles
            (id,date,article,or_grs,pa,d,em,r,s,p_fines,rosaces,em_clb,perles,fabricant,ismail_pierres,quantite)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (aid, a.get("date"), a.get("article"),
              a.get("or_grs"), a.get("pa"), a.get("d"), a.get("em"),
              a.get("r"), a.get("s"), a.get("p_fines"), a.get("rosaces"),
              a.get("em_clb"), a.get("perles"), a.get("fabricant"),
              1 if a.get("ismail_pierres") else 0,
              int(a.get("quantite") or 1)))
        inserted += 1
    print(f"  articles : {inserted}")

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


def _fix_quantites(conn):
    """
    Migration one-shot : fusionne les doublons identiques créés lors de la migration JSON→SQLite.
    Les articles 4374-4482 sont des copies d'articles existants (même type, poids, prix).
    On incrémente la quantité de l'original et on supprime la copie.
    Ne s'exécute que si nécessaire (présence d'articles > 4372 avec quantite=1).
    """
    # Vérifier si la migration est nécessaire
    row = conn.execute("SELECT COUNT(*) FROM articles WHERE id > 4372 AND (quantite IS NULL OR quantite = 1)").fetchone()
    if not row or row[0] == 0:
        return
    print(f"[DB] Fusion des doublons quantité ({row[0]} articles parasites détectés)...")

    # Articles parasites : id > 4372, pas de ventes associées
    parasites = conn.execute("""
        SELECT a.id, a.article, a.or_grs, a.pa, a.d, a.em, a.r, a.s, a.p_fines, a.rosaces, a.em_clb, a.perles
        FROM articles a
        WHERE a.id > 4372
          AND NOT EXISTS (SELECT 1 FROM ventes v WHERE v.ref = a.id)
    """).fetchall()

    deleted = 0
    for p in parasites:
        pid = p[0]
        # 1er essai : article identique (même poids, même prix)
        orig = conn.execute("""
            SELECT id FROM articles
            WHERE id < ? AND article = ? AND or_grs = ? AND pa = ?
              AND (d IS ? OR d = ?) AND (em IS ? OR em = ?)
              AND (r IS ? OR r = ?) AND (s IS ? OR s = ?)
        """, (pid, p[1], p[2], p[3], p[4], p[4], p[5], p[5], p[6], p[6], p[7], p[7])).fetchone()
        if not orig:
            # 2e essai : même type + même prix (poids peut différer légèrement)
            orig = conn.execute("""
                SELECT id FROM articles
                WHERE id < ? AND article = ? AND pa = ?
                  AND (d IS ? OR d = ?) AND (em IS ? OR em = ?)
                  AND (r IS ? OR r = ?) AND (s IS ? OR s = ?)
                ORDER BY id
                LIMIT 1
            """, (pid, p[1], p[3], p[4], p[4], p[5], p[5], p[6], p[6], p[7], p[7])).fetchone()
        if not orig:
            # 3e essai : même type seulement — forcer la fusion sous la ref la plus proche
            orig = conn.execute("""
                SELECT id FROM articles WHERE id < ? AND article = ?
                ORDER BY ABS(id - ?) LIMIT 1
            """, (pid, p[1], pid)).fetchone()
        if orig:
            conn.execute("UPDATE articles SET quantite = COALESCE(quantite,1) + 1 WHERE id = ?", (orig[0],))
            conn.execute("DELETE FROM articles WHERE id = ?", (pid,))
            deleted += 1

    print(f"[DB] Fusion terminée : {deleted} doublons supprimés, quantités mises à jour.")


def init_db():
    """Crée la base de données et migre les JSON si c'est la première fois."""
    DATA_DIR.mkdir(exist_ok=True)
    first_run = not DB_FILE.exists()
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        if first_run:
            _migrate_json(conn)
        # Migration 1 : ajouter colonne quantite si absente
        try:
            conn.execute("ALTER TABLE articles ADD COLUMN quantite INTEGER DEFAULT 1")
            print("[DB] Colonne 'quantite' ajoutée aux articles.")
        except Exception:
            pass  # Colonne déjà présente
        # Migration 2 : désactivée (bug corrigé, ne plus supprimer les articles > 4372)
        # _fix_quantites(conn)
        # Migration 3 : ajouter colonne note si absente
        try:
            conn.execute("ALTER TABLE articles ADD COLUMN note TEXT DEFAULT ''")
            print("[DB] Colonne 'note' ajoutée aux articles.")
        except Exception:
            pass
        # Migration 4 : code référence texte (ex: chaine_jaune) — sinon on affiche l'id
        try:
            conn.execute("ALTER TABLE articles ADD COLUMN ref_code TEXT")
            print("[DB] Colonne 'ref_code' ajoutée aux articles.")
        except Exception:
            pass
        # Migration 5 : mode de vente explicite (1 = au poids, 0 = à la pièce).
        # NULL = non défini → on retombe sur l'ancienne détection automatique.
        try:
            conn.execute("ALTER TABLE articles ADD COLUMN vente_poids INTEGER")
            print("[DB] Colonne 'vente_poids' ajoutée aux articles.")
        except Exception:
            pass
        # Migration 4 : source et telephone sur les ventes
        try:
            conn.execute("ALTER TABLE ventes ADD COLUMN source TEXT DEFAULT 'stock'")
            print("[DB] Colonne 'source' ajoutée aux ventes.")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE ventes ADD COLUMN telephone TEXT DEFAULT ''")
            print("[DB] Colonne 'telephone' ajoutée aux ventes.")
        except Exception:
            pass
        # Migration : type_vente (produit/service)
        try:
            conn.execute("ALTER TABLE ventes ADD COLUMN type_vente TEXT DEFAULT 'produit'")
            print("[DB] Colonne 'type_vente' ajoutée aux ventes.")
        except Exception:
            pass
        # Marquer les ventes Sidi Baba comme service (toujours, car historique)
        try:
            conn.execute("UPDATE ventes SET type_vente='service' WHERE client='Sidi Baba'")
        except Exception:
            pass
        # Migration 5 : mode prix global sur les factures
        try:
            conn.execute("ALTER TABLE factures ADD COLUMN prix_global INTEGER DEFAULT 0")
            print("[DB] Colonne 'prix_global' ajoutée aux factures.")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE factures ADD COLUMN total_global REAL DEFAULT 0")
            print("[DB] Colonne 'total_global' ajoutée aux factures.")
        except Exception:
            pass
        # Migration 6 : table sessions persistantes
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT PRIMARY KEY,
                role       TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_used  TEXT NOT NULL
            );
        """)

        # Migration 7 : journal des recherches employés
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS search_logs (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                role    TEXT,
                ref     TEXT,
                article TEXT,
                found   INTEGER DEFAULT 0,
                ip      TEXT,
                device  TEXT,
                raw_ts  REAL,
                ts      TEXT
            );
        """)
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_search_logs_ts ON search_logs(raw_ts)")
        except Exception:
            pass

        # Migration 8 : journal d'audit + corbeille (snapshot pour restauration)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                action    TEXT,          -- created | deleted | modified | restored
                entity    TEXT,          -- vente | article | credit | cheque | fournisseur
                ref       TEXT,
                summary   TEXT,
                role      TEXT,
                ip        TEXT,
                device    TEXT,
                snapshot  TEXT,          -- JSON de l'objet (pour restaurer)
                restored  INTEGER DEFAULT 0,
                raw_ts    REAL,
                ts        TEXT
            );
        """)
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(raw_ts)")
        except Exception:
            pass


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
        "quantite": row["quantite"] if row["quantite"] else 1,
        "note": row["note"] or "",
        "ref_code": (row["ref_code"] if "ref_code" in row.keys() else None) or None,
        # None = non défini (détection auto), 1 = au poids, 0 = à la pièce
        "vente_poids": (row["vente_poids"] if "vente_poids" in row.keys() else None),
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
            (id,date,article,or_grs,pa,d,em,r,s,p_fines,rosaces,em_clb,perles,fabricant,ismail_pierres,quantite,note,ref_code,vente_poids)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, [(a["id"], a.get("date"), a.get("article"),
               a.get("or_grs"), a.get("pa"), a.get("d"), a.get("em"),
               a.get("r"), a.get("s"), a.get("p_fines"), a.get("rosaces"),
               a.get("em_clb"), a.get("perles"), a.get("fabricant"),
               1 if a.get("ismail_pierres") else 0,
               int(a.get("quantite") or 1),
               str(a.get("note") or ""),
               (a.get("ref_code") or None),
               (None if a.get("vente_poids") is None else (1 if a.get("vente_poids") else 0))
               ) for a in articles])


# ─── VENTES ───────────────────────────────────────────────────────────────────

def _row_to_vente(row):
    keys = row.keys()
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
        "telephone": row["telephone"] if "telephone" in keys else "",
        "mode_paiement": row["mode_paiement"], "commentaire": row["commentaire"],
        "source": row["source"] if "source" in keys else "stock",
        "type_vente": row["type_vente"] if "type_vente" in keys else "produit",
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
             pv,benef,client,telephone,mode_paiement,commentaire,source,type_vente)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, [(v["id_vente"], v.get("date_achat"), v.get("date_vente"),
               v.get("ref"), v.get("article"), v.get("or_grs"),
               1 if v.get("vente_au_poids") else 0, v.get("prix_or_achat"),
               v.get("pa"), v.get("d"), v.get("em"), v.get("r"), v.get("s"),
               v.get("p_fines"), v.get("rosaces"), v.get("em_clb"), v.get("perles"),
               v.get("pv"), v.get("benef"), v.get("client"),
               v.get("telephone",""), v.get("mode_paiement"), v.get("commentaire"),
               v.get("source","stock"), v.get("type_vente","produit")) for v in ventes])


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
        "prix_global": int(row["prix_global"]) if row["prix_global"] else 0,
        "total_global": float(row["total_global"]) if row["total_global"] else 0,
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
             total,avance,mode_paiement,note,date,created_at,prix_global,total_global)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, [(fac["id"], fac.get("numero"), fac.get("client"),
               fac.get("telephone"), fac.get("email"), fac.get("ville"),
               json.dumps(fac.get("articles",[]), ensure_ascii=False),
               fac.get("total"), fac.get("avance",0),
               fac.get("mode_paiement"), fac.get("note",""),
               fac.get("date"), fac.get("created_at"),
               int(fac.get("prix_global", 0)), float(fac.get("total_global", 0))) for fac in factures])


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
                (id,date,article,or_grs,pa,d,em,r,s,p_fines,rosaces,em_clb,perles,fabricant,ismail_pierres,quantite,note)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                a.get("id"), a.get("date"), a.get("article"), a.get("or_grs"),
                a.get("pa"), a.get("d"), a.get("em"), a.get("r"), a.get("s"),
                a.get("p_fines"), a.get("rosaces"), a.get("em_clb"), a.get("perles"),
                a.get("fabricant"), int(bool(a.get("ismail_pierres", 0))),
                int(a.get("quantite") or 1), str(a.get("note") or "")
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
                (id,client,contact,date_achat,refs,article,montant_total,paiements,reste,statut,date_solde,note)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
                c.get("id"), c.get("client"), c.get("contact"), c.get("date_achat"),
                c.get("refs"), c.get("article"), c.get("montant_total"),
                json.dumps(c.get("paiements") or []),
                c.get("reste"), c.get("statut"), c.get("date_solde"), c.get("note","")
            ))

        for f in payload.get("fournisseurs", []):
            conn.execute("""INSERT OR REPLACE INTO fournisseurs
                (id,fournisseur,contact,date_commande,num_commande,article,montant_total,paiements,reste,statut,date_solde,note)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
                f.get("id"), f.get("fournisseur"), f.get("contact"), f.get("date_commande"),
                f.get("num_commande"), f.get("article"), f.get("montant_total"),
                json.dumps(f.get("paiements") or []),
                f.get("reste"), f.get("statut"), f.get("date_solde"), f.get("note","")
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
            conn.execute("INSERT OR REPLACE INTO notifs (id,type,date,ref,article,client,dismissed) VALUES (?,?,?,?,?,?,?)", (
                n.get("id"), n.get("type"), n.get("date"), n.get("ref"),
                n.get("article"), n.get("client"), int(bool(n.get("dismissed")))
            ))

        cfg = payload.get("config", {})
        for k, v in cfg.items():
            conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (k, v))


# ─── SESSIONS ─────────────────────────────────────────────────────────────────

def create_session(token: str, role: str):
    """Crée ou remplace une session dans la base."""
    now = datetime.now().isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sessions (token, role, created_at, last_used) VALUES (?,?,?,?)",
            (token, role, now, now)
        )

def get_session_role(token: str):
    """Retourne le rôle associé au token, ou None si inexistant/expiré."""
    if not token:
        return None
    with get_conn() as conn:
        row = conn.execute("SELECT role FROM sessions WHERE token=?", (token,)).fetchone()
        if row:
            conn.execute("UPDATE sessions SET last_used=? WHERE token=?",
                         (datetime.now().isoformat(), token))
            return row["role"]
    return None

def delete_session(token: str):
    """Supprime une session (logout)."""
    if not token:
        return
    with get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))

def cleanup_old_sessions(max_age_hours: int = 72):
    """Supprime les sessions inactives depuis plus de max_age_hours."""
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
    with get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE last_used < ?", (cutoff,))


# ─── JOURNAL DES RECHERCHES EMPLOYÉS ──────────────────────────────────────────

def log_search(role, ref, article, found, ip="", device=""):
    """Enregistre une recherche de référence. NE déclenche PAS de sync R2
    (appelé sur des requêtes GET uniquement)."""
    import time as _t
    now = datetime.now()
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO search_logs (role, ref, article, found, ip, device, raw_ts, ts) VALUES (?,?,?,?,?,?,?,?)",
                (role, str(ref), article or "", 1 if found else 0, ip or "", device or "",
                 _t.time(), now.strftime("%d/%m/%Y %H:%M:%S"))
            )
            # Purge : ne garder que les 5000 dernières entrées
            conn.execute("""
                DELETE FROM search_logs WHERE id NOT IN (
                    SELECT id FROM search_logs ORDER BY id DESC LIMIT 5000
                )
            """)
    except Exception:
        pass

def get_search_logs(limit=100, since_ts=None):
    """Retourne les recherches récentes (les plus récentes en premier)."""
    with get_conn() as conn:
        if since_ts is not None:
            rows = conn.execute(
                "SELECT * FROM search_logs WHERE raw_ts > ? ORDER BY id DESC LIMIT ?",
                (since_ts, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM search_logs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    cols = rows[0].keys() if rows else []
    return [{
        "id": r["id"], "role": r["role"], "ref": r["ref"],
        "article": r["article"], "found": bool(r["found"]),
        "ip": (r["ip"] if "ip" in cols else ""),
        "device": (r["device"] if "device" in cols else ""),
        "raw_ts": r["raw_ts"], "ts": r["ts"],
    } for r in rows]

def search_logs_stats(since_ts):
    """Compte total et introuvables depuis un timestamp donné."""
    with get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM search_logs WHERE raw_ts > ?", (since_ts,)
        ).fetchone()[0]
        nf = conn.execute(
            "SELECT COUNT(*) FROM search_logs WHERE raw_ts > ? AND found = 0", (since_ts,)
        ).fetchone()[0]
    return {"total": total, "introuvables": nf}


# ─── JOURNAL D'AUDIT + CORBEILLE ──────────────────────────────────────────────

def log_audit(action, entity, ref, summary, role="", ip="", device="", snapshot=None):
    """Enregistre une action sensible (création/suppression/modif).
    snapshot : objet Python sérialisable (pour pouvoir restaurer)."""
    import time as _t
    now = datetime.now()
    try:
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO audit_log
                   (action, entity, ref, summary, role, ip, device, snapshot, raw_ts, ts)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (action, entity, str(ref), summary, role, ip or "", device or "",
                 json.dumps(snapshot, ensure_ascii=False) if snapshot is not None else None,
                 _t.time(), now.strftime("%d/%m/%Y %H:%M:%S"))
            )
            # Purge : garder les 10000 dernières entrées, MAIS ne JAMAIS
            # supprimer les éléments de la corbeille (suppressions non
            # restaurées avec snapshot) — ils restent indéfiniment.
            conn.execute("""DELETE FROM audit_log
                WHERE id NOT IN (SELECT id FROM audit_log ORDER BY id DESC LIMIT 10000)
                  AND NOT (action='deleted' AND snapshot IS NOT NULL AND restored=0)""")
    except Exception:
        pass

def add_print_job(ref, payload):
    """Ajoute une étiquette à la file d'impression. payload = dict prêt à imprimer."""
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO print_queue (ref, payload, status, created_at) VALUES (?,?, 'pending', ?)",
            (int(ref), json.dumps(payload, ensure_ascii=False), now)
        )
        return cur.lastrowid

def get_pending_print_jobs(limit=50):
    """Retourne les étiquettes en attente d'impression (plus anciennes d'abord)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, ref, payload, created_at FROM print_queue "
            "WHERE status='pending' ORDER BY id ASC LIMIT ?", (limit,)
        ).fetchall()
    out = []
    for r in rows:
        try:
            payload = json.loads(r["payload"] or "{}")
        except Exception:
            payload = {}
        out.append({"id": r["id"], "ref": r["ref"], "payload": payload,
                    "created_at": r["created_at"]})
    return out

def mark_print_job_done(job_id):
    """Marque une étiquette comme imprimée + purge les vieilles entrées."""
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    with get_conn() as conn:
        conn.execute("UPDATE print_queue SET status='done', printed_at=? WHERE id=?",
                     (now, int(job_id)))
        # Purge : garder les 500 dernières entrées imprimées
        conn.execute("""DELETE FROM print_queue WHERE status='done' AND id NOT IN (
            SELECT id FROM print_queue WHERE status='done' ORDER BY id DESC LIMIT 500)""")

def clear_pending_print_jobs():
    """Vide la file d'attente (annule tout ce qui n'est pas encore imprimé)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM print_queue WHERE status='pending'")

def get_audit_logs(limit=200, entity=None, action=None):
    """Retourne les entrées d'audit récentes (plus récentes d'abord)."""
    q = "SELECT * FROM audit_log WHERE 1=1"
    args = []
    if entity: q += " AND entity=?"; args.append(entity)
    if action: q += " AND action=?"; args.append(action)
    q += " ORDER BY id DESC LIMIT ?"; args.append(limit)
    with get_conn() as conn:
        rows = conn.execute(q, args).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "action": r["action"], "entity": r["entity"],
            "ref": r["ref"], "summary": r["summary"], "role": r["role"],
            "ip": r["ip"], "device": r["device"],
            "has_snapshot": bool(r["snapshot"]),
            "restored": bool(r["restored"]),
            "raw_ts": r["raw_ts"], "ts": r["ts"],
        })
    return out

def get_audit_entry(audit_id):
    """Retourne une entrée complète (avec snapshot désérialisé)."""
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM audit_log WHERE id=?", (audit_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["snapshot"] = json.loads(r["snapshot"]) if r["snapshot"] else None
    return d

def mark_audit_restored(audit_id):
    with get_conn() as conn:
        conn.execute("UPDATE audit_log SET restored=1 WHERE id=?", (audit_id,))

def get_trash(limit=200):
    """Éléments supprimés et restaurables (snapshot présent, non restaurés)."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM audit_log
               WHERE action='deleted' AND snapshot IS NOT NULL AND restored=0
               ORDER BY id DESC LIMIT ?""", (limit,)
        ).fetchall()
    return [{
        "id": r["id"], "entity": r["entity"], "ref": r["ref"],
        "summary": r["summary"], "role": r["role"], "device": r["device"],
        "raw_ts": r["raw_ts"], "ts": r["ts"],
    } for r in rows]


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


# ─── DEVIS ────────────────────────────────────────────────────────────────────

def _row_to_devis(row):
    d = dict(row)
    d['articles'] = json.loads(d.get('articles') or '[]')
    return d

def mark_devis_vendu(devis_id: int, date_vente: str = None):
    """Marque un devis comme vendu (sans le supprimer)."""
    from datetime import datetime as _dt
    dv = date_vente or _dt.now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        # Ajouter la colonne statut si elle n'existe pas encore
        cols = [r[1] for r in conn.execute("PRAGMA table_info(devis)").fetchall()]
        if 'statut' not in cols:
            conn.execute("ALTER TABLE devis ADD COLUMN statut TEXT DEFAULT 'actif'")
        if 'date_vente' not in cols:
            conn.execute("ALTER TABLE devis ADD COLUMN date_vente TEXT")
        conn.execute(
            "UPDATE devis SET statut='vendu', date_vente=? WHERE id=?",
            (dv, int(devis_id))
        )

def load_devis():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM devis ORDER BY id DESC").fetchall()
    return [_row_to_devis(r) for r in rows]

def insert_devis(item):
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO devis (client, telephone, date_devis, articles, total_initial, total_reduit, note, created_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            item.get('client',''), item.get('telephone',''),
            item.get('date_devis',''),
            json.dumps(item.get('articles',[]), ensure_ascii=False),
            float(item.get('total_initial',0)), float(item.get('total_reduit',0)),
            item.get('note',''), item.get('created_at','')
        ))
        return cur.lastrowid

def delete_devis(devis_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM devis WHERE id=?", (int(devis_id),))
    print(f"  Config    : {load_config()}")
