#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Synchronise la base LOCALE avec la PRODUCTION au lancement de l'app.

Récupère les dernières données depuis l'app en ligne (Railway) et les importe
dans la base locale, en préservant le schéma actuel (codes de lots de chaînes,
catégories service/réparation, etc.).

Robuste : si la production est injoignable (pas de réseau) ou non configurée,
on NE touche à rien et l'app démarre normalement avec les données locales.
Une sauvegarde de la base locale est faite avant chaque synchronisation.

Réutilise l'accès enregistré par l'agent d'impression (~/.gestionstock_print.json).
"""

import json
import os
import shutil
import sqlite3
import sys
import urllib.request

CONFIG_PATH = os.path.expanduser("~/.gestionstock_print.json")


def main():
    # 1) Config d'accès à la production
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        url = cfg["url"].rstrip("/")
        pwd = cfg["password"]
    except Exception:
        print("ℹ️  Pas de synchronisation configurée — démarrage avec les données locales.")
        return 0

    try:
        import database as db
        db.init_db()

        # 2) Connexion + récupération des données (lecture seule côté production)
        req = urllib.request.Request(
            url + "/api/login",
            data=json.dumps({"password": pwd}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        resp = urllib.request.urlopen(req, timeout=15)
        cookie = (resp.headers.get("Set-Cookie") or "").split(";")[0]

        def get(path):
            rq = urllib.request.Request(url + path, headers={"Cookie": cookie})
            return json.loads(urllib.request.urlopen(rq, timeout=120).read())

        backup = get("/api/backup")
        try:
            conf = get("/api/config")
            conf = conf if isinstance(conf, dict) else None
        except Exception:
            conf = None

        # Garde-fou : ne jamais écraser le local avec des données vides
        if not backup.get("articles") and not backup.get("ventes"):
            print("⚠️  Données de production vides — synchronisation ignorée.")
            return 0

        # 3) Sauvegarde de la base locale (un fichier de secours, écrasé à chaque fois)
        try:
            c = sqlite3.connect(str(db.DB_FILE))
            c.execute("PRAGMA wal_checkpoint(FULL)")
            c.close()
            shutil.copy2(str(db.DB_FILE),
                         os.path.join(str(db.DATA_DIR), "gestionstock_avant_sync.db"))
        except Exception:
            pass

        # 4) Import (les fonctions save_* respectent le schéma actuel)
        db.save_articles(backup["articles"])
        db.save_ventes(backup["ventes"])
        db.save_credits(backup["credits"])
        db.save_factures(backup.get("factures", []))
        db.save_cheques(backup.get("cheques", []))
        db.save_fournisseurs(backup.get("fournisseurs", []))
        if conf:
            db.save_config(conf)

        print(f"✅ Données à jour avec la production ({len(backup.get('ventes', []))} ventes).")
    except Exception as e:
        print(f"⚠️  Synchronisation impossible ({e}).")
        print("    → Démarrage avec les données locales (pas de blocage).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
