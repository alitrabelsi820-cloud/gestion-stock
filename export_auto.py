#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Export comptable automatique — TRABELSI Bijouterie.

Télécharge les exports Excel mensuels dans le dossier OneDrive/Bureau,
sans intervention. Conçu pour être lancé chaque jour par macOS :

  - il regarde tous les mois TERMINÉS (le mois en cours est ignoré) ;
  - il télécharge ceux qui manquent encore dans le dossier ;
  - si le Mac était éteint le 1er du mois, le rattrapage se fait tout seul
    au prochain démarrage. Aucun mois ne peut être oublié.

Réutilise la configuration de l'agent d'impression (~/.gestionstock_print.json)
pour l'adresse du logiciel et le mot de passe.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime

CONFIG_PATH = os.path.expanduser("~/.gestionstock_print.json")
DOSSIER = os.path.expanduser("~/OneDrive/Bijouterie - VF/Exports Comptables")
DEFAULT_URL = "https://app-production-1856.up.railway.app"


def log(msg):
    print(f"[{datetime.now():%d/%m/%Y %H:%M}] {msg}", flush=True)


def charger_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
            if cfg.get("password"):
                return cfg.get("url", DEFAULT_URL).rstrip("/"), cfg["password"]
        except Exception:
            pass
    log("⚠️  Configuration introuvable (~/.gestionstock_print.json).")
    log("    Lance une fois l'app « Imprimer étiquettes » pour l'enregistrer.")
    return None, None


class App:
    def __init__(self, url, password):
        self.url, self.password, self.cookie = url, password, None

    def _req(self, chemin, data=None, brut=False):
        headers = {"Content-Type": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(self.url + chemin, data=body, headers=headers,
                                     method="POST" if data is not None else "GET")
        resp = urllib.request.urlopen(req, timeout=120)
        sc = resp.headers.get("Set-Cookie")
        if sc:
            self.cookie = sc.split(";")[0]
        contenu = resp.read()
        if brut:
            nom = ""
            cd = resp.headers.get("Content-Disposition", "")
            m = re.search(r'filename="([^"]+)"', cd)
            if m:
                nom = m.group(1)
            return contenu, nom
        return json.loads(contenu)

    def login(self):
        self._req("/api/login", {"password": self.password})

    def mois_disponibles(self):
        return self._req("/api/export-mois").get("mois", [])

    def telecharger(self, mois):
        return self._req(f"/api/export-excel?mois={mois}", brut=True)


def main():
    log("── Export comptable automatique ──")
    url, password = charger_config()
    if not password:
        return 1

    os.makedirs(DOSSIER, exist_ok=True)
    app = App(url, password)
    try:
        app.login()
    except Exception as e:
        log(f"⚠️  Connexion impossible : {e}")
        return 1

    try:
        mois_dispo = app.mois_disponibles()
    except Exception as e:
        log(f"⚠️  Liste des mois indisponible : {e}")
        return 1

    mois_courant = datetime.now().strftime("%Y-%m")
    # On n'exporte que les mois TERMINÉS (le mois en cours bougera encore).
    a_faire = [m for m in mois_dispo if m < mois_courant]

    existants = set(os.listdir(DOSSIER))
    telecharges = 0
    for mois in sorted(a_faire):
        if any(f.startswith(f"TRABELSI_{mois}_") for f in existants):
            continue  # déjà exporté
        try:
            contenu, nom = app.telecharger(mois)
            if not nom:
                nom = f"TRABELSI_{mois}.xlsx"
            chemin = os.path.join(DOSSIER, nom)
            tmp = chemin + ".part"
            with open(tmp, "wb") as f:
                f.write(contenu)
            os.replace(tmp, chemin)          # écriture atomique
            log(f"✅ {nom}  ({len(contenu)//1024} Ko)")
            telecharges += 1
        except Exception as e:
            log(f"⚠️  {mois} : {e}")

    if telecharges:
        log(f"Terminé — {telecharges} fichier(s) ajouté(s) dans le dossier.")
    else:
        log("Rien à faire — tous les mois terminés sont déjà exportés.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
