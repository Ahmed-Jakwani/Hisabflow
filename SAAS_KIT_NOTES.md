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
- `add_module_to_plan_wizard.py`: installing a module via RPC only touches the
  DB — a running container's own process needs a restart before the module's
  UI/assets actually show up. Currently just a warning message to the admin,
  not automated.
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
  `hf_basic_b2b_theme`, `ica_web_responsive`, `saas_kit_auto_login`. So any
  SaaS plan module list drawing on modules outside this set can't actually
  install into client/template containers — only these are visible there.
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

## Session Log

- **2026-07-26**: Given SSH access to production server (187.127.125.206) for
  read-only inspection only (no code changes, no server commands beyond
  checking state) at user's request. Read the entire `odoo_saas_kit` module
  and the `saas_kit_auto_login` companion addon end-to-end, then did a
  read-only Docker/nginx/git inventory of the server (see section above).
  This file created.
