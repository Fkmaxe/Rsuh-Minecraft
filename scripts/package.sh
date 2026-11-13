#!/usr/bin/env bash
# Produit dist/rush-server/ (+ .tar.gz) : dossier autonome à copier sur la VM, puis sudo ./install.sh
set -euo pipefail
cd "$(dirname "$0")/.."
bash scripts/build.sh -q
OUT=dist/rush-server
rm -rf "$OUT" dist/rush-server.tar.gz
mkdir -p "$OUT/scripts" "$OUT/docs" "$OUT/artifacts"
cp -R orchestrator config "$OUT/"
find "$OUT" -name __pycache__ -prune -exec rm -rf {} +
cp scripts/download.py scripts/analyze_logs.py "$OUT/scripts/"
cp docs/rush.service docs/validation.md "$OUT/docs/"
cp artifacts/rush-paper.jar artifacts/rush-proxy.jar "$OUT/artifacts/"
cp README.md "$OUT/"
cp deploy/install.sh "$OUT/install.sh"
chmod 755 "$OUT/install.sh"
TAR_FLAGS=()
# macOS bsdtar ajoute sinon des attributs Apple que GNU tar signale à l'extraction.
tar --version | grep -q bsdtar && TAR_FLAGS=(--no-xattrs --no-mac-metadata)
COPYFILE_DISABLE=1 tar "${TAR_FLAGS[@]}" -C dist -czf dist/rush-server.tar.gz rush-server
echo "Prêt : dist/rush-server/ et dist/rush-server.tar.gz ($(du -h dist/rush-server.tar.gz | cut -f1))"
