"""
backup.py — Sauvegarde automatique des données
- Local  : dossier backups/ (copie des JSON + base SQLite)
- OneDrive : copie de gestionstock.db vers OneDrive/BIjouterie/Backups-DB/
Conserve les 30 derniers backups dans chaque emplacement.
"""
import shutil, os
from pathlib import Path
from datetime import datetime

BASE_DIR   = Path(__file__).parent
DATA_DIR   = BASE_DIR / "data"
BACKUP_DIR = BASE_DIR / "backups"
DB_FILE    = DATA_DIR / "gestionstock.db"
MAX_BACKUPS = 30

ONEDRIVE_BACKUP_DIR = Path(
    "/Users/mac/Library/CloudStorage/OneDrive-Personnel(2)/BIjouterie -VF 2/Backups-DB"
)


def run_backup():
    now   = datetime.now()
    stamp = now.strftime("%Y-%m-%d_%H-%M-%S")

    # ── Backup local ──────────────────────────────────────────────────────────
    dest = BACKUP_DIR / stamp
    dest.mkdir(parents=True, exist_ok=True)

    files_saved = []

    # Copie de la base SQLite
    if DB_FILE.exists():
        shutil.copy2(DB_FILE, dest / DB_FILE.name)
        files_saved.append(DB_FILE.name)

    # Copie des JSON (garde une trace lisible des données)
    for json_file in DATA_DIR.glob("*.json"):
        shutil.copy2(json_file, dest / json_file.name)
        files_saved.append(json_file.name)

    print(f"[{stamp}] Backup local OK → {dest} ({len(files_saved)} fichiers)")

    # Nettoyer les vieux backups locaux
    all_backups = sorted(BACKUP_DIR.iterdir(), key=lambda p: p.name)
    for old in all_backups[:-MAX_BACKUPS]:
        shutil.rmtree(old)
        print(f"  Ancien backup local supprimé : {old.name}")

    # ── Backup OneDrive ───────────────────────────────────────────────────────
    if DB_FILE.exists() and ONEDRIVE_BACKUP_DIR.parent.exists():
        try:
            ONEDRIVE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            od_dest = ONEDRIVE_BACKUP_DIR / f"gestionstock_{stamp}.db"
            shutil.copy2(DB_FILE, od_dest)
            print(f"  Backup OneDrive OK → {od_dest.name}")

            # Nettoyer les vieux backups OneDrive
            od_backups = sorted(
                [p for p in ONEDRIVE_BACKUP_DIR.glob("gestionstock_*.db")],
                key=lambda p: p.name
            )
            for old_od in od_backups[:-MAX_BACKUPS]:
                old_od.unlink()
                print(f"  Ancien backup OneDrive supprimé : {old_od.name}")
        except Exception as e:
            print(f"  [Backup OneDrive] Erreur (non bloquante) : {e}")
    else:
        if not ONEDRIVE_BACKUP_DIR.parent.exists():
            print("  [Backup OneDrive] OneDrive non disponible, backup local uniquement.")

    return stamp, files_saved


if __name__ == "__main__":
    run_backup()
