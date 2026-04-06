#!/bin/bash
# ─────────────────────────────────────────────
#  LANCER LA GESTION DE STOCK — Double-clic !
# ─────────────────────────────────────────────

cd "$(dirname "$0")"

# Tuer l'ancien serveur s'il tourne encore sur le port 5500
OLD_PID=$(lsof -ti tcp:5500 2>/dev/null)
if [ -n "$OLD_PID" ]; then
  echo "→ Arrêt de l'ancien serveur (PID $OLD_PID)..."
  kill -9 $OLD_PID 2>/dev/null
  sleep 1
fi

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║    TRABELSI — Gestion de Stock           ║"
echo "╠══════════════════════════════════════════╣"
echo "║  Démarrage du serveur...                 ║"
echo "║  Site : http://localhost:5500            ║"
echo "║  Ferme cette fenêtre pour arrêter.       ║"
echo "╚══════════════════════════════════════════╝"
echo ""

python3 app.py
