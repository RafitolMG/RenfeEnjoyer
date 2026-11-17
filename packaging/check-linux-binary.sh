#!/bin/bash
# Checks the Linux binary inside another distribution's container (the `linux` CI job).
#
#   docker run --rm -v "$PWD/dist/RenfeEnjoyer-linux:/app/RenfeEnjoyer:ro" \
#     -v "$PWD/packaging:/t:ro" <image> bash /t/check-linux-binary.sh [--browser]
#
# The binary is mounted read-only on purpose: an app the user cannot write next to must
# still start. It has to serve the API and start programs with the system's libraries,
# which "opening the browser" through /bin/sh catches: built on an older distribution,
# the bundle's libraries break newer ones' sh. With --browser (Arch only: needs pacman
# and a container started with --security-opt seccomp=unconfined for Chromium's sandbox),
# it also installs Chromium and runs the bot's own browser check as an ordinary user.
set -u

if [ "${1:-}" = "--browser" ]; then
  pacman -Sy --noconfirm --needed chromium >/dev/null 2>&1 || { echo "No se pudo instalar Chromium"; exit 1; }
  useradd -m check
  su check -c 'cd ~ && XDG_DATA_HOME=~/data timeout 180 /app/RenfeEnjoyer --check-browser'
  exit $?
fi

work=$(mktemp -d)
printf '#!/bin/sh\necho "$1" > %s/opened\n' "$work" > "$work/browser"
chmod +x "$work/browser"
BROWSER=$work/browser XDG_DATA_HOME=$work/data RENFE_PORT=8765 RENFE_ACTIVATION_URL=http://127.0.0.1:9 \
  /app/RenfeEnjoyer > "$work/log" 2>&1 < /dev/null &

# No curl in every image: bash's /dev/tcp is enough for one request.
health=""
for _ in $(seq 120); do
  health=$( (exec 3<>/dev/tcp/127.0.0.1/8765 && printf 'GET /api/health HTTP/1.0\r\n\r\n' >&3 && tail -1 <&3) 2>/dev/null)
  [ -n "$health" ] && break
  sleep 0.5
done
for _ in $(seq 20); do [ -f "$work/opened" ] && break; sleep 0.5; done

glibc=$(ldd --version 2>&1 | head -1 | grep -o '[0-9.]*$')
broken=$(grep -c 'symbol lookup\|error while loading' "$work/log")
echo "glibc $glibc · servidor: ${health:-no responde} · navegador: $([ -f "$work/opened" ] && echo abierto || echo 'no abierto') · errores: $broken"
if [ -z "$health" ] || [ ! -f "$work/opened" ] || [ "$broken" -ne 0 ]; then
  tail -20 "$work/log"
  exit 1
fi
