#!/bin/bash
# build_app.sh — Construit GestionStock.app pour Mac
# Usage : bash build_app.sh

set -e
cd "$(dirname "$0")"

PYINSTALLER="$HOME/Library/Python/3.9/bin/pyinstaller"

echo "======================================"
echo "  Build GestionStock.app"
echo "======================================"

# Nettoyer les anciens builds
rm -rf build dist

# Lancer PyInstaller
"$PYINSTALLER" GestionStock.spec --noconfirm --clean

# Vérifier que le .app existe
if [ -d "dist/GestionStock.app" ]; then
    echo ""
    echo "✓ Build réussi : dist/GestionStock.app"
    echo ""
    echo "Pour installer : copie dist/GestionStock.app dans /Applications"
    echo "Pour lancer    : double-clic sur GestionStock.app"
    echo ""
    # Créer un dossier data vide dans le .app pour les données utilisateur
    DATA_DIR="dist/GestionStock.app/Contents/MacOS/data"
    mkdir -p "$DATA_DIR"
    # Copier la base de données existante si elle existe
    if [ -f "data/gestionstock.db" ]; then
        cp "data/gestionstock.db" "$DATA_DIR/"
        echo "✓ Base de données copiée dans l'app"
    fi
else
    echo "✗ Erreur : GestionStock.app non trouvé dans dist/"
    exit 1
fi
