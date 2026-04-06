"""
compress_photos.py — Compresse les photos PNG en JPEG 85%
Les originaux ne sont PAS modifiés.
Résultat dans : /Users/mac/Desktop/GestionStock/photos_compressed/
"""

from pathlib import Path
from PIL import Image
import sys

SOURCE_DIR = Path("/Users/mac/Library/CloudStorage/OneDrive-Personnel(2)/BIjouterie -VF 2/5-Photos(PNG)-1/")
DEST_DIR   = Path("/Users/mac/Desktop/GestionStock/photos_compressed")
QUALITY    = 85   # 85% = visuellement identique, taille réduite de ~85%

def compress():
    DEST_DIR.mkdir(exist_ok=True)

    photos = sorted(SOURCE_DIR.glob("*.png")) + sorted(SOURCE_DIR.glob("*.PNG"))
    total  = len(photos)
    total_original = 0
    total_compressed = 0
    errors = 0

    print(f"Compression de {total} photos (PNG → JPEG {QUALITY}%)...")
    print(f"Destination : {DEST_DIR}")
    print()

    for i, src in enumerate(photos, 1):
        dest = DEST_DIR / (src.stem + ".jpg")

        # Sauter si déjà compressé
        if dest.exists():
            sys.stdout.write(f"\r  [{i}/{total}] {src.name} — déjà fait, saut")
            sys.stdout.flush()
            continue

        original_size = src.stat().st_size
        total_original += original_size

        try:
            with Image.open(src) as img:
                # Convertir RGBA → RGB si nécessaire (JPEG ne supporte pas la transparence)
                if img.mode in ("RGBA", "P"):
                    bg = Image.new("RGB", img.size, (255, 255, 255))
                    bg.paste(img, mask=img.split()[3] if img.mode == "RGBA" else None)
                    img = bg
                elif img.mode != "RGB":
                    img = img.convert("RGB")

                img.save(dest, "JPEG", quality=QUALITY, optimize=True)

            compressed_size = dest.stat().st_size
            total_compressed += compressed_size
            ratio = (1 - compressed_size / original_size) * 100

            sys.stdout.write(
                f"\r  [{i}/{total}] {src.stem} — "
                f"{original_size/1024/1024:.1f} Mo → {compressed_size/1024:.0f} Ko "
                f"(-{ratio:.0f}%)        "
            )
            sys.stdout.flush()

        except Exception as e:
            errors += 1
            print(f"\n  ERREUR {src.name} : {e}")

    print(f"\n\n{'='*55}")
    print(f"  Photos traitées  : {total - errors}/{total}")
    if errors:
        print(f"  Erreurs          : {errors}")
    if total_original > 0:
        print(f"  Taille originale : {total_original/1024/1024/1024:.2f} Go")
        print(f"  Après compression: {total_compressed/1024/1024:.0f} Mo")
        print(f"  Gain             : {(1 - total_compressed/total_original)*100:.0f}%")
    print(f"  Dossier résultat : {DEST_DIR}")
    print("="*55)

if __name__ == "__main__":
    compress()
