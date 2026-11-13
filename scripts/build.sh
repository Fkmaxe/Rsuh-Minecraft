#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .cache artifacts
if command -v mvn >/dev/null; then
  MAVEN=mvn
else
  MAVEN="$PWD/.cache/apache-maven-3.9.9/bin/mvn"
  if [ ! -x "$MAVEN" ]; then
    curl -fL https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/3.9.9/apache-maven-3.9.9-bin.tar.gz -o .cache/maven.tar.gz
    tar -xzf .cache/maven.tar.gz -C .cache
  fi
fi
"$MAVEN" -B -Dmaven.repo.local="$PWD/.cache/m2" package "$@"
cp plugins/paper/target/rush-paper-1.0.0.jar artifacts/rush-paper.jar
cp plugins/proxy/target/rush-proxy-1.0.0.jar artifacts/rush-proxy.jar
