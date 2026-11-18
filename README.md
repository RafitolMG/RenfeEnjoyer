# Renfe Enjoyer

Bot que automatiza la compra de billetes de abono de Renfe. Vigila la página de resultados y,
en cuanto aparece una plaza en el tren que buscas, la reserva y te cede el navegador para que
completes la compra.

Se usa de dos formas: con los **ejecutables** para Windows y Linux, que no necesitan instalar nada
más que Chrome (ver [Ejecutables](#ejecutables-windows-y-linux)), o **desde el código**, que es
como se desarrolla y como se sirve por Tailscale para usarla desde el móvil.

Monorepo con tres piezas:

| Directorio    | Contenido                                                              |
| ------------- | ---------------------------------------------------------------------- |
| `backend/`    | API FastAPI + el bot de Selenium, con SQLite para los perfiles          |
| `frontend/`   | SPA en Vue 3 + TypeScript que lanza y monitoriza las búsquedas          |
| `activation/` | Servidor que aprueba equipos y reparte las actualizaciones              |

## Desde el código

### Requisitos

- Python 3.12+
- Node 20+
- Chromium o Chrome, y `chromedriver` (en Arch: `sudo pacman -S chromium chromedriver`)

### Puesta en marcha

Esto es para Linux. Si solo quieres usar la app, los [ejecutables](#ejecutables-windows-y-linux) no
necesitan nada de esto.

Una sola vez en cada equipo:

```bash
git clone https://github.com/RafitolMG/RenfeEnjoyer.git
cd RenfeEnjoyer
./scripts/install.sh
```

La versión antigua de Tkinter está en la rama `legacy-tkinter`. Si clonaste el repositorio cuando
esta versión vivía en `ubuntu-dev`, cambia a `main` con `git fetch && git switch main`.

`install.sh` instala las dependencias de Python y del frontend y crea el comando `renfe` en
`~/.local/bin`. No instala nada a nivel de sistema: si falta Python, Node, Chromium o Tailscale, lo
dice. Se puede relanzar sin problema.

Los perfiles y las sesiones de Renfe son de cada equipo y no viajan con el repositorio. En uno nuevo
hay que crear los perfiles otra vez (o copiar `backend/renfe_enjoyer.db`), y la primera búsqueda de
cada perfil pasará por el login y el captcha.

A partir de ahí, desde cualquier directorio:

```bash
renfe
```

Recompila el frontend si has cambiado algo, arranca el servidor, espera a que responda y abre la
app en el navegador. Si ya estaba en marcha, solo abre el navegador. `Ctrl+C` lo para todo.

La app queda en `http://<equipo>.<tailnet>.ts.net:8000`, la dirección MagicDNS completa del equipo,
que el comando muestra al arrancar. Usa esa y no la IP, que cambia si el nodo se vuelve a registrar
en Tailscale. El nombre corto (`http://<equipo>:8000`) no sirve desde el propio equipo: ahí resuelve
a sus direcciones IPv6, y el servidor solo escucha en la IPv4 de Tailscale.

### Desarrollo

Con recarga en caliente, en dos terminales. Solo es accesible desde el propio equipo:

```bash
cd backend && ../.venv/bin/uvicorn app.main:app --reload    # terminal 1
cd frontend && npm run dev                                  # terminal 2
```

La interfaz queda en <http://localhost:5173>. El servidor de Vite hace de proxy de `/api` hacia
el backend, así que no hace falta configurar nada más.

### Importar perfiles de la versión antigua

La app de Tkinter guardaba el correo y la contraseña en columnas intercambiadas. Este script los
reordena al esquema nuevo:

```bash
cd backend
PYTHONPATH=. ../.venv/bin/python scripts/migrate_legacy_db.py
```

## Ejecutables (Windows y Linux)

La app también se distribuye como un único archivo por sistema, que no necesita Python, Node ni
Tailscale; solo **Google Chrome** (o Chromium en Linux). El `chromedriver` lo descarga Selenium la
primera vez, así que esa primera búsqueda necesita internet y tarda algo más.

**Descargarlo** desde <https://renfe.rafitolmg.dev>, o la última versión directamente:
[`RenfeEnjoyer.exe`](https://github.com/RafitolMG/RenfeEnjoyer/releases/latest/download/RenfeEnjoyer.exe)
para Windows 10 u 11 y
[`RenfeEnjoyer-linux`](https://github.com/RafitolMG/RenfeEnjoyer/releases/latest/download/RenfeEnjoyer-linux)
para Linux x86_64 (cualquier distribución con glibc 2.35 o posterior: Ubuntu 22.04, Debian 12,
Fedora 36…). No hace falta cuenta de GitHub.

**Usarlo.**

La app se abre en **su propia ventana**, sin pestañas ni barra de direcciones, y **cerrarla
cierra la app**. Si hay una búsqueda en curso, antes de cerrar pregunta, porque se detendría.

- **Windows**: doble clic en `RenfeEnjoyer.exe`. La ventana es nativa (WebView2, que viene con
  Windows 10 y 11), sin consola; lo que antes salía en la consola queda en
  `%LOCALAPPDATA%\RenfeEnjoyer\renfe-enjoyer.log`. Si faltara WebView2, se abre en Edge en modo app.
  El ejecutable no está firmado, así que **SmartScreen avisará** la primera vez: *Más información*
  → *Ejecutar de todas formas*.
- **Linux**:

  ```bash
  chmod +x RenfeEnjoyer-linux
  ./RenfeEnjoyer-linux
  ```

  La ventana es Chrome o Chromium en modo app, con un perfil propio. Desde una terminal, `Ctrl+C`
  también la para.

Si ya estaba abierta, lanzarla otra vez solo trae su ventana al frente (en Linux abre otra). Si el puerto 8000 está
ocupado, elige otro con la variable `RENFE_PORT`.

Si las búsquedas no consiguen abrir el navegador, `RenfeEnjoyer.exe --check-browser` (o
`./RenfeEnjoyer-linux --check-browser`) lo abre sin ventana, carga una página de prueba y dice si
funciona o por qué no.

Diferencias con la versión desde el código:

- **Solo se puede usar desde el propio equipo.** Escucha en `127.0.0.1`, no en Tailscale ni en la
  red local, así que no se abre desde el móvil.
- **Los datos** (perfiles en `renfe_enjoyer.db`, sesiones de Renfe en `browser-profile/`, la clave
  de cifrado y la licencia) **están en `%LOCALAPPDATA%\RenfeEnjoyer`** en Windows y en
  **`~/.local/share/RenfeEnjoyer`** en Linux. Sobreviven a cambiar de versión; para empezar de
  cero, borra esa carpeta.
- **Necesitan activación**: la primera vez muestran una pantalla para solicitar acceso, que el
  administrador aprueba. Ver [Servidor de activación](#servidor-de-activación).

### Actualizaciones

Los ejecutables se actualizan solos, pero solo a versiones que el administrador ha publicado. Cuando
hay una nueva, la app la descarga en segundo plano, comprueba que es exactamente la publicada y
muestra **Actualizar**. Al pulsarlo, siempre sin una búsqueda en marcha, la versión nueva sustituye
a la vieja y la página se recarga sola. Si la nueva no llega a arrancar, la app vuelve sola a la
anterior y lo dice.

Si el administrador marca una **versión mínima**, las anteriores siguen abriéndose pero no dejan
buscar hasta actualizar.

La app busca versiones nuevas cada hora. Si una descarga falla, lo dice en la misma barra:
**Comprobar de nuevo** pregunta al servidor en el acto y **Descargar a mano** baja el ejecutable desde
GitHub.

### Publicar una versión

1. Sube la versión en `backend/app/config.py` (`APP_VERSION`), `backend/pyproject.toml` y
   `frontend/package.json`, y haz commit en `main`.
2. Crea y sube la etiqueta, que tiene que coincidir con `APP_VERSION` o la publicación falla:

   ```bash
   git tag v2.1.0 && git push origin v2.1.0
   ```

   El CI compila los dos ejecutables, los prueba (en Windows, también el relevo de una
   actualización real y su vuelta atrás) y los publica como Release de GitHub, que pasa a ser la
   que descargan los botones de la página de inicio.
3. En el admin, pestaña **Versiones**, pulsa **Publicar** en la nueva. Hasta entonces no se
   ofrece a ninguna app.

En cada push el CI también deja los ejecutables como artefactos en *Actions*
(`RenfeEnjoyer-windows`, `RenfeEnjoyer-linux`), aunque esos piden cuenta y caducan a los 90 días.

### Compilarlos a mano

En un equipo con Python 3.12+ y Node 20+ del mismo sistema que el ejecutable que quieres
(PyInstaller no compila para otro sistema):

```bash
python -m venv .venv
.venv/bin/pip install -e "./backend[build]"            # Windows: .venv\Scripts\pip
(cd frontend && npm ci && npm run build)
.venv/bin/pyinstaller --noconfirm packaging/renfe-enjoyer.spec   # deja dist/RenfeEnjoyer(.exe)
```

## Servidor de activación

`activation/` es un servidor aparte que decide qué equipos pueden usar los ejecutables y les reparte
las actualizaciones. Solo afecta a los ejecutables: quien arranca la app desde el código no pasa por
él (el código es público y GPL, así que la comprobación se podría quitar de todas formas).

Cómo funciona:

- Cada equipo se identifica con un hash del identificador del sistema (`MachineGuid` en Windows,
  `/etc/machine-id` en Linux), nunca con el valor en bruto.
  La app muestra los 12 primeros caracteres como «código de este equipo», los mismos que ve el
  administrador.
- Al aprobarlo, el servidor le entrega una **licencia firmada** (Ed25519) para ese equipo y válida 7
  días. La app la guarda en su carpeta de datos (`license.json`) y la renueva al consultar al servidor,
  como mucho una vez por hora.
- Si el servidor no responde, el equipo sigue funcionando hasta que caduque la licencia. Una
  revocación surte efecto en la siguiente consulta (menos de una hora si el equipo tiene conexión,
  7 días como mucho si no).
- La app solo lleva la **clave pública**, así que no puede fabricar licencias, y una licencia
  copiada a otro equipo no sirve.

Administración: `https://renfe.rafitolmg.dev/admin`, que lleva a una pantalla de login. Tiene dos
pestañas:

- **Equipos**: las solicitudes, con un buscador y filtros por estado, para aprobar, revocar o borrar.
  Un equipo revocado no puede volver a pedir acceso; uno borrado sí. De cada uno muestra la IP y la
  ubicación aproximada desde la que pidió acceso y desde la que se conectó por última vez, y
  **Historial** lista sus conexiones de los últimos 90 días. Desde la 2.0.4 la app envía también el
  nombre del equipo, el usuario, el sistema, el idioma y la zona horaria; el formulario de solicitud
  lo avisa.
- **Versiones**: las releases de GitHub. **Publicar** ofrece una a las apps y fija el SHA-256 de
  cada ejecutable en ese momento, así que un archivo cambiado después en GitHub no se instala.
  **Retirar** deja de ofrecerla, y la **versión mínima** obliga a actualizar a las anteriores. Si una
  release se vuelve a compilar con el mismo número, sus archivos ya no coinciden con lo fijado y la
  pestaña lo avisa: **Volver a fijar** lo corrige sin dejar de ofrecerla.

La ubicación sale de la base gratuita *IP to City Lite* de [DB-IP](https://db-ip.com), que el
servidor descarga en su volumen de datos y renueva cada mes: las IPs no se envían a nadie.

- **Contraseña.** La primera vez es la de `ADMIN_PASSWORD`, y la página avisa hasta que la cambias
  con **Cambiar contraseña** (pide la actual y una nueva de al menos 12 caracteres). Desde entonces
  manda la nueva, guardada como hash `scrypt` en la base de datos, y `ADMIN_PASSWORD` se ignora.
  Cambiarla cierra las demás sesiones abiertas.
- **Sesión.** Dura 12 horas, en una cookie `HttpOnly`, `Secure` y `SameSite=Strict`; en la base de
  datos solo se guarda un hash. **Cerrar sesión** la invalida en el servidor.
- **Intentos fallidos.** Tras 10 en 10 minutos desde la misma dirección, el login se bloquea un rato.
- **Si olvidas la contraseña**, vuelve a la de `ADMIN_PASSWORD` ejecutando en el contenedor (desde
  la terminal de la app en Coolify, o por SSH con `docker exec`):

  ```bash
  python -m app.reset_admin
  ```

### Despliegue en Coolify

1. **DNS**: un registro `A` de `renfe.rafitolmg.dev` a la IP del servidor.
2. **Nueva aplicación** desde este repositorio público, rama `main`, *Build Pack* `Dockerfile`,
   *Base Directory* `/activation`, puerto `8000` y dominio `https://renfe.rafitolmg.dev`.
3. **Volumen persistente** montado en `/data`. Sin él, cada despliegue borra todas las aprobaciones.
   Guarda también la base de ubicaciones de DB-IP, unos 130 MB.
4. **Variables de entorno**, ambas como secretas:
   - `ACTIVATION_PRIVATE_KEY`: la clave privada.
   - `ADMIN_PASSWORD`: la contraseña inicial de admin, que conviene cambiar desde la página.
   Sin cualquiera de las dos, el servidor se niega a arrancar.
5. **Health check** en `/health`.

Las claves se generan con `cd activation && python scripts/generate_keys.py`. La pública va en
`PUBLIC_KEY` de `backend/app/activation.py` y la privada **solo** en Coolify. Cambiar el par invalida
todas las licencias emitidas; los equipos aprobados siguen aprobados y reciben una nueva en la
siguiente consulta, pero solo los `.exe` compilados con la nueva clave pública las aceptan.

Opcionales: `LICENSE_DAYS` (7 por defecto), `MAX_PENDING` (500 solicitudes pendientes como máximo),
`GITHUB_TOKEN` (solo sube el límite de consultas a GitHub de la pestaña Versiones) y
`GEOIP_ENABLED=0` (no descarga la base de ubicaciones).
Para probar la app contra un servidor local: `RENFE_ACTIVATION=1` y `RENFE_ACTIVATION_URL=http://…`.

## Acceso desde el tailnet

`renfe` sirve la API y el frontend en un único puerto, atado a la IP de Tailscale del equipo, así
que la app se puede usar desde el móvil o el portátil sin exponer nada a internet.

Se ata a la IP de Tailscale en concreto, no a `0.0.0.0`, así que **no** queda expuesto en la red
local. Sobrescribe el destino con `RENFE_HOST` y `RENFE_PORT` si lo necesitas.

El bot abre el navegador en el escritorio del equipo, así que **tiene que haber una sesión de
escritorio iniciada** aunque tú lo uses desde otro dispositivo. Si el servidor se arrancó desde una
terminal sin pantalla, el bot localiza la sesión Wayland abierta y abre la ventana ahí.

Advertencias:

- **La API no tiene autenticación.** Cualquiera con acceso a tu tailnet puede leer los perfiles y
  lanzar búsquedas. Vale mientras el tailnet sean solo equipos tuyos.
- **No uses `tailscale funnel`** con esto: publicaría la app en internet.
- Desde el móvil puedes lanzar, seguir y detener una búsqueda, pero **el captcha hay que
  resolverlo en la máquina** donde corre el bot, porque es ahí donde se abre el navegador.

## Sesión persistente

El bot guarda el perfil del navegador, así que **normalmente no inicia sesión**: reutiliza la
sesión anterior y se salta el login, el captcha y la verificación. Solo cuando la sesión caduca
vuelve a pedirte esos pasos.

Hay **un perfil por cuenta de Renfe**, bajo `backend/browser-profile/<hash del correo>/` (en los
ejecutables, en `browser-profile/` dentro de su carpeta de datos). Borrar un perfil de la app, o
cambiarle el correo, borra también esa sesión, salvo que otro perfil use la misma cuenta. Compartir
uno solo haría que el bot buscase con la cuenta que hubiera iniciado sesión la última vez, no con
la del perfil elegido.

La interfaz indica si el perfil seleccionado tiene sesión guardada y permite cerrarla:

```bash
curl -X DELETE http://<host>:8000/api/users/<id>/session
```

Ese directorio contiene tu sesión de Renfe iniciada. Está en `.gitignore`, pero trátalo como una
credencial más.

## Captcha en el inicio de sesión

Renfe protege el login con reCAPTCHA y una sesión automatizada suele recibir un reto de imágenes.
El bot **no intenta resolverlo**: pasa al estado `awaiting_human`, te cede la ventana del navegador
y sigue solo en cuanto lo completes.

Esto implica que el inicio de sesión no es totalmente desatendido, y que si usas la app desde el
móvil puede que necesites acercarte al equipo para ese paso.

## Verificación en dos pasos

Si Renfe pide un código por SMS o correo, el bot **no falla**: se queda esperando en el estado
`awaiting_code` y la interfaz muestra un campo para introducirlo. Funciona igual desde el móvil,
así que puedes contestar la verificación sin estar delante del equipo.

Desde consola, si prefieres:

```bash
curl -X POST http://<host>:8000/api/jobs/current/code \
     -H 'Content-Type: application/json' -d '{"code":"483920"}'
```

El bot escribe el código en el modal «Verificación en dos pasos» de Renfe y pulsa **Validar**.
Si Renfe lo rechaza, vuelve a pedírtelo con un aviso; si se agotan los intentos, pulsa **Generar
código** para que Renfe envíe uno nuevo y te lo pide otra vez.

## Cómo funciona una búsqueda

1. Eliges un perfil, el trayecto (ida o vuelta) y la fecha. El abono ya determina origen y destino.
2. El backend lanza el bot en un hilo aparte: entra en Renfe, abre el abono y te muestra los trenes
   de ese día.
3. Marcas uno y confirmas. El bot recarga la página de resultados hasta que ese tren tenga plaza.
4. El progreso llega a la interfaz por WebSocket, con el contador de recargas y el registro en vivo.
5. Al reservar, el bot deja el navegador abierto para que termines a mano. Cuando acabes, pulsa
   **He terminado, cerrar navegador**.

Si ya sabes a qué hora sale tu tren, la API acepta `departure_time` al lanzar la búsqueda y se salta
el listado:

```bash
curl -X POST http://<host>:8000/api/jobs/current -H 'Content-Type: application/json' \
     -d '{"user_id": 1, "journey_type": "ida", "date": "15/01/2027", "departure_time": "07:30"}'
```

Las columnas del listado salen tal cual de la tabla de Renfe. Solo la de salida está comprobada; el
resto no se ha podido ver aún contra la web real.

Puedes detener la búsqueda en cualquier momento; el bucle de recarga es infinito por diseño.

## Comprobaciones

Las mismas que pasa el CI:

```bash
cd backend    && ../.venv/bin/pytest -q && ../.venv/bin/ruff check . && ../.venv/bin/mypy
cd frontend   && npm run typecheck && npm run build
cd activation && .venv/bin/pytest -q && .venv/bin/ruff check . && .venv/bin/mypy   # su propio venv
```

### Pruebas end-to-end

`e2e/` prueba cada funcionalidad como la usa una persona, con un navegador real (Playwright): la app
desde el código con un bot simulado en lugar de Renfe (nunca toca Renfe), el servidor de
activación real con claves de prueba y un GitHub falso para las releases. Cubre perfiles,
búsquedas completas, activación, el admin, la landing y las actualizaciones con su relevo y su
vuelta atrás.

```bash
.venv/bin/pip install -e './backend[e2e]' && .venv/bin/python -m playwright install chromium
.venv/bin/pytest e2e                      # E2E_CHROMIUM=/usr/bin/chromium usa el del sistema
```

`e2e/test_binary.py` prueba además un ejecutable compilado (`E2E_BINARY=dist/RenfeEnjoyer-linux`):
que abre su ventana sin heredar las librerías del paquete, que conecta por HTTPS
(`E2E_NETWORK=1`), `--check-browser`, una carpeta sin permiso de escritura y, con
`E2E_BINARY_OLD`, el relevo real desde una versión anterior. El CI ejecuta las dos partes y no
publica una release si falla cualquiera.

## Contraseñas

Las contraseñas de Renfe de los perfiles se guardan **cifradas** en el SQLite: el bot necesita
escribirlas en el login de Renfe, así que no pueden guardarse como hash. La clave está en
`credentials.key`, junto a la base de datos y fuera de ella; en Windows queda además sellada con
DPAPI y solo la puede abrir esa cuenta de Windows. Si se pierde la clave, la app sigue funcionando y
solo pide volver a escribir la contraseña del perfil al buscar.

Protege si alguien obtiene solo el `.db`, pero no frente a un programa que se ejecute con tu propio
usuario. Tanto el `.db` como `credentials.key` están en `.gitignore`; no los compartas.
