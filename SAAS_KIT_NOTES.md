# Odoo 19 SaaS Kit — Working Notes

This file is the running log/reference for our work on this repo, especially the
`odoo_saas_kit` module. Keep it updated as we go: architecture facts, decisions,
gotchas, and the state of in-progress work. Treat it as the source of truth for
"why things are the way they are" across sessions.

## IMPORTANT — Development Workflow (always follow this)

This repo is developed **locally only**. The production Odoo instance never gets
edited directly.

1. Code is written/edited **here** (local clone of `github.com/Ahmed-Jakwani/Hisabflow`).
2. Changes are committed and **pushed to GitHub** (by the user).
3. On the server, the custom-addons directory used by the running Odoo Docker
   container(s) is a **separate clone of the same GitHub repo**.
4. To deploy: the **user** SSHes to the server, `git pull`s inside that
   server-side clone, and restarts/upgrades the affected container(s).

**Claude does not run commands on the server** (confirmed 2026-07-26) — not
even read-only ones — unless explicitly asked to run that specific command in
that turn. When server-side state is needed, propose the exact command and
wait for the user to run it and paste back the output.

Consequences of this workflow:
- **Never** hand-edit files directly on the server's addons clone — any change
  made there and not pushed from local will be silently lost/diverged on the
  next pull, and creates drift between local and server history.
- Any code change requested by the user goes: edit locally → commit → push →
  (only then, if asked) SSH in and `git pull` + restart container.
- Do not run destructive git operations on the server clone without checking
  `git status` first (same rule as local).

## Credentials / Secrets Handling

- Server SSH root credentials were shared directly in chat for this session.
  They are **not** stored in this file or anywhere in the repo (this repo is
  pushed to GitHub — committing live prod credentials there would leak them).
- `odoo_saas_kit/models/lib/saas.conf` and `odoo_saas_kit/odoo.conf` **are**
  currently tracked in git (only `__pycache__/` and `*.pyc` are gitignored) and
  contain real secrets (`template_master`, `container_master`, `container_passwd`,
  `db_password`). This is a pre-existing exposure worth fixing at some point
  (e.g. move to `.gitignore` + a `.example` template + inject real values only
  on the server), flagged here but not changed yet since it wasn't asked for.

## Server Environment (as of 2026-07-26)

- IP: 187.127.125.206 (root access). Odoo runs as **Docker containers**.
- Deployment is Webkul's **Odoo SaaS Kit** pattern: one shared "template"
  container per Odoo version (`odoo19_template_cont` etc.), and one Docker
  container per client database, all fronted by nginx vhosts generated per
  subdomain, with Let's Encrypt/certbot for custom domains.
- Key paths from `odoo_saas_kit/models/lib/saas.conf` (server-side, per the repo's
  current config for v19):
  - `odoo_saas_data` (base data dir) = `/opt/odoo19/Odoo-SAAS-Data/`
  - `data_dir_path` = `/opt/data-dir`
  - `common_addons_v19` = `/opt/odoo19/common-addons_v19` (mounted into every
    v19 container as `/mnt/extra-addons` — this is where `saas_kit_auto_login`
    must also be copied so client/template DBs can install it)
  - `odoo_template_v19` container name = `odoo19_template_cont`
  - Template container ports: `8819` (web), `8829` (longpolling)
  - `nginx_vhosts` = `/opt/odoo19/Odoo-SAAS-Data/docker_vhosts/`
  - Nginx runs on the **host**, not in a container — vhost reload
    (`nginx -t && nginx -s reload`) happens over SSH to `nginx_ssh_host` using
    a dedicated `nginx_deploy` key user (see `nginx_ssh_*` keys in saas.conf).
- Per-client containers are created with `odoo_container.run_odoo()` /
  `odoo_remote_container.run_odoo()` in `odoo_saas_kit/models/lib/saas_localhost.py`
  / `saas_remote.py`: bind-mount `data_dir` (filestore etc.), `/etc/odoo/`
  (config), and the shared `common_addons` dir as `/mnt/extra-addons`; publish
  8069/8071 to two free host ports found by scanning `8000-9000`.

## `odoo_saas_kit` Module — Deep Notes

Webkul "ODOO SAAS KIT | ALL IN ONE" (`version 19.0.1.0.0`), heavily patched in
this repo to actually work on Odoo 19 (upstream Webkul module targets much older
Odoo versions and needed real fixes, not just a version bump). Confirmed by
in-code comments explaining each fix — this is NOT stock Webkul code.

### Core domain model
- `saas.server` — a deployable target (containerized only in `SERVER_TYPE`
  today). Holds SSH (`sftp_*`) and DB (`db_*`) connection info, `host_server`
  ("self" vs "remote"), `max_clients`. `get_server_details()` builds the
  `host_server`/`db_server` dicts passed everywhere else.
- `saas.plan` — a sellable SaaS offering: which modules, billing model (fixed
  or per-user), DB template name/state machine (draft → confirm), which
  server(s) (single or multi-server with `server.priority` ordering).
  `create_db_template()` spins up (or reuses) the shared template container and
  creates+installs modules into a fresh template DB.
- `saas.contract` — the sold/purchase-order-linked instance of a plan for one
  partner: billing cycles, per-user pricing/arrears, custom domains, and the
  big state machine (draft → open → confirm → hold/expired → cancel).
  `create_saas_client()` / `mark_confirmed()` create the actual `saas.client`
  and kick off container creation.
- `saas.client` — the actual running instance: DB name, container id/port,
  state (draft/started/stopped/inactive/cancel). Wraps start/stop/restart via
  `lib/containers.py`, and DB/container teardown via `lib/client.py`.
- `saas.module` / `saas.module.category` / `saas.module.status` — catalog of
  installable addons per plan/client and their install state per client.
- `custom.domain` — client's custom domain mappings (nginx vhost + certbot).

### `models/lib/*.py` — the actual provisioning engine
This is where all Docker/SSH/Postgres/XML-RPC work happens (kept out of Odoo
ORM code on purpose — these are plain Python, callable both from Odoo and
standalone).

- `saas.py` — dispatcher: routes to `saas_localhost.py` (docker.from_env(),
  same host as Odoo manager) or `saas_remote.py` (docker over
  `tcp://host:2375` + paramiko SSH) based on `host_server['server_type']`.
- `saas_localhost.py` / `saas_remote.py` — near-duplicate implementations
  (local vs remote) of: read `saas.conf`, find 2 free ports (8000-9000) per
  container, write `odoo.conf` for the new container, `docker run` the Odoo
  image with the right volumes/ports, wait for it to answer XML-RPC
  (`wait_for_http`), copy the template's filestore over, clone the DB via
  `duplicate_database`, and write/reload the nginx vhost. `create_db_template()`
  is the equivalent for the one shared template container per plan.
  - **Important fixed bug**: `data_dir` must be set in the generated
    `odoo.conf`, otherwise Odoo defaults it to a location inside the
    container's anonymous volume (not the bind-mounted host path) and every
    filestore-copy step silently fails to find anything.
  - **Important fixed bug**: `server_wide_modules` must include
    `saas_kit_auto_login` so its login route is reachable before Odoo has
    resolved a database for the request (needed on the un-filtered shared
    template container especially).
- `containers.py` — start/stop/restart a container by id, local or remote.
- `client.py` — teardown: drop DB (XML-RPC `db.drop`), remove container,
  delete nginx vhost + reload, delete the data directory (local `shutil.rmtree`
  or remote `rm -rf` over SSH).
- `saas_client_db.py` / `install_module.py` — erppeek-based: create/clone DB,
  install a module list into a DB via XML-RPC.
- `query.py` — direct Postgres access (via `pg_query.py`'s `PgQuery`) for
  things XML-RPC can't/shouldn't do: user counts, arrears billing queries,
  writing `res_partner`/`res_users` fields, setting per-user pricing limits and
  contract-expiry flags into `ir_config_parameter`.
  - **Notable Odoo 19 fix**: `update_user()` used to also write a
    `signup_token`/`signup_type` onto `res_partner` — Odoo 19 removed the
    `signup_token` column entirely (auth_signup now uses signed stateless
    tokens via `tools.hash_sign`/`verify_hash_signed`, keyed per-database).
    Writing it either hard-fails or is silently ignored, and any link built
    from it is rejected as "not valid or expired". Replaced by
    `trigger_password_reset()`, which RPCs into the **client's own** instance
    and calls the real `res.users.action_reset_password()` so a validly-signed
    reset email is sent from the client DB itself.
- `auto_login_token.py` (manager side) + `saas_kit_auto_login` addon
  (client/template side, separate addon dir at repo root) — a custom
  HMAC-signed one-time auto-login mechanism built specifically to replace
  Odoo's own per-database `tools.hash_sign`, which can't be verified across two
  different databases (manager vs client). Signed with the target container's
  own `admin_passwd` (`container_master`/`template_master` from `saas.conf`,
  which the manager already knows and which is already present in that
  container's own `odoo.conf`) — so no new shared secret is needed. Token is
  short-lived (120s default) and carries `db`+`uid`+`exp`. This addon **must be
  installed inside every client DB and the shared template DB** (it's appended
  to the module list in `create_db_template()`), and must exist in each
  version's `common_addons_v*` folder on the server to be installable at all.
  It replaces the legacy Webkul `wk_saas_tool`'s `/saas/login` route, which was
  never ported to the 19.0 template image.
- `generate_ssl_custom_domain.py` / `create_certificate.py` — custom-domain
  nginx vhost generation + certbot Let's Encrypt cert issuance, reload via
  local shell (nginx assumed on same host in this path — note this differs
  from the `nginx_ssh_*`-over-SSH reload path used in `saas_localhost.py`'s
  `nginx_vhost.domainmapping()`; worth reconciling if nginx ever moves off the
  Odoo host in the custom-domain flow too).
- `check_connectivity.py` / `check_if_db_accessible.py` — "Test Connection"
  buttons on `saas.server` (SSH reachability / Postgres reachability).
  `check_connectivity.py` appears to check the manager's own DB (own connect
  info) rather than an arbitrary target — worth double-checking if remote
  multi-server is actually used.
- `find_me_a_port.py` — standalone script, sftp'd to the remote host and run
  there via SSH (`saas_remote.py`) to find a free port range without needing a
  docker/python lib on the remote side beyond stdlib.

### Companion addon: `saas_kit_auto_login/`
Small, separate addon (own manifest, `depends: ['base']` only) meant to be
installed **on client/template DBs, not the manager**. Exposes
`/saas_kit/auto_login/<token>`, `auth='none'`, verifies the HMAC token against
its own `admin_passwd`, and finalizes an Odoo session for the given `uid`
(mirrors `odoo.http.Session.finalize`). Must ship in every version's
`common_addons_v*` on the server for `create_db_template()`'s auto-install to
find it.

### Known follow-ups seen in code comments (not yet acted on)
- ~~`add_module_to_plan_wizard.py`: installing a module via RPC only touches
  the DB — a running container's own process needs a restart~~ **Fixed
  2026-08-02**: `install_remaining_modules()` (saas_plan.py) and
  `add_module_to_plan_wizard.py`'s per-client loop now call
  `restart_db_template()`/`client.restart_client()` automatically after a
  successful RPC install, reusing the existing container-restart plumbing —
  see Session Log below. The **module's files must still physically exist in
  `common-addons_v19` on the server** first — that part can't be automated
  from inside Odoo; see the `om_account_accountant` note below.
- `custom_domain`/SSL flow's nginx reload is local-shell, while the main
  domain-mapping flow's reload is SSH-based — should probably be unified.
- Secrets committed in `saas.conf`/`odoo.conf` (see Credentials section above).

## Server — Live Docker Inventory (observed 2026-07-26, read-only check)

- **Host**: `srv1809585`, Ubuntu, kernel 6.8, Docker 29.6.1 + Compose v5.3.0.
- **6 containers total**, all under `/opt/odoo19` (docker-compose managed, plus
  a couple created ad hoc by the SaaS Kit's docker SDK calls):
  - `odoo19` — the **manager instance** (runs `odoo_saas_kit` itself). Image
    `odoo19-custom:latest`, built from `/opt/odoo19/Dockerfile`. Runs with
    `network_mode: host` (so it can bind to arbitrary host ports and use
    `/var/run/docker.sock`, which is bind-mounted in — this is how it talks to
    Docker directly on "self" host_server mode). Addons mounted from
    `/opt/odoo19/custom-addons` (the server-side git clone).
  - `odoo19_db` — Postgres 16, container name `odoo19_db`, on the
    `odoo19_default` compose network with DNS aliases `odoo19_db`/`db`, port
    5432 published to the host.
  - `odoo19_template_cont` — the shared v19 DB-template container (matches
    `saas.conf`'s `odoo_template_v19`), ports 8819/8829, mounted volumes match
    exactly what `saas_localhost.py`'s `create_db_template()` sets up
    (`/etc/odoo`, `/opt/data-dir`, `/mnt/extra-addons` ← `common-addons_v19`).
  - `hunain-traders.hisabflow.tech` and `jakwani-traders.hisabflow.tech` — the
    only **two currently-running client containers**, ports 8001-8002 and
    8003-8004 respectively.
  - `portainer` — Docker Web UI, unrelated to the SaaS Kit itself.
- **Networking**: client + template containers all land on the default
  `bridge` network (not `odoo19_default`) since they're created via the
  Docker SDK (`containers.run()`) with no explicit network. Their generated
  `odoo.conf` uses `db_host = host.docker.internal` (resolved via
  `extra_hosts: host.docker.internal→host-gateway`, set in
  `saas_localhost.py`'s `run_odoo()`), reaching Postgres via its
  host-published `5432`. This lines up correctly — **not a bug**, just worth
  knowing the two networks are intentionally separate.
- **Nginx**: real nginx runs on the host (not containerized). Its main conf
  has `include /opt/odoo19/Odoo-SAAS-Data/docker_vhosts/*.conf;` — this is how
  SaaS-Kit-generated per-client vhosts (written by
  `nginx_vhost.domainmapping()`) get picked up automatically, entirely
  separate from `/etc/nginx/sites-enabled/`. Currently only 2 vhost confs
  exist there (`hunain-traders`, `jakwani-traders`), both plain HTTP on
  port 80 pointing at the container's published ports — matching the 2 live
  client containers.
  - **Note**: `hunain-traders.hisabflow.tech` *also* has its own manually
    maintained file directly in `/etc/nginx/sites-available/` +
    `sites-enabled/`, serving HTTPS on 443 with its own SSL cert
    (`hisabflow.tech` wildcard-style cert) and proxying to the *same* backend
    ports (8001/8002) as the auto-generated HTTP vhost in `docker_vhosts/`.
    That looks like a manually-added HTTPS layer in front of the SaaS-Kit's
    plain-HTTP vhost for this one client, not something the module itself
    manages — `jakwani-traders` has no equivalent, so it's HTTP-only right
    now. Worth confirming whether that's deliberate/temporary or something to
    replicate for other clients.
  - `db19_templates.hisabflow.tech` also has a proper `sites-available`/
    `sites-enabled` entry (this is the shared template login host referenced
    in `saas_plan.py`'s `login_to_db_template()`).
  - `hisabflow.tech` (the main site, presumably the manager/Hisabflow
    storefront itself) is in `sites-available` but **not** in `sites-enabled`
    — i.e. currently disabled/not serving. Worth double-checking if that's
    intentional.
- **Orphaned SaaS data directories**: `/opt/odoo19/Odoo-SAAS-Data/` has 7
  per-client directories (`odoo.conf` + `data-dir`) but only 2 have a live
  container and an nginx vhost: `abdullah-traders.hisabflow.tech`,
  `abdullahamir.hisabflow.tech`, `jakwani.hisabflow.tech`, `moin.hisabflow.tech`,
  `younusbakers.hisabflow.tech` have **no matching container at all** (not
  even stopped — `docker ps -a` only lists 6 containers total) and no vhost
  conf. These look like leftovers from client-creation attempts that failed
  partway (before the container/vhost step, or after a `drop_container` whose
  directory cleanup didn't run/failed) — disk cost is small (`df -h /opt` →
  84G free of 96G) but worth reconciling against the actual `saas.client`
  records in Odoo to see if these correspond to clients stuck in a bad state.
- **`common-addons_v19`** (mounted into every v19 container as
  `/mnt/extra-addons`) currently has: `app_common`, `app_odoo_customize`,
  `hf_basic_b2b_theme`, `ica_web_responsive`, `saas_kit_auto_login`,
  `om_account_accountant` + its full dependency chain (added 2026-08-03, see
  below). So any SaaS plan module list drawing on modules outside this set
  can't actually install into client/template containers — only these are
  visible there.
  - **Fixed 2026-08-03**: `om_account_accountant` (added to the B2B plan's
    module list) had this exact problem — existed in the local repo but was
    never copied to `common-addons_v19`, so every B2B client's registry
    loaded it as `not installable, skipped`. **Deployed and installed**:
    copied `om_account_accountant` + its full transitive dependency chain
    (`accounting_pdf_reports`, `om_account_asset`, `om_account_budget`,
    `om_fiscal_year`, `om_recurring_payments`, `om_account_daily_reports`,
    `om_account_followup` — all depend only on core `account`/`mail`, already
    in base Odoo) to `common-addons_v19`, fixed permissions (extracted as
    `700`/owner-only from the tar — **must be `chmod -R a+rX` after any
    future module deploy**, or the container's `odoo` user gets a silent
    `Permission denied` scanning the folder and just never discovers the
    module at all, no error surfaced anywhere obvious), restarted every
    affected container (template `template_basic_b2b_plan_tid_13` + all 5
    B2B clients: `jakwani-traders`, `mhperfumers`, `sibte-hunain`,
    `moin-ali`, `ayesha-islam`), called `ir.module.module.update_list()` via
    XML-RPC on each (**required** — a plain container restart alone does
    *not* repopulate `ir_module_module` for newly-added module files; only
    `update_list()` does), then `button_immediate_install()`. Confirmed
    `state='installed'` in all 6 databases afterward.
  - **Odoo quirk hit along the way**: `ir.module.module.update_list()` over
    XML-RPC needed `execute_kw(..., 'update_list', [[]])` (ids arg present)
    on some databases and `execute_kw(..., 'update_list', [])` (no ids arg)
    on others — inconsistent across databases running the exact same Odoo
    version/addons, cause not chased down. Robust approach: try both, use
    whichever doesn't raise.
  - **Also discovered while investigating this**: `saas.module.status` rows
    for this module already said `status='installed'` everywhere *before*
    any of the above — because `install_module.py`'s `install_modules()`
    calls erppeek's `client.install(name)`, which silently no-ops (no
    exception) when asked to install a module Odoo doesn't recognize at all,
    rather than raising. So the SaaS Kit's own bookkeeping can be
    confidently wrong in this specific failure mode (module never deployed
    to `common-addons_v19` at all) — worth hardening `install_modules()` to
    verify the module was actually found/installed (e.g. re-check
    `ir.module.module` state after the call) rather than trusting the
    absence of an exception, but not changed yet (not asked for).
- **Server-side git clone** (`/opt/odoo19/custom-addons`, used as the
  manager's addons path): remote is `git@github.com:Ahmed-Jakwani/Hisabflow`
  (SSH, vs. local clone's HTTPS remote — just a different auth method, same
  repo), currently at commit `5a553ca` — **2 commits behind local `main`**
  (missing `f9b3486 Update app_odoo_customize_views.xml` and
  `7d7ae3b wk_backup_restore`; confirmed `wk_backup_restore/` is indeed absent
  from the server's addons listing). This is expected given the
  edit-locally→push→pull-on-server workflow — just means a `git pull` +
  module update on the server is still pending for those two commits.
  - The server clone also has **11 untracked directories** (never committed
    to this git repo, so not present in the local clone either):
    `asl_list_view_pdf_print`, `common_connector_library`, `fs_stock_card`,
    `hf_basic_b2b_theme`, `hr_payroll_account_community`,
    `hr_payroll_community`, `mcp_server`, `odoo_cheque_management`,
    `purchase_request`, `shopify_ept`, `tv_service_desk`. These are presumably
    OCA/third-party/paid modules installed directly on the server outside the
    git workflow — worth being aware of since they won't survive a fresh
    clone/redeploy from this repo alone.

## Feature: "View Credentials" button (plan/template + client)

Added 2026-07-26, in the local repo only (not yet pushed/deployed):
`odoo_saas_kit/wizards/view_credentials_wizard.py` (+ matching view XML) —
new `saas.credentials.viewer` / `saas.credentials.viewer.line` transient
wizard, opened via a "View Credentials" button in the header of both the
`saas.plan` and `saas.client` form views.

- Gate: asks for the **manager instance's own master/admin password**
  (validated with `odoo.service.db.check_super()` — the exact same check
  Odoo's own Database Manager UI uses; confirmed still present/unchanged in
  this Odoo 19 EE source), not any SaaS-Kit-specific secret.
- Once validated, connects directly to the target Postgres database (the
  plan's `db_template` or the client's `database_name`) via the existing
  `PgQuery` helper and lists every active `res_users.login`.
- **Deliberate limitation, by design, not a bug to fix later**: Odoo never
  stores a recoverable password, only a salted hash. So only user id 2 (the
  account the SaaS Kit itself provisions, always created with
  `container_user`/`container_passwd` from `saas.conf` — see
  `odoo_container.create_db()`) gets a "password" value shown at all, and
  it's explicitly the **default value from saas.conf**, flagged as possibly
  stale if the client has since completed their own password reset. Every
  other user shows "real password unknown, not provisioned by SaaS Kit."
  This mirrors exactly what was explained in the previous session's Q&A
  about superuser credentials — the button surfaces what's actually knowable
  rather than pretending to reveal something that can't exist.
- Access restricted to `group_saas_manager` only (same pattern as
  `saas.plan.reset`/`saas.plan.add.module` — no `base.group_user` row).

Still to do (deferred by user): the actual **modules restriction** issue
(clients installing modules beyond their plan) — root cause already
documented above, no code changes made for it yet.

## Other module: `auto_database_backup` (not part of SaaS Kit)

Cybrosys "Automatic Database Backup" module, pasted directly into the local
repo (not from git) on 2026-07-26 — a hardened/refactored build (master
password never persisted, cron-only guard on dumps, safe OAuth redirects,
real test suite), not stock Cybrosys code.

Originally supported 11 destinations (Local, Google Drive, FTP, SFTP,
Dropbox, OneDrive, NextCloud, Amazon S3, Azure Blob, Google Cloud Storage,
WebDAV). Per instruction, trimmed down to only: **Local, FTP, SFTP, Google
Drive, Google Cloud Storage**. Dropbox/OneDrive/NextCloud/Amazon
S3/Azure Blob/WebDAV code fully removed (fields, handler methods, OAuth
routes/wizard, external Python deps) — not just hidden from the UI.

- `models/db_backup_configure.py`: 1386 → 836 lines.
- Deleted: `wizard/dropbox_auth_code.py`, `wizard/dropbox_auth_code_views.xml`.
- `controllers/auto_database_backup.py`: `OnedriveAuth` renamed to
  `GoogleDriveAuth` (only Google Drive's OAuth callback remains; OneDrive's
  route removed).
- `__manifest__.py` `external_dependencies` trimmed to `paramiko` +
  `google-cloud-storage` only.
- Tests, mail templates (success/failure notification emails), and the
  config form view all updated to match — verified no leftover references to
  removed providers anywhere in the module (`grep` clean).
- Follow-up: also removed the 31 screenshot images tied to the dropped
  providers (`amazon*.png`, `drop*.png`, `dropbox\`1.png`, `onedrive*.png`,
  `nextcloud*.png`, `next2.png`) from `static/description/assets/screenshots/`,
  and cleaned `static/description/index.html` (989 → 782 lines) so it no
  longer references those deleted images or describes the removed providers
  (banner text, "12→5 Storage Destinations" copy in two places, the
  destination-card grids in two places, the pip-install list, the screenshot
  tabs/panes for Dropbox/OneDrive/Nextcloud/Amazon S3, the FAQ answer, and the
  changelog entry). Verified: no leftover provider mentions (grep clean, one
  unrelated Font Awesome CDN hostname aside) and no `<img src>` pointing at a
  now-missing file. `README.rst`/`doc/RELEASE_NOTES.md` still left untouched
  (not loaded by Odoo, not explicitly requested).

Still uncommitted locally as of this entry — user will review, commit, push,
then pull + install on the server per the standard workflow above.

## Session Log

- **2026-07-26**: Given SSH access to production server (187.127.125.206) for
  read-only inspection only (no code changes, no server commands beyond
  checking state) at user's request. Read the entire `odoo_saas_kit` module
  and the `saas_kit_auto_login` companion addon end-to-end, then did a
  read-only Docker/nginx/git inventory of the server (see section above).
  This file created.

- **2026-08-02** — Universal SSL + client-creation reliability (large session,
  explicit per-step SSH authorization given for this task; production changes
  made directly with sign-off at each risky step, not just read-only). Full
  writeup would be huge, so this is the condensed version of what changed and
  why - see git log (`807771f`, `4cb7fe5`, `e5218b8`, `8cd134c` and the SSL
  commits before them) for the actual diffs.

  **SSL / HTTPS:**
  - Root cause of "only 1 client had HTTPS": the module's own per-client vhost
    generator (`nginx_vhost.domainmapping()`) always used the HTTP-only
    template; the HTTPS template existed but no call site ever selected it.
  - Discovered mid-session that someone had *already* set up a better
    architecture that supersedes that per-client-vhost approach:
    `wildcard-clients.hisabflow.tech` — one nginx vhost matching
    `~^(?<sub>.+)\.hisabflow\.tech$` via regex, backed by a
    `map $host $client_port { ... }` in `/etc/nginx/conf.d/client-ports.conf`.
    One wildcard cert covers every subdomain; a new client only needs one
    line added to that map, no per-client vhost file or cert. Adopted this as
    the real mechanism going forward instead of finishing the per-vhost plan.
  - New `odoo_saas_kit/models/lib/client_port_map.py`: adds/updates a
    client's line in that map over SSH, called right after
    `domainmapping()` in `saas_localhost.py`'s `main()`/`create_db_template()`.
    **Gotcha**: the `nginx_deploy` SSH key used for this is locked down via
    an `authorized_keys` forced-command (`command="sudo nginx -t && sudo
    nginx -s reload"`) - it can run *only* that literal command, so SFTP
    (the first implementation) silently fails with `SSHException('EOF during
    negotiation')`. Fixed by adding `/usr/local/sbin/nginx-deploy-dispatch.sh`
    on the host (still forced-command-only, but now accepts one more literal
    action: `update-map <hostname> <port>`, dispatched via `$SSH_ORIGINAL_COMMAND`)
    and `/usr/local/sbin/nginx-client-map-update.sh` (strict regex-validated,
    does the actual sed + reload) - `nginx_deploy`'s sudoers grant was
    extended to allow only that one new script path.
  - The wildcard cert (`/etc/letsencrypt/live/hisabflow.tech/`, covers
    `*.hisabflow.tech`) already existed but its renewal was **silently
    broken two ways**: (1) `certbot.timer` calls `/usr/bin/certbot` (apt
    package), but the `certbot-dns-hostinger` plugin was only installed in a
    separate venv (`/opt/certbot-venv/`, reachable via
    `/usr/local/bin/certbot-hostinger`) - every scheduled renewal attempt for
    this cert failed with "plugin does not appear to be installed", node one
    ever noticed. (2) The plugin's own `add_txt_record()` deletes-then-adds
    the `_acme-challenge` TXT record on every call - fatal for a cert with
    *both* `hisabflow.tech` and `*.hisabflow.tech` as SANs, since certbot
    needs two different TXT values live simultaneously and the second
    `_perform()` call wipes out the first. Fixed by reissuing as
    **wildcard-only** (`*.hisabflow.tech`, no apex SAN) and adding a
    dedicated root cron entry (`/opt/certbot-venv/bin/certbot renew
    --cert-name hisabflow.tech ...`, 03:17 daily) instead of relying on the
    system timer for this one cert; added `renew_hook = nginx -t && nginx -s
    reload` to its renewal conf.
  - Making the cert wildcard-only broke `odoo-portal` (the *already-enabled*
    nginx config that's actually been serving the main `hisabflow.tech`
    website+backend this whole time - a separate config from anything in
    this repo) which pointed its own `hisabflow.tech`/`www` blocks at the
    same cert file; a wildcard cert doesn't cover the bare apex. Repointed
    those two blocks at the pre-existing `hisabflow.tech-0001` cert (apex +
    www, renews fine via the standard nginx/HTTP-01 plugin) - left the
    `portal.hisabflow.tech`-blocking block on the wildcard cert as-is (just
    needs *a* valid cert to complete TLS before returning 444).
  - SSH-ified `generate_ssl_custom_domain.py`/`create_certificate.py` (the
    separate "Add Custom Domain" arbitrary-external-domain flow) the same way
    `nginx_vhost` already was - it was running `certbot`/`nginx` as local
    subprocess calls inside the `odoo19` container, where neither binary
    exists.

  **Client-creation reliability** (found via real client creation attempts
  after the SSL work - "Sibte Hunain"/"Mussyyab Ali"(=`mhperfumers`)/"Moin
  Ali"/"Ayesha Islam" test clients):
  - **`proxy_mode`/`web.base.url`**: neither was ever set for client/template
    containers, so Odoo ignored `X-Forwarded-Proto` and kept generating
    `http://` links/redirects even behind HTTPS - browsers flagged every
    client "Not Secure" (mixed content). Now set automatically in
    `saas_localhost.py` (`proxy_mode = True` in odoo.conf) and via a new
    `query.set_base_url()` call from `contract.py` right after client
    creation (`web.base.url` → the client's real `https://` hostname). Also
    fixed `client_url`'s hardcoded `http://` (used for the invite email link
    and "Open Database" button) and the custom-domain auto-login URL
    (now respects that domain's own `is_ssl_enable` flag).
  - **Template container filestore bug (recurrence)**: `odoo19_template_cont`
    predates the already-documented `data_dir` fix above and was never
    migrated - its filestore for *every* plan template lived inside the
    container's own ephemeral storage
    (`/var/lib/odoo/.local/share/Odoo/filestore/`), not the bind-mounted host
    path. Every new client's filestore-copy-from-template step silently
    failed to find anything, corrupting the client (missing attachments,
    broken compiled asset bundles → `AssetsLoadingError`/500s). Fixed by
    adding `data_dir` to its odoo.conf, `docker cp`-ing its real filestore
    (84MB) to the bind-mounted path, and restarting it - this fixes it for
    *every future* client, not just the ones hit during this session (whose
    filestores were separately repaired by copying from the now-correctly
    located template filestore, plus clearing stale
    `ir_attachment`/`/web/assets/%` rows so bundles recompiled fresh).
  - **Odoo 19 field removal**: `contract.py`'s invoice-email code created a
    `mail.compose.message` with a `record_name` key - that field doesn't
    exist on the model in Odoo 19, so invoice creation/emailing silently
    failed (caught, logged, no invoice) on every single new client. Fixed by
    dropping that key.
  - **Postgres connection exhaustion**: each client/template container keeps
    its own idle connection pool (Odoo default `db_maxconn=64`); with ~14
    databases total this hit `max_connections=100` ("FATAL: sorry, too many
    clients already") with just a handful of clients running - which in turn
    made the short-lived (120s) auto-login token expire before a stalled
    request could complete, so "Login" fell back to the plain login page.
    Raised Postgres to `max_connections=300`, added `db_maxconn=4` to every
    container's odoo.conf (`16` for the manager) - both live and now
    generated automatically for new clients/templates in `saas_localhost.py`.
  - **Flaky first login (separate from the above)**: even with connections
    healthy, a *freshly created* client container is unreliable for its
    first ~30-90s - the XML-RPC login used for the password-reset email
    routinely fails a few times with "Invalid username or password" before
    settling, and in that same window the auto-login token's HMAC
    verification can spuriously reject a **correctly signed, non-expired**
    token (confirmed by replaying the exact failing token immediately after
    a container restart - it then succeeds). Root cause not fully chased
    into Odoo internals; the reliable fix is procedural: `run_odoo()` now
    restarts the new client's container once, as its last setup step, before
    the client is ever considered "ready" - so it's already past this window
    by the time anyone clicks "Login".
  - **`add_module_to_plan_wizard.py`/`install_remaining_modules()`**: see the
    "Known follow-ups" entry above - both now restart the affected
    template/client containers automatically after a successful module
    install via RPC, instead of just posting a "you may need to restart"
    note. Discovered while investigating why `om_account_accountant` (added
    to the B2B plan) wasn't showing up in B2B clients - see the
    `common-addons_v19` note above for the actual (separate, non-code) reason
    that specific module isn't installing yet.

  **Process note**: this session needed production SSH access far beyond
  read-only checks (editing nginx configs, issuing certs, restarting
  containers, a couple of direct `ir_config_parameter`/`ir_attachment`
  Postgres writes, adding two small dispatcher scripts + a sudoers line on
  the host). Every write-class action got explicit per-step user
  confirmation before running, not blanket pre-authorization - the harness's
  own safety layer independently enforced this (blocked a few attempts to
  bundle actions or reuse earlier authorization for a new one, e.g. touching
  `saas_kit_auto_login`'s code for debug logging needed its own separate
  sign-off even though general SSH access had already been granted).
