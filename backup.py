"""
backup.py — Sauvegarde automatique des données
- Local  : dossier backups/ (copie des JSON + base SQLite)
- OneDrive : copie de gestionstock.db vers OneDrive/BIjouterie/Backups-DB/
- Sync photos : télécharge les nouvelles photos R2 → photos_compressed/
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


def sync_photos_from_r2():
    """Télécharge depuis R2 les photos manquantes dans photos_compressed/."""
    import os
    R2_ACCESS_KEY = os.environ.get("R2_ACCESS_KEY", "")
    R2_SECRET_KEY = os.environ.get("R2_SECRET_KEY", "")
    R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "0214bd70a06317f8616baeb74eba7d20")
    R2_BUCKET     = os.environ.get("R2_BUCKET_NAME", "bijouterie-photos")

    # Credentials hardcodés en fallback pour le Mac local
    if not R2_ACCESS_KEY:
        R2_ACCESS_KEY = "9abc7f46ddc14792455f77bd6eefa304"
        R2_SECRET_KEY = "25dfdaae576cadfd29ac0fb6991b05069ce182416d36e812525b29d96847373e"

    PHOTOS_DIR = BASE_DIR / "photos_compressed"
    PHOTOS_DIR.mkdir(exist_ok=True)

    try:
        import boto3, warnings
        warnings.filterwarnings("ignore")
        s3 = boto3.client("s3",
            endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
            aws_access_key_id=R2_ACCESS_KEY,
            aws_secret_access_key=R2_SECRET_KEY,
            region_name="auto")

        paginator = s3.get_paginator("list_objects_v2")
        downloaded = 0
        for page in paginator.paginate(Bucket=R2_BUCKET):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                local_path = PHOTOS_DIR / key
                if not local_path.exists():
                    s3.download_file(R2_BUCKET, key, str(local_path))
                    downloaded += 1

        if downloaded:
            print(f"  [Sync photos] {downloaded} nouvelles photos téléchargées depuis R2")
        else:
            print(f"  [Sync photos] Toutes les photos sont à jour")
    except Exception as e:
        print(f"  [Sync photos] Erreur (non bloquante) : {e}")


if __name__ == "__main__":
    run_backup()
    sync_photos_from_r2()
