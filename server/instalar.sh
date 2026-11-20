#!/usr/bin/env bash
# Instala Juntadas en la Raspberry Pi.
# Uso (en la terminal de la Pi):
#   curl -fsSL https://raw.githubusercontent.com/arehermann/juntadas/main/server/instalar.sh | bash
set -euo pipefail

REPO="${JUNTADAS_REPO:-arehermann/juntadas}"
RAW="https://raw.githubusercontent.com/$REPO/main/server"
DIR="$HOME/juntadas"
CONF="$HOME/.juntadas.conf"
USER_NAME="$(id -un)"

echo "== 1/4  Bajando los archivos de Juntadas =="
mkdir -p "$DIR"
curl -fsSL "$RAW/juntadas_server.py" -o "$DIR/juntadas_server.py"
curl -fsSL "$RAW/tunel.py" -o "$DIR/tunel.py"
curl -fsSL "$RAW/respaldo.py" -o "$DIR/respaldo.py"
curl -fsSL "$RAW/actualizar.sh" -o "$DIR/actualizar.sh"
chmod +x "$DIR/actualizar.sh"

echo "== 2/4  Instalando cloudflared (el túnel) =="
if ! command -v cloudflared >/dev/null 2>&1; then
  case "$(dpkg --print-architecture)" in
    arm64) ARCH=arm64 ;;
    armhf) ARCH=armhf ;;
    amd64) ARCH=amd64 ;;
    *) echo "No conozco esta arquitectura: $(dpkg --print-architecture)"; exit 1 ;;
  esac
  curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$ARCH.deb" -o /tmp/cloudflared.deb
  sudo dpkg -i /tmp/cloudflared.deb
  rm -f /tmp/cloudflared.deb
else
  echo "Ya estaba instalado."
fi

echo "== 3/4  Llave de GitHub =="
if [ -f "$CONF" ] && grep -q '^GITHUB_TOKEN=.\+' "$CONF"; then
  echo "Ya hay una llave guardada en $CONF (si querés cambiarla, borrá ese archivo y corré esto de nuevo)."
else
  echo "Pegá la llave (token) de GitHub y apretá Enter. No se va a ver mientras la pegás:"
  read -rs TOKEN < /dev/tty
  echo
  umask 077
  printf 'GITHUB_TOKEN=%s\nGITHUB_REPO=%s\n' "$TOKEN" "$REPO" > "$CONF"
  chmod 600 "$CONF"
fi
if ! grep -q '^BACKUP_REPO=' "$CONF"; then
  printf 'BACKUP_REPO=%s/juntadas-datos\n' "${REPO%%/*}" >> "$CONF"
  echo "Las copias de los datos se van a subir al repo PRIVADO ${REPO%%/*}/juntadas-datos."
fi

# Puerto: el guardado en la config, o el primero libre desde 8797 (otro programa puede usar 8787)
PORT="$(grep '^PUERTO=' "$CONF" | cut -d= -f2)"
port_busy(){ ss -ltn "sport = :$1" 2>/dev/null | grep -q LISTEN; }
if [ -z "$PORT" ] || { port_busy "$PORT" && ! systemctl is-active --quiet juntadas; }; then
  PORT=8797
  while port_busy "$PORT"; do PORT=$((PORT+1)); done
  sed -i '/^PUERTO=/d' "$CONF"; echo "PUERTO=$PORT" >> "$CONF"
fi
echo "Juntadas va a usar el puerto $PORT."

echo "== 4/4  Dejándolo funcionando siempre (también al reiniciar) =="
sudo tee /etc/systemd/system/juntadas.service >/dev/null <<EOF
[Unit]
Description=Juntadas (servidor)
After=network.target

[Service]
User=$USER_NAME
ExecStart=/usr/bin/python3 $DIR/juntadas_server.py --port $PORT --data $HOME/juntadas-datos/datos.json
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/juntadas-tunel.service >/dev/null <<EOF
[Unit]
Description=Juntadas (tunel de Cloudflare)
After=network-online.target juntadas.service
Wants=network-online.target

[Service]
User=$USER_NAME
ExecStart=/usr/bin/python3 $DIR/tunel.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/juntadas-actualizar.service >/dev/null <<EOF
[Unit]
Description=Juntadas (buscar actualizaciones en GitHub)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash $DIR/actualizar.sh $DIR $USER_NAME $REPO
EOF

sudo tee /etc/systemd/system/juntadas-actualizar.timer >/dev/null <<EOF
[Unit]
Description=Juntadas (actualizar cada 10 minutos)

[Timer]
OnBootSec=3min
OnUnitActiveSec=10min

[Install]
WantedBy=timers.target
EOF

SINCE="$(date "+%Y-%m-%d %H:%M:%S")"
sudo systemctl daemon-reload
sudo systemctl enable --now juntadas.service juntadas-tunel.service juntadas-actualizar.timer
sudo systemctl restart juntadas.service juntadas-tunel.service

echo
echo "Esperando que el túnel arranque..."
for i in $(seq 1 30); do
  URL="$(sudo journalctl -u juntadas-tunel --since "$SINCE" --no-pager 2>/dev/null | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1 || true)"
  [ -n "$URL" ] && break
  sleep 2
done
if [ -n "${URL:-}" ]; then
  echo "Listo. La Raspberry está en: $URL"
  sleep 2
  if curl -fsS "http://127.0.0.1:$PORT/api/health" | grep -q '"ok"'; then echo "El servidor responde bien."
  else echo "OJO: el servidor no responde. Mirá:  journalctl -u juntadas -n 30"; fi
  echo "La página: https://${REPO%%/*}.github.io/${REPO##*/}/"
else
  echo "El túnel todavía no dio su dirección. Mirá qué pasa con:  journalctl -u juntadas-tunel -f"
fi
