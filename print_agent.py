#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent d'impression d'étiquettes — TRABELSI Bijouterie.

Tourne sur le Mac relié en USB à l'imprimante Zebra ZD220.
Il interroge l'app en ligne (Railway) toutes les 2-3 secondes, récupère les
étiquettes mises en file d'attente (bouton « Imprimer » dans l'app) et les
envoie à la Zebra en langage ZPL.

Aucune dépendance externe : uniquement la bibliothèque standard Python 3.
"""

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
import getpass

# ── Configuration ────────────────────────────────────────────────────────────
DEFAULT_URL = "https://app-production-1856.up.railway.app"
CONFIG_PATH = os.path.expanduser("~/.gestionstock_print.json")
POLL_SECONDS = 3

# Réglages étiquette (203 dpi, 8 dots/mm) — étiquette bijou haltère ~60×12mm
# PW=480 FIXE : ne pas changer, sinon la référence centrée (^FB) se décale.
LABEL = dict(PW=480, LL=96, DARKNESS=28)


def load_config():
    """Charge (ou demande à la première utilisation) l'URL et le mot de passe."""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    print("── Première configuration ──")
    url = input(f"Adresse de l'app [{DEFAULT_URL}] : ").strip() or DEFAULT_URL
    pwd = getpass.getpass("Mot de passe admin de l'app : ").strip()
    cfg = {"url": url.rstrip("/"), "password": pwd}
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        os.chmod(CONFIG_PATH, 0o600)
    except Exception as e:
        print(f"(impossible d'enregistrer la config : {e})")
    return cfg


def ensure_single_instance():
    """Tue toute autre instance de l'agent déjà en cours (évite les doublons
    d'impression : 2 agents = 2 étiquettes, parfois de versions différentes)."""
    me = os.getpid()
    try:
        out = subprocess.run(["pgrep", "-f", "print_agent.py"],
                             capture_output=True, text=True).stdout
        for pid in out.split():
            try:
                pid = int(pid)
            except ValueError:
                continue
            if pid != me:
                try:
                    os.kill(pid, 9)
                    print(f"  (ancien agent {pid} arrêté)")
                except Exception:
                    pass
    except Exception:
        pass


def find_printer():
    """Trouve la file d'impression de la Zebra."""
    try:
        out = subprocess.run(["lpstat", "-p"], capture_output=True, text=True).stdout
    except Exception:
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and ("ZD220" in parts[1] or "Zebra" in parts[1]):
            return parts[1]
    return None


class App:
    def __init__(self, cfg):
        self.url = cfg["url"]
        self.password = cfg["password"]
        self.cookie = None

    def _request(self, path, data=None):
        headers = {"Content-Type": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(self.url + path, data=body,
                                     headers=headers, method="POST" if data is not None else "GET")
        resp = urllib.request.urlopen(req, timeout=20)
        # récupérer le cookie de session au login
        sc = resp.headers.get("Set-Cookie")
        if sc:
            self.cookie = sc.split(";")[0]
        return json.loads(resp.read().decode())

    def login(self):
        self._request("/api/login", {"password": self.password})

    def get_jobs(self):
        return self._request("/api/print-queue").get("jobs", [])

    def mark_done(self, jid):
        self._request("/api/print-queue/done", {"id": jid})


def build_zpl(payload):
    """Construit le ZPL d'une étiquette à partir du contenu (ref + pierres)."""
    ref = payload.get("ref", "")
    stones = payload.get("stones", []) or []
    try:
        copies = max(1, min(int(payload.get("copies", 1)), 99))
    except (TypeError, ValueError):
        copies = 1
    z = ["^XA", "^MTT", f"^MD{LABEL['DARKNESS']}",
         f"^PW{LABEL['PW']}", f"^LL{LABEL['LL']:04d}", "^LH0,0", "^LS0"]
    # Gauche : rien.
    # Milieu : la référence seule, centrée automatiquement dans la partie du
    # milieu (^FB = bloc centré, quelle que soit la longueur de la réf).
    z.append(f"^FO180,33^A0N,30,30^FB180,1,0,C,0^FD#{ref}^FS")
    # Droite : contenu (pierres : D, Em, S, ...), empilées, centrées
    # verticalement, décalées vers le centre de la partie droite.
    n = len(stones)
    if n >= 4:
        fs, step = 16, 20
    else:
        fs, step = 20, 23
    if n:
        block = fs + (n - 1) * step          # hauteur totale du bloc pierres
        # centrage vertical + léger décalage vers le bas (~1mm = 8 dots) pour
        # ne pas toucher le haut de l'étiquette.
        y = max(6, (96 - block) // 2 + 8)
        for abbr, val in stones:
            z.append(f"^FO404,{y}^A0N,{fs},{fs}^FD{abbr}: {val}^FS")
            y += step
    # Nombre d'exemplaires identiques
    z.append(f"^PQ{copies},0,0,N")
    z.append("^XZ")
    return "\n".join(z)


def print_zpl(printer, zpl):
    p = subprocess.run(["lp", "-d", printer, "-o", "raw"],
                       input=zpl.encode(), capture_output=True)
    return p.returncode == 0


def main():
    print("═══════════════════════════════════════════")
    print("  🏷️  Agent d'impression étiquettes — TRABELSI")
    print("═══════════════════════════════════════════\n")

    ensure_single_instance()   # jamais deux agents en même temps
    cfg = load_config()
    app = App(cfg)

    # Export comptable : télécharge en arrière-plan les mois terminés manquants
    # (rattrapage automatique — aucun mois ne peut être oublié).
    def _export_auto():
        try:
            import export_auto
            export_auto.main()
        except Exception:
            pass
    threading.Thread(target=_export_auto, daemon=True).start()

    printer = find_printer()
    if not printer:
        print("⚠️  Imprimante Zebra introuvable. Vérifie qu'elle est branchée")
        print("    et ajoutée dans Réglages > Imprimantes, puis relance.")
        input("\nAppuie sur Entrée pour quitter…")
        return
    print(f"🖨️  Imprimante : {printer}")

    try:
        app.login()
        print(f"🔗  Connecté à {app.url}")
    except Exception as e:
        print(f"⚠️  Connexion impossible ({e}).")
        print("    Vérifie l'adresse et le mot de passe (supprime ~/.gestionstock_print.json pour reconfigurer).")
        input("\nAppuie sur Entrée pour quitter…")
        return

    print("\n✅  En marche. Laisse cette fenêtre ouverte.")
    print("    Clique « Imprimer » sur un article dans l'app → l'étiquette sort ici.\n")

    while True:
        try:
            jobs = app.get_jobs()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                try:
                    app.login()
                except Exception:
                    pass
            time.sleep(POLL_SECONDS)
            continue
        except Exception:
            time.sleep(POLL_SECONDS)
            continue

        for job in jobs:
            zpl = build_zpl(job.get("payload", {}))
            ok = print_zpl(printer, zpl)
            ref = job.get("ref", "?")
            if ok:
                try:
                    app.mark_done(job["id"])
                    print(f"  ✅  Étiquette #{ref} imprimée")
                except Exception:
                    print(f"  ✅  #{ref} imprimée (marquage en attente)")
            else:
                print(f"  ⚠️  Échec impression #{ref} (imprimante prête ?)")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nArrêt.")
