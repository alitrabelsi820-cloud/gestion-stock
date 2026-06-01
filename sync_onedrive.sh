#!/bin/bash
# Sync automatique vers OneDrive — exclut .git et fichiers temp
ONEDRIVE="/Users/mac/Library/CloudStorage/OneDrive-Personnel(2)/BIjouterie -VF 2/GestionStock"
SOURCE="/Users/mac/Desktop/GestionStock"

rsync -a --delete \
  --exclude='.git' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='dist/' \
  --exclude='build/' \
  --exclude='photos_compressed/' \
  --exclude='data_backup*' \
  --exclude='*.db-shm' \
  --exclude='*.db-wal' \
  "$SOURCE/" "$ONEDRIVE/"

echo "[$(date '+%d/%m/%Y %H:%M')] ✅ Sync OneDrive OK"
