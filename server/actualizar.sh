#!/usr/bin/env bash
# Actualización automática de Juntadas en la Raspberry.
# La corre un timer de systemd cada 10 minutos: baja los archivos del servidor
# desde la rama main de GitHub y, si cambiaron, los reemplaza y reinicia lo necesario.
# Uso: actualizar.sh <carpeta> <usuario> <repo>
set -euo pipefail
DIR="$1"; OWNER_USER="$2"; REPO="${3:-arehermann/juntadas}"
RAW="https://raw.githubusercontent.com/$REPO/${JUNTADAS_RAMA:-main}/server"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

for f in juntadas_server.py tunel.py respaldo.py actualizar.sh; do
  curl -fsSL --max-time 60 "$RAW/$f?t=$(date +%s)" -o "$TMP/$f"
done
# no instalar nada roto: los .py tienen que compilar y el .sh tener sintaxis válida
python3 -m py_compile "$TMP"/*.py
bash -n "$TMP/actualizar.sh"

changed_server=0; changed_tunnel=0
for f in juntadas_server.py respaldo.py tunel.py actualizar.sh; do
  if ! cmp -s "$TMP/$f" "$DIR/$f"; then
    install -o "$OWNER_USER" -m 755 "$TMP/$f" "$DIR/$f"
    echo "Actualizado: $f"
    case "$f" in
      juntadas_server.py|respaldo.py) changed_server=1 ;;
      tunel.py) changed_tunnel=1 ;;
    esac
  fi
done
if [ "$changed_server" = 1 ]; then systemctl restart juntadas; echo "Servidor reiniciado"; fi
if [ "$changed_tunnel" = 1 ]; then systemctl restart juntadas-tunel; echo "Túnel reiniciado (dirección nueva)"; fi
