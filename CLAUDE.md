# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A bot that automates booking seats on Renfe (Spanish railway) season passes ("abonos") with Selenium. The
user picks a saved profile, a direction and a date; the bot logs into `venta.renfe.com`, opens the pass,
lists that day's trains for the user to choose one, then refreshes the results page until the chosen train
has seats, reserves it, and hands the browser over so the user completes the booking by hand.

There is no separate frontend repo — this is a **monorepo** (the GitHub repo is `RafitolMG/RenfeEnjoyer`; it was
`RenfeEnjoyer-Backend` until 2026-11-17, and GitHub redirects the old URLs):

- `backend/` — FastAPI app wrapping the Selenium bot, SQLite for profiles.
- `frontend/` — Vue 3 + TypeScript SPA (Vite) that launches searches and streams progress.

It was a Tkinter desktop app until the v2 restructure; that version lives on in the `legacy-tkinter` branch and
the `1.0.0` tag. v2 was developed on `ubuntu-dev` and became `main` on 2026-11-17.

## Running & building

```bash
# One-off setup on a machine: venv at the repo root (not backend/), npm ci, `renfe` link in ~/.local/bin.
# Reports missing system packages instead of installing them.
./scripts/install.sh

# Development, two terminals
cd backend && ../.venv/bin/uvicorn app.main:app --reload   # http://127.0.0.1:8000
cd frontend && npm run dev                                 # http://localhost:5173
```

Vite proxies `/api` (HTTP **and** WebSocket) to port 8000, so the SPA is same-origin in dev and CORS only
matters for other setups.

`scripts/serve-tailscale.sh` (installed as the `renfe` command via a symlink in `~/.local/bin`) serves the
built SPA from FastAPI on a single port bound to the machine's Tailscale IP. It rebuilds the SPA when any
source is newer than `dist/index.html`, waits for `/api/health`, opens the MagicDNS URL in the browser when
run from a desktop session, and only opens the browser if the server is already up. It resolves its own path
through `readlink -f` so the symlink works, and traps signals so stopping it never orphans uvicorn. It
advertises the full MagicDNS name, not the IP (which changes when the node re-registers) nor the short name
(which on this machine resolves to its own IPv6 addresses, where the server does not listen). `app.main`
mounts `frontend/dist` at `/` only when that directory exists, after the API routers so the catch-all does
not shadow them; in dev the mount is simply absent.

**Windows ships as a single PyInstaller executable**, not through these scripts. `packaging/renfe-enjoyer.spec`
bundles the built SPA and runs `app.desktop`, which serves on `127.0.0.1` only (no Tailscale), opens the browser
once `/api/health` answers, and only opens the browser if the app is already up. `config.FROZEN` switches the
data directory to `%LOCALAPPDATA%\RenfeEnjoyer`, because the bundle unpacks to a temp dir deleted on exit, and
the SPA to the bundle's copy. PyInstaller cannot cross-compile, so the `.exe` is built by the `windows` CI job,
which also runs the backend tests on Windows, starts the executable and checks it serves the app, then uploads
it as the `RenfeEnjoyer-windows` artifact. **Linux ships the same way since 2.0.5**: the `linux` job builds the
spec on `ubuntu-22.04` (PyInstaller bundles everything but glibc, so building on a newer runner would refuse to
start on older distributions), smoke-tests it and uploads `RenfeEnjoyer-linux`. Frozen on Linux, `DATA_DIR` is
`$XDG_DATA_HOME/RenfeEnjoyer` (`~/.local/share`), and the binary is run from a terminal, whose Ctrl+C stops it.
**On Linux the bundle's `LD_LIBRARY_PATH` must not reach child processes**: PyInstaller points it at the bundled
22.04 libraries, and on Arch even `/bin/sh` then dies (`undefined symbol: rl_trim_arg_from_keyseq`), so the first 2.0.5 build
could not open the browser. `config.system_environment()` strips it from `os.environ` at import when frozen; the
running process is unaffected because the loader reads it only at startup. `RenfeEnjoyer --check-browser`
starts the bot's driver headless and loads a page; both CI jobs run it, and the `linux` job also runs
`packaging/check-linux-binary.sh` in Arch, Fedora, Debian and Ubuntu containers with the binary mounted
read-only (the updater's start-up tidying once crashed the app there; `updater._remove` now never raises).
**HTTPS likewise**: the bundled OpenSSL looks for CA certificates where the build machine keeps them
(`/usr/lib/ssl`), which Arch and Fedora lack, so every request to the activation server and GitHub failed as "no
connection". `config.ca_bundle()` sets `SSL_CERT_FILE` to the system bundle, or certifi's (packaged with Selenium)
on images with none; the container check asks the real activation server.
A pushed `v*` tag runs the `release` job once both builds pass: it checks the tag against `APP_VERSION` and
publishes both files as one GitHub release (`gh release create … --latest`). The landing page
(`activation/app/index.html`) links to `releases/latest/download/<asset>` and the activation server finds builds
by name (`RELEASE_ASSETS`), so the asset names must not change.
pyinstaller-hooks-contrib covers uvicorn's and websockets' dynamic imports and Selenium's data files, but not
Selenium's driver classes: since 4.52 `webdriver.Chrome` resolves through `importlib` in a module `__getattr__`,
so the spec lists `selenium.webdriver.chrome` as hidden imports. Without them the Windows build died on launch
(Python 3.12 evaluates `driver.py`'s `-> webdriver.Chrome` at import); on Python 3.14, which defers
annotations, the same bundle starts and only dies at the first search, so a local Linux build does not show it.

**The executable only runs on computers the activation server approves.** `activation/` is a separate FastAPI
service (own venv, own tests, deployed on Coolify from its `Dockerfile`) that holds the Ed25519 private key and
signs a 7-day license per approved `machine_id` (a SHA-256 of Windows' `MachineGuid`, never the raw value). The
desktop side is `app/activation.py`: it carries only `PUBLIC_KEY`, rechecks with the server at most hourly
(`RECHECK_AFTER`), stores the license in `DATA_DIR/license.json`, and deletes it the moment the server answers
anything but `approved`, so a revocation is not outlived by a saved license. With the server unreachable a valid
license keeps the app working. `POST /api/jobs/current` returns 403 without one, and the SPA swaps the search for
`ActivationPanel`. `config.ACTIVATION_REQUIRED` is `FROZEN`; `RENFE_ACTIVATION=1` can switch it on for a
checkout but nothing switches it off for the bundle. The admin side (`activation/app/auth.py`) is a login
form with a server-side session (only the token's SHA-256 is stored, cookie `HttpOnly`/`SameSite=Strict`, `Secure`
over https via the proxy headers). `ADMIN_PASSWORD` is only the initial password: once changed, an scrypt hash in
`AdminCredential` wins, a change signs out the other sessions, and `python -m app.reset_admin` falls back to the
variable. This is a deterrent, not protection: the source is public and GPL, so it guards the distributed
executable only. Changing the key pair means rebuilding the `.exe`.

**The admin sees who asks and from where.** The server stores the IP of each request and of the last check-in
(`--proxy-headers` makes `request.client` the caller, not Traefik) plus a check-in history (`CheckInRecord`:
one row per address/version change or 10 minutes, kept 90 days). Since 2.0.4 the app also sends
`device_info()` — hostname, OS user, OS version, locale, time zone — on every check and request, and the
activation form says so; older versions simply send none. Locations come from DB-IP's free City Lite database
(`activation/app/geo.py`), downloaded into the data volume and renewed monthly in a background thread, so IPs
never leave the server; they are resolved when the admin page asks, and its CC BY licence is why the admin
credits DB-IP. The deployed SQLite predates these columns and `create_all` never alters tables, so
`db.add_missing_columns()` adds any nullable column a model gained; keep new columns nullable.

**The executable updates itself, but only to versions the admin publishes.** CI turns a `v*` tag into a
GitHub release (and fails if the tag is not `v` + `APP_VERSION`); nothing reaches the apps until the admin's
"Versiones" tab publishes it (`activation/app/releases.py`). Publishing pins the SHA-256 GitHub reports for the
asset (`digest`) of each platform's build, and every `/api/v1/check` answer carries the newest published version
that has a build for the caller's `platform` (apps before 2.0.5 send none and count as Windows), signed with the
license key, with `"kind": "update"` so neither can pass for the other; revoked computers get none. A download
takes the running executable's permission bits, or on Linux the new build would not be executable. The admin can set a
published version as minimum, below which `POST /api/jobs/current` returns 403. On the desktop side
`app/updater.py` downloads to `<exe>.new`, checks hash and size, records `<exe>.new.json` (so a finished download
survives a restart), and the SPA's `UpdateBanner` offers "Actualizar" while no search runs. A file that does not
match its pin is remembered by (version, sha256) and not fetched again until the offer changes; a network
failure is retried at the next check, which "Comprobar de nuevo" (`POST /api/update/check`) forces. Rebuilding a
release under the same version changes its digests behind the pin: the admin flags it (`stale`) and
"Volver a fijar" (`POST /api/v1/admin/releases/{version}/repin`) re-pins in place, even for the minimum version. The handover lives in
`app.desktop`: the server stops (closing job streams first, or the graceful shutdown waits on them), the running
`.exe` is renamed to `.old` — Windows allows renaming a running executable, not overwriting it — the new one starts
with `--after-update` and `PYINSTALLER_RESET_ENVIRONMENT=1` (otherwise a onefile child reuses its parent's unpacked
files, deleted when the parent exits), and the old process exits once `/api/health` reports the new version. If
it never does within `HANDOVER_TIMEOUT`, the old one undoes the swap and serves again. A checkout never updates
(`git pull`). The Windows CI job exercises the real handover and rollback with a second build patched to 0.0.1.

```bash
cd backend  && ../.venv/bin/pytest -q            # single test: pytest tests/test_jobs_api.py::test_name
cd backend  && ../.venv/bin/ruff check . && ../.venv/bin/ruff format --check . && ../.venv/bin/mypy
cd frontend && npm run typecheck && npm run build
.venv/bin/pytest e2e                              # end-to-end, from the repo root (E2E_CHROMIUM=/usr/bin/chromium)
```

**`e2e/` is the end-to-end suite and the release gate** (the `release` job needs it). It drives a real browser
against real processes: the app from source through `e2e/harness/app_server.py` (the real app and its own
`desktop` serve/handover loop; only the Renfe bot, the activation key/URL and the "executable" are stand-ins:
the bot's scenario is the profile's `abono`, and a downloaded build is a file whose first line `E2E:<version>`
says which version the next process starts as), the real activation server from `activation/` with test keys,
and `FakeGitHub` serving releases through `GITHUB_API`. `test_binary.py` runs against a built executable
(`E2E_BINARY`, `E2E_BINARY_OLD`, `E2E_NETWORK=1`): it is what would have caught the Linux bundle's library,
certificate and read-only-folder failures. Add a test there for any packaging fix, and one in the source suite
for any feature; a fix without one is a patch.

CI (`.github/workflows/ci.yml`) runs exactly these on push and PR, plus the Windows job above and an
`activation` job (ruff, mypy, pytest in `activation/`, then builds the image and checks it starts).

## Architecture

A search is a **background job**, not a blocking call. That shape is the whole point of the v2 rewrite and
everything else follows from it:

1. `POST /api/jobs/current` validates the request, loads the profile's credentials and calls
   `JobManager.start()`.
2. `JobManager` (`app/bot/runner.py`) spawns one worker thread running `run_search` and returns immediately.
3. The bot reports through a `Reporter`; the manager stamps each event and hands it to the event loop with
   `loop.call_soon_threadsafe`, which fans it out to every WebSocket subscriber.
4. `GET /api/jobs/stream` (WebSocket) replays a snapshot on connect, so a client that joins late — or
   reconnects — still renders the full log.

Key invariants:

- **Only one job at a time.** Each job owns a visible browser window the user must interact with, so
  `start()` raises `JobConflict` (HTTP 409) while another is active.
- **The browser profile is persistent and per-account.** `driver.profile_dir_for()` keys it on a
  hash of the Renfe email under `config.PROFILE_DIR`, passed as `--user-data-dir`, so the usual path
  skips the login entirely and with it the captcha and the code. One shared profile would be a
  correctness bug, not just untidy: `_session_is_active` can only see that *a* session is open, not
  whose, so the bot would search on whichever account logged in last. `_ensure_session` loads
  the passes page and checks whether the URL still holds `myPassesCard.do`: unauthenticated, Renfe
  bounces to its public homepage rather than to the login form, so looking for a login field there
  would never match. Whether Renfe's auth cookie survives a browser restart is unverified — a
  session cookie would not, no matter the profile.
- **Renfe's session lasts 30 minutes from the login, however busy it is.** Measured on two runs:
  the `SSOInfo` cookie expires 30 minutes after the login and is never renewed, not even by
  hundreds of reloads; the first reload after that bounces through `home.do` to
  `www.renfe.com/es/es`. That page has no reserve button, so the bot used to read it as "no
  seats" and reload it forever — the blank window reported as "it goes white after a while".
  `_poll_for_seat` now checks after every reload that the URL still holds `trainFormalization.do`
  and otherwise reopens the search (`_reopen_results`), which may mean a fresh login with its
  captcha and code. A long polling session therefore needs the user every 30 minutes.
- **Whether a profile is logged in is recorded, never inferred.** The bot writes `SESSION_MARKER` into the
  profile when it sees the session work and removes it before logging in again; the SPA's indicator reads
  only that. The profile's contents prove nothing: Chrome fills the directory the moment it opens, and a
  search stalled on the captcha left 21 Renfe cookies behind with no login.
- **Renfe's login is behind reCAPTCHA.** Measured against the live site, an automated session
  scores badly enough to get an image challenge in both headless and headed runs, so the login
  cannot be fully unattended. The bot does not try to solve it: `awaiting_human` hands the window
  over and waits (`HUMAN_STEP_TIMEOUT`) until the challenge is gone, then carries on. A persistent
  Chrome profile would likely raise the score, but that is untested.
- **Three states park the worker on the user instead of failing**, all through the per-job `Interaction`
  bundle that `run_search` receives: `awaiting_code` waits on `interaction.code` until `POST
  /api/jobs/current/code` supplies the verification code; `awaiting_train` waits on `interaction.train`
  until `POST /api/jobs/current/train` picks a listed departure; `reserved` waits on
  `interaction.release`. All are cancellable. A `ValuePrompt` must be opened (`request()`) *before* its
  state is announced, or a client answering instantly is refused with `PromptNotOpen`.
- **The verification code goes through Renfe's two-step modal**, whose ids (`OTP_*` in `renfe.py`) come
  from markup captured on the live site. Validar is a `type="button"` with an `onclick`, so the bot clicks
  it: pressing Enter in the field sends nothing. After each code it waits for Renfe's verdict instead of
  re-prompting straight away; a rejection asks the user again, and running out of attempts clicks
  "Generar código" first. The error labels are hidden before each submit, because one left on screen by
  the previous code would read as the verdict on the next before Renfe has checked it. How the modal
  behaves after "Generar código" is unverified.
- **The job stream is the only source of truth for job state.** Action endpoints return a status,
  but the SPA deliberately ignores it: the worker usually announces the next state before the HTTP
  response lands, and applying the response afterwards rolled the UI back (measured: after choosing a
  train the picker stayed up while the bot was already polling, 3 runs of 3). Starting a job broadcasts
  a fresh `snapshot` so clients drop the previous job's log. `snapshot`, `trains` and `search` messages
  are broadcast without being recorded in the history, since the status they carry is already in any
  replayed snapshot.
- **`reserved` is not a terminal state.** The worker parks on the `release` event until the user confirms
  via `POST /api/jobs/current/release`; only then is the browser closed. This replaces the old
  `input('Presiona Enter...')` that made the app console-only.
- **Cancellation is cooperative.** `cancel` is a `threading.Event` checked at each step, and every sleep goes
  through `_sleep()`, which waits on that event instead of `time.sleep`. Adding a bare `time.sleep` to the
  bot would make "Detener" hang for that long.

## Gotchas specific to this codebase

- **Renfe's DOM ids are hardcoded and brittle.** They live at the top of `app/bot/renfe.py`. Expect breakage
  whenever Renfe changes their markup; that file is where to look first.
  - The pass button's id is `new<abono>` right-padded with spaces, so it is matched with an XPath
    `starts-with`, not an exact id.
  - Results rows are `row<n>`. Only a row with seats carries a reserve button, `continuar<n>`, looked up
    inside the row; a full train shows "Tren completo, sin plazas disponibles en este horario." instead.
  - Renfe writes times as `07.18`, with a dot. `_parse_departure` turns them into the zero-padded
    `07:18` the API and SPA use, and both the listing and the polling go through it so they cannot
    disagree. Before that, the listing read no departures at all and the polling's
    `contains(text(), '07:18')` could never have matched.
  - `list_trains` passes every `td[data-label]` cell that has text through, and the SPA builds its
    columns from them; only `Salida` is relied on. Checked against a captured table: `Avisos` (empty, the
    notice lives in a `title`, so it is dropped), `Salida`, `Llegada`, `Duración` (served already mangled
    as `Duraciï¿½n`, repaired through `LABEL_REPAIRS`), `Tren`, and `Clase` on rows with seats. `Plazas` is
    derived by the bot from the reserve button.
  - One departure can be listed twice under different train numbers, each with its own seats (07.18 as
    `row13003` and `row35003`). It is offered once, with seats if any of its rows has them, and polling
    tries every row with that time. Whether the table lists the whole day at once is still unverified.
  - `_choose_train` waits until a departure is readable, not just for a row. A table that never becomes
    readable fails with its own message and its first rows are dumped to the log; that dump is how the
    dotted times were found.
  - `modalGeneric` being visible means the train filled up between listing and reserving: refresh, don't fail.
  - Clicks go through `_click`, which falls back to a scripted click. `element_to_be_clickable` only
    checks visible-and-enabled, not that the element is on top: measured against the live page, the
    OneTrust banner covers its own reject button and intercepts the native click in roughly 5 of 6 runs.
    In the pre-v2 code that exception was swallowed by a blanket `except Exception: print(e)`, so the bot
    died at the cookie banner and never reached the login form — the "it takes 5-10 tries" symptom.
  - `fill_field` reads the value back after typing and retries, because `send_keys` reports success even
    when the page swallows the keystrokes, which otherwise submits the login form empty.
- **`loginParticular.do` vs `loginCEX.do`**: only the former accepts an email in the `num_tarjeta` field.
  `loginCEX` is the company login and wants a client number.
- **The legacy DB has its columns semantically swapped.** The old `add_user(user, mail, ctr, abono)` wrote
  against `(username, password, email, abono)`, so `password` holds the email and vice versa.
  `scripts/migrate_legacy_db.py` un-swaps them on import. The current schema is correct — do not replicate
  the swap.
- **Renfe passwords are encrypted, not hashed**: the bot has to type them into Renfe's login. The
  `password` column is an `EncryptedString` (`app/credentials.py`, Fernet), so the ORM reads plain text and
  the file holds `enc:v1:…`. The key lives in `credentials.key` next to the database, never in it: sealed
  with DPAPI on Windows (bound to that Windows account), a 0600 file elsewhere. `init_db` encrypts any
  plaintext row left from before. A value the key cannot open reads as `""`, so listing still works and
  only starting a search on that profile asks for the password again (409). `UserRead` deliberately omits
  the password so it never reaches the client, and a blank password in `PATCH /api/users/{id}` means "keep
  the stored one". Tests point `RENFE_KEY_PATH` at a temp file in `conftest.py`.
- **Deleting a profile, or changing its email, removes that account's browser profile** (and with it the
  Renfe session), unless another profile uses the same account: one account can hold several passes. Both
  return 409 while a search runs, since Chrome holds the directory open.
- **`JobManager` is a process-wide singleton**, so tests reset it through the autouse `reset_job_manager`
  fixture. Tests that exercise a job monkeypatch `app.bot.runner.run_search(request, reporter, interaction)`
  — never drive the real Renfe site.
- **Ruff flags `Depends()` in defaults (B008)**, so dependencies use the `SessionDep` alias in `app/api/deps.py`
  rather than `session: Session = Depends(get_session)`.

## Environment notes

- Developed on CachyOS (Arch) with Python 3.14 and Node 26; CI pins Python 3.12 / Node 22.
- `build_chrome_driver()` resolves the browser binary by name (`google-chrome`, `chromium`, …) because
  Selenium only looks for `google-chrome` by default, and falls back to Selenium Manager when no system
  `chromedriver` is on PATH. On Arch both come from the `chromium` and `chromedriver` packages.
- **The server may run outside the desktop session** — `serve-tailscale.sh` launched from a terminal, a
  service, SSH — and a visible browser then has no display and exits on launch (`SessionNotCreatedException:
  Chrome instance exited`). `driver.browser_environment()` points the browser at the user's Wayland socket in
  `XDG_RUNTIME_DIR` when neither `WAYLAND_DISPLAY` nor `DISPLAY` is set. Selenium's `Service(env=...)`
  *replaces* the process environment rather than extending it, so the whole mapping is passed. With no
  desktop session logged in at all there is nothing to open on; that surfaces as `BrowserUnavailable`.
- **Windows has no display lookup and no `os.getuid()`**, so `browser_environment()` returns the environment
  untouched on anything but Linux, and the Wayland-socket tests skip there (no `AF_UNIX` on Windows Python).
  Selenium Manager finds Chrome on Windows by itself; `BROWSER_BINARIES` simply matches nothing.
- **On Windows uvicorn can bind a port another program already holds**, because it sets `SO_REUSEADDR`,
  which there means "share". `tests/test_desktop.py` sets `SO_EXCLUSIVEADDRUSE` on its listener for that
  reason; otherwise the busy-port test starts a server that never returns.
- **Headless is opt-in** (`RENFE_HEADLESS=1`) and only useful for debugging: the user has to see the window
  to finish the purchase.
- System Python on Arch is PEP 668 externally-managed — always install into a venv.
- **The look is a station departure board in Renfe's palette**, dark by default. The colours come from renfe.com's
  own design tokens (`rv-ui-kit`); the app keeps them in `frontend/src/assets/styles.css` and the activation
  server's pages in `activation/app/static/theme.css`, which must stay in step. `--board-*` panels (train list,
  log, the landing's example) are dark in both themes on purpose. The bot's seat labels (`SEATS_FREE`/`SEATS_FULL`)
  are matched by `TrainPicker.vue` to colour the Plazas column.
- `vue-tsc` is incompatible with TypeScript 7 (it requires the `typescript/lib/tsc` subpath, which TS 7
  removed from its exports), so `typescript` is pinned to the 5.x line.
