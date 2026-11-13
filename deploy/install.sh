#!/usr/bin/env bash
# Installe (ou met à jour) puis démarre le réseau Rush sur une VM Linux.
#   sudo ./install.sh [--accept-eula] [--dir /opt/rush] [--user rush] [--no-service] [--no-start]
# Relancer le script met à jour le code sans toucher à runtime/ (base, clés, réglages locaux).
set -euo pipefail

DIR=/opt/rush
RUSH_USER=rush
ACCEPT_EULA=0
SERVICE=1
START=1
while [ $# -gt 0 ]; do
  case "$1" in
    --accept-eula) ACCEPT_EULA=1 ;;
    --dir) DIR="$2"; shift ;;
    --user) RUSH_USER="$2"; shift ;;
    --no-service) SERVICE=0 ;;
    --no-start) START=0 ;;
    -h|--help) sed -n 2,4p "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Option inconnue : $1" >&2; exit 2 ;;
  esac
  shift
done

SRC="$(cd "$(dirname "$0")" && pwd)"
step() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mErreur : %s\033[0m\n' "$*" >&2; exit 1; }
as_rush() { (cd "$DIR" && runuser -u "$RUSH_USER" -- "$@"); }

[ "$(id -u)" = 0 ] || die "lancer avec sudo"
[ "$(uname -s)" = Linux ] || die "ce script vise Linux"
[ -f "$SRC/orchestrator/cli.py" ] && [ -f "$SRC/artifacts/rush-paper.jar" ] || die "dossier incomplet : utiliser le dossier produit par scripts/package.sh"
[ -d /run/systemd/system ] || { [ "$SERVICE" = 1 ] && echo "systemd absent : installation sans service."; SERVICE=0; }

java_major() { "$1" -version 2>&1 | sed -n 's/.*version "\([0-9]*\).*/\1/p' | head -1; }
find_java() {
  for candidate in "$(command -v java 2>/dev/null || true)" /usr/lib/jvm/*/bin/java; do
    [ -x "$candidate" ] && [ "$(java_major "$candidate")" -ge 21 ] 2>/dev/null && { echo "$candidate"; return; }
  done
  return 1
}

step "Dépendances (Java 21+, Python 3.10+, curl)"
if command -v apt-get >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq python3 curl ca-certificates >/dev/null
  if ! find_java >/dev/null; then
    if apt-cache policy openjdk-21-jre-headless 2>/dev/null | grep -q 'Candidate: [0-9]'; then
      apt-get install -y -qq openjdk-21-jre-headless >/dev/null
    else
      # Debian 12 ne fournit que Java 17 : dépôt officiel Eclipse Temurin.
      apt-get install -y -qq gnupg >/dev/null
      install -d -m 755 /etc/apt/keyrings
      curl -fsSL https://packages.adoptium.net/artifactory/api/gpg/key/public | gpg --dearmor --yes -o /etc/apt/keyrings/adoptium.gpg
      echo "deb [signed-by=/etc/apt/keyrings/adoptium.gpg] https://packages.adoptium.net/artifactory/deb $(. /etc/os-release && echo "$VERSION_CODENAME") main" > /etc/apt/sources.list.d/adoptium.list
      apt-get update -qq
      apt-get install -y -qq temurin-21-jre >/dev/null
    fi
  fi
elif command -v dnf >/dev/null; then
  dnf install -y -q python3 curl
  find_java >/dev/null || dnf install -y -q java-21-openjdk-headless
else
  echo "Gestionnaire de paquets non reconnu : installer Java 21+, Python 3.10+ et curl."
fi
JAVA="$(find_java)" || die "Java 21+ introuvable"
PYTHON="$(command -v python3)" || die "python3 introuvable"
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 10))' || die "Python 3.10+ requis ($("$PYTHON" -V))"
command -v curl >/dev/null || die "curl introuvable"
echo "Java : $JAVA ($(java_major "$JAVA")), $("$PYTHON" -V)"

step "Utilisateur $RUSH_USER et fichiers dans $DIR"
id "$RUSH_USER" >/dev/null 2>&1 || useradd --system --home-dir "$DIR" --no-create-home --shell /usr/sbin/nologin "$RUSH_USER"
if [ "$SERVICE" = 1 ] && systemctl is-active --quiet rush; then
  echo "Arrêt du service pour la mise à jour…"
  systemctl stop rush
fi
install -d -m 755 "$DIR" "$DIR/artifacts"
if [ "$SRC" != "$DIR" ]; then
  rm -rf "$DIR/orchestrator" "$DIR/scripts" "$DIR/config" "$DIR/docs"
  cp -R "$SRC/orchestrator" "$SRC/scripts" "$SRC/config" "$SRC/docs" "$DIR/"
  cp "$SRC/install.sh" "$SRC/README.md" "$DIR/"
  cp "$SRC/artifacts/rush-paper.jar" "$SRC/artifacts/rush-proxy.jar" "$DIR/artifacts/"
fi
chown -R "$RUSH_USER:$RUSH_USER" "$DIR"

step "Téléchargement des serveurs (SHA-256 vérifiés)"
as_rush "$PYTHON" scripts/download.py

step "EULA Minecraft"
EULA_DONE=$("$PYTHON" -c 'import json,sys; print(int(json.load(open(sys.argv[1])).get("eula", False)))' "$DIR/runtime/settings.json" 2>/dev/null || echo 0)
if [ "$EULA_DONE" != 1 ] && [ "$ACCEPT_EULA" != 1 ]; then
  [ -t 0 ] || die "relancer avec --accept-eula après avoir lu https://aka.ms/MinecraftEULA"
  read -r -p "Acceptez-vous l'EULA Minecraft (https://aka.ms/MinecraftEULA) ? [o/N] " answer
  case "$answer" in o|O|oui|y|Y|yes) ;; *) die "EULA refusée" ;; esac
fi
as_rush "$PYTHON" -m orchestrator.cli init --accept-eula

# Réglages propres à cette machine. max_instances n'est calculé qu'à la première installation :
# environ 1,3 Go par partie (1 Go de heap + mémoire native) après 2,5 Go pour système, lobby et proxy.
"$PYTHON" - "$DIR/runtime/settings.json" "$JAVA" <<'EOF'
import json, os, sys
path, java = sys.argv[1], sys.argv[2]
settings = json.load(open(path))
settings['java'] = java
if 'max_instances' not in settings:
    ram_gb = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / 2**30
    settings['max_instances'] = max(1, min(64, int((ram_gb - 2.5) / 1.3)))
    print(f"RAM {ram_gb:.1f} Go : max_instances = {settings['max_instances']} (modifiable dans runtime/settings.json)")
json.dump(settings, open(path, 'w'), indent=2)
EOF
chown "$RUSH_USER:$RUSH_USER" "$DIR/runtime/settings.json"
chmod 600 "$DIR/runtime/settings.json"

step "Commande « rush »"
cat > /usr/local/bin/rush <<EOF
#!/bin/sh
# Raccourci d'administration du réseau Rush installé dans $DIR.
case "\$1" in
  start|stop|restart) exec systemctl "\$1" rush ;;
  logs) exec journalctl -u rush -f ;;
esac
cd "$DIR" || exit 1
if [ "\$(id -un)" = "$RUSH_USER" ]; then exec "$PYTHON" -m orchestrator.cli "\$@"; fi
if [ "\$(id -u)" = 0 ]; then exec runuser -u "$RUSH_USER" -- "$PYTHON" -m orchestrator.cli "\$@"; fi
exec sudo -u "$RUSH_USER" "$PYTHON" -m orchestrator.cli "\$@"
EOF
chmod 755 /usr/local/bin/rush

step "Pare-feu : port 25565/TCP"
if command -v ufw >/dev/null && ufw status | grep -q 'Status: active'; then
  ufw allow 25565/tcp >/dev/null && echo "ufw : 25565/tcp autorisé"
elif command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then
  firewall-cmd --permanent --add-port=25565/tcp >/dev/null && firewall-cmd --reload >/dev/null && echo "firewalld : 25565/tcp autorisé"
else
  echo "Aucun pare-feu local actif. Ouvrir 25565/TCP chez l'hébergeur (groupe de sécurité) si besoin."
fi

if [ "$SERVICE" = 0 ]; then
  step "Installation terminée (sans service)"
  echo "Démarrer au premier plan : cd $DIR && runuser -u $RUSH_USER -- $PYTHON -m orchestrator.cli start --diagnostic"
  exit 0
fi

step "Service systemd"
sed -e "s#^User=.*#User=$RUSH_USER#" -e "s#^WorkingDirectory=.*#WorkingDirectory=$DIR#" \
    -e "s#^ExecStart=.*#ExecStart=$PYTHON -m orchestrator.cli start --diagnostic#" \
    "$DIR/docs/rush.service" > /etc/systemd/system/rush.service
systemctl daemon-reload
systemctl enable --quiet rush
[ "$START" = 1 ] || { echo "Service installé, non démarré : rush start"; exit 0; }
systemctl start rush

echo "Attente du proxy Minecraft (le premier démarrage télécharge et prépare Paper, 1 à 3 min)…"
for _ in $(seq 1 90); do
  # Velocity répond aux pings avant que le lobby soit prêt : exiger les deux.
  if as_rush "$PYTHON" -c '
import sys
from orchestrator.cli import read_settings, request
from orchestrator.protocol import ping
ping("127.0.0.1", read_settings()["proxy_port"])
sys.exit(not any(s["name"] == "lobby" for s in request("/health")["servers"]))' 2>/dev/null; then
    IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    step "Serveur prêt : ${IP:-<IP de la VM>}:25565"
    cat <<EOF
  rush status                     état du réseau
  rush role <pseudo> admin        après la première connexion du joueur
  rush deploy-test --mode 5v5     test de déploiement d'une partie
  rush logs | stop | start | restart
  Logs PvP : $DIR/runtime/servers/rush-*/plugins/Rush/events-*.jsonl
EOF
    exit 0
  fi
  systemctl is-active --quiet rush || die "le service s'est arrêté : journalctl -u rush -n 50, $DIR/runtime/servers/lobby/console.log"
  sleep 2
done
die "proxy injoignable après 3 min : journalctl -u rush -n 50, $DIR/runtime/proxy/console.log"
