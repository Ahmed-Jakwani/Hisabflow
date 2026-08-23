# HisabFlow SaaS Kit — Technical Reference

Deep reference for `odoo_saas_kit` and its companion addon `saas_kit_auto_login`
as deployed on **hisabflow.tech**: architecture, every model and library module
with code references, the provisioning engine, Docker layout, nginx and TLS, the
auto-login crypto, module entitlement, and the failure modes that actually bit us.

Line references are to the repository at the time of writing. The operator-facing
guide is [SAAS_KIT_USER_GUIDE.md](SAAS_KIT_USER_GUIDE.md); the audit history and
rationale for each fix is in [../SAAS_KIT_NOTES.md](../SAAS_KIT_NOTES.md).

> **Origin.** This is Webkul's "ODOO SAAS KIT | ALL IN ONE" (`19.0.1.0.0`),
> heavily patched. Upstream targets much older Odoo versions; a large share of
> the code here exists because Odoo 17→19 changed behaviour underneath it. Treat
> in-code comments explaining a fix as load-bearing documentation — this is not
> stock Webkul code.

---

## 1. Architecture

### 1.1 Runtime topology

```
                          Internet
                             │  :80 / :443
                    ┌────────▼─────────┐
                    │  nginx (on HOST, │   *.hisabflow.tech wildcard vhost
                    │  not in Docker)  │ + map $host $client_port
                    └────────┬─────────┘
        ┌────────────────────┼───────────────────────┬──────────────────┐
        │                    │                       │                  │
   hisabflow.tech   db19-templates.hisabflow.tech  <sub>.hisabflow.tech │
   (odoo-portal)          :8819                      :800x              │
        │                    │                       │                  │
┌───────▼────────┐  ┌────────▼──────────────┐  ┌─────▼──────┐   ┌───────▼──────┐
│ odoo19         │  │ odoo19_template_cont  │  │ client A   │   │ client B     │
│ MANAGER        │  │ ALL plan templates    │  │ container  │   │ container    │
│ network: host  │  │ dbfilter ^template_   │  │ dbfilter=  │   │ dbfilter=    │
│ docker.sock    │  │ bridge network        │  │ its own db │   │ its own db   │
└───────┬────────┘  └────────┬──────────────┘  └─────┬──────┘   └───────┬──────┘
        │                    │                       │                  │
        └────────────────────┴───────────┬───────────┴──────────────────┘
                                         │ 5432 (host-published)
                                ┌────────▼────────┐
                                │ odoo19_db       │
                                │ postgres:16     │
                                └─────────────────┘
```

Key structural facts, each of which surprises people:

- **nginx runs on the host, not in a container.** All nginx work therefore
  happens over SSH from inside the manager container.
- **There is exactly one template container per Odoo version**, not per plan.
  Every plan's template *database* lives inside `odoo19_template_cont`.
- **The manager uses `network_mode: host`** and bind-mounts
  `/var/run/docker.sock` — that is how it creates sibling containers.
- **Client/template containers land on the default `bridge` network**, not the
  compose network, because they're created via the Docker SDK with no network
  argument. They reach Postgres via `host.docker.internal` → the host-published
  `5432`. Intentional, not a bug.

### 1.2 Container inventory

| Container | Image | Role | Ports |
|---|---|---|---|
| `odoo19` | `odoo19-custom:latest` | Manager (runs `odoo_saas_kit`), db `test` | host network |
| `odoo19_db` | `postgres:16` | Shared Postgres for everything | `5432` |
| `odoo19_template_cont` | `odoo:19.0` | Holds **all** plan template DBs | `8819`, `8829` |
| `<subdomain>.hisabflow.tech` | `odoo:19.0` | One per client | two ports from 8000–9000 |
| `portainer` | `portainer-ce` | Unrelated Docker UI | `8000`, `9443` |

### 1.3 Host filesystem layout

| Path | Purpose |
|---|---|
| `/opt/odoo19/docker-compose.yml` | Manager + Postgres |
| `/opt/odoo19/custom-addons` | **git clone** of the repo; manager's addons path |
| `/opt/odoo19/common-addons_v19` | Shared custom addons → `/mnt/extra-addons` in **every** v19 container. **Not a git clone** — populated by `cp` |
| `/opt/odoo19/Odoo-SAAS-Data/` | Per-instance data root (`odoo_saas_data`) |
| `…/Odoo-SAAS-Data/<name>/odoo.conf` | Generated per-container config → `/etc/odoo` |
| `…/Odoo-SAAS-Data/<name>/data-dir/` | Filestore → `/opt/data-dir` |
| `…/Odoo-SAAS-Data/docker_vhosts/` | vhost templates (`vhosttemplatehttp.txt`, `…https.txt`), included by `nginx.conf` |
| `/etc/nginx/conf.d/client-ports.conf` | `map $host $client_port` — **this is how a client becomes reachable** |
| `/etc/nginx/sites-enabled/wildcard-clients.hisabflow.tech` | The single vhost serving every subdomain |
| `/etc/nginx/sites-enabled/odoo-portal` | Main `hisabflow.tech` site (outside this module) |
| `/usr/local/sbin/nginx-deploy-dispatch.sh` | SSH forced-command dispatcher |
| `/usr/local/sbin/nginx-client-map-update.sh` | Edits the port map, then reloads nginx |

---

## 2. Data model

Defined under `odoo_saas_kit/models/`.

| Model | File | Role |
|---|---|---|
| `saas.server` | `saas_server.py` | Deployable target: SSH (`sftp_*`), DB (`db_*`), `host_server` (`self`/`remote`), `max_clients`, `module_installation_limit`, `server_domain` |
| `saas.plan` | `saas_plan.py` | Sellable offering: `saas_module_ids`, billing, `db_template`, state machine |
| `saas.contract` | `contract.py` | Per-customer commercial record; **snapshots** the plan's modules |
| `saas.client` | `saas_client.py` | The running instance: db name, container id, port, state |
| `saas.module` | `module.py` | Catalogue row; `technical_name` is the load-bearing field |
| `saas.module.category` | `module_category.py` | Nested grouping (`_parent_name = "parent_id"`) |
| `saas.module.status` | `saas_module_status.py` | Per-plan / per-client install bookkeeping |
| `custom.domain` | `custom_domain.py` | Customer-owned domains (own vhost + certbot) |

### 2.1 State machines

| Model | States |
|---|---|
| `saas.plan` | `draft` → `confirm` → `cancel` (`saas_plan.py:32`) |
| `saas.contract` | `draft` → `open` → `confirm` → `hold` / `expired` → `cancel` (`contract.py:24`) |
| `saas.client` | `draft` → `started` ⇄ `stopped` → `inactive` → `cancel` (`saas_client.py:32`) |
| `saas.module.status` | `installed` / `uninstalled` (`saas_module_status.py:20`) |

### 2.2 `saas.module.status` — the bookkeeping trap

One row per (module, plan) or (module, client). Historically this could be
**confidently wrong**: rows said `installed` for modules that were absent from
every database. Two causes, both now fixed:

- `create_db_template()` reported success without installing (§4.2);
- `install_modules()` trusted "no exception raised" as proof (§6.1).

Both `client_id` and `plan_id` are now `ondelete='cascade'`
(`saas_module_status.py:27-35`). Before that they defaulted to `SET NULL`, and
131 orphan rows had accumulated — which also made modules undeletable, because
`saas.module.unlink()` (`module.py:27-33`) refuses while *any* status row
references the module, reporting "Delete the linked client first" about a client
that no longer exists.

---

## 3. The provisioning engine (`models/lib/`)

Plain Python, deliberately outside the ORM.

| Module | Responsibility |
|---|---|
| `saas.py` | Dispatcher: `self` → `saas_localhost`, `remote` → `saas_remote` |
| `saas_localhost.py` | The real engine for same-host provisioning |
| `saas_remote.py` | Near-duplicate for remote hosts (docker over `tcp://…:2375` + paramiko) |
| `saas_client_db.py` | erppeek/XML-RPC: create/clone DB, install modules, credentials |
| `install_module.py` | Thin wrapper; re-exports `install_modules` |
| `module_visibility.py` | Module entitlement enforcement |
| `hostnames.py` | Shared hostname construction |
| `client_port_map.py` | Maintains nginx's `client-ports.conf` over SSH |
| `auto_login_token.py` | Mints auto-login tokens (manager side) |
| `containers.py` | start/stop/restart by container id |
| `client.py` | Teardown: DB, container, vhost, data dir |
| `query.py` | Direct Postgres for things XML-RPC can't do |
| `pg_query.py` | `PgQuery` connection helper |
| `generate_ssl_custom_domain.py`, `create_certificate.py` | Custom-domain vhost + certbot |
| `check_connectivity.py`, `check_if_db_accessible.py` | "Test Connection" buttons |
| `find_me_a_port.py` | Standalone script sftp'd to a remote host to find free ports |

### 3.1 Dispatcher

`saas.py` branches on `host_server['server_type']`:
- `main()` → `saas_localhost.main()` or `saas_remote.main()`
- `create_db_template()` → the matching implementation
- For `remote`, `isitaccessible()` opens a paramiko session first and raises if
  unreachable.

### 3.2 Configuration: `models/lib/saas.conf`

Read by `saas_localhost.read_variables()` (`saas_localhost.py:55`),
`auto_login_token.read_secret()` and `install_module.get_port()`.

| Key | Meaning |
|---|---|
| `template_master` | `admin_passwd` of the template container — **also the auto-login signing key for templates** |
| `container_master` | Same, for client containers |
| `container_user` / `container_passwd` | Login/password the provisioned admin user is created with |
| `container_passwd_legacy` | Comma-separated previous passwords (see §7.2) |
| `odoo_image_v19` … `_v12` | Docker image per version |
| `odoo_template_v19` | Template container name (`odoo19_template_cont`) |
| `template_odoo_port_v19` / `_lport_v19` | `8819` / `8829` |
| `common_addons_v19` | `/opt/odoo19/common-addons_v19` |
| `odoo_saas_data`, `data_dir_path`, `nginx_vhosts` | Host paths |
| `nginx_ssh_host/port/user/key` | SSH for nginx work (`host.docker.internal`, `nginx_deploy`) |

> ⚠️ **`saas.conf` and `odoo_saas_kit/odoo.conf` are tracked in git with live
> secrets.** This is a real exposure and it has already caused an incident: a
> rotation of `container_passwd` (commit `ce99f72`) silently severed management
> access to every database provisioned before it (§7.2). Recommended fix: untrack
> both, ship `.example` files, inject real values on the server only.

---

## 4. DB template provisioning

### 4.1 Manager side — `saas_plan.create_db_template()` (`saas_plan.py:423`)

1. Refuse if `db_template` is empty, or already starts with `template_`
   (so it cannot run twice for the same plan).
2. `db_template_name = "template_" + obj.db_template`.
3. `create_status_modules()` (`:340`) creates a status row per plan module;
   `get_installable_modules()` (`:350`) slices to the server's
   `module_installation_limit`.
4. Append `saas_kit_auto_login` — it replaced the legacy `wk_saas_tool`
   `/saas/login` route, which was never ported to 19.0.
5. Call `saas.create_db_template(...)`.
6. On success: write back `db_template`, `state='confirm'`, `container_id`, then
   mark each module `installed` **unless** it came back in `modules_missed`.

> **Fixed here:** a non-dict `result` (the old `"alreadyexists"` string) now
> means *nothing installed*, not *everything installed*. The old code defaulted
> `modules_missed` to `[]`, which is precisely how both live templates ended up
> all-green with none of their modules present.

### 4.2 Engine side — `saas_localhost.create_db_template()` (`saas_localhost.py:569`)

- `host_domain = hostnames.db_template_host(version, server_domain)` (`:581`) →
  `db19-templates.hisabflow.tech`.
- **Container is built only `if not is_container_available(...)`** (`:587`).
  In practice `odoo19_template_cont` exists, so the whole config block is
  skipped — **an existing template container silently lacks fixes this code
  "applies"**. That is why `data_dir`, `proxy_mode`, `db_maxconn` and `dbfilter`
  all had to be applied to the live container by hand.
- odoo.conf written for a *new* template container (`:593`–`:623`):
  `db_user`, `admin_passwd = template_master`, `db_port`, `db_host`,
  `db_password`, `data_dir`, `server_wide_modules = base,rpc,web,saas_kit_auto_login`,
  `proxy_mode = True`, `db_maxconn = 4`, `dbfilter = ^template_`.
- `containers.run(...)` (`:625`) then `wait_for_http` (`:627`).
- Register the host in the nginx map (`:634`); on success set
  `response['url'] = https://…`.
- **Then, for both the "DB exists" and "DB created" paths**, install the module
  list via `saas_client_db.create_saas_client(operation="install", …,
  common_addons_path=…, entitled_modules=…)`, and restart the shared container so
  newly installed code loads.

> **Consequences worth internalising:**
> **Database per plan template: yes. Container per plan template: no.** The
> restart at the end interrupts *every other plan's* template. And because one
> container has one `/mnt/extra-addons`, **filesystem-level addons isolation
> between plans is architecturally impossible as built** — hence the DB-level
> entitlement mechanism in §6.3.

---

## 5. Client provisioning

### 5.1 Contract → client

- `contract.create_saas_client()` (`contract.py:663`) and
  `mark_confirmed()` (`:478`) both: validate domain uniqueness and `max_clients`,
  create/find the `saas.client`, call `attach_modules()` (`:167`) to build status
  rows **from the contract**, then `client.fetch_client_url()`.
- `query.set_base_url()` sets `web.base.url` to the client's `https://` host —
  without it Odoo emits `http://` links behind the HTTPS proxy.
- `set_user_data()` (`:373`) renames user id 2 to the customer's email and RPCs
  into the client to trigger a real `action_reset_password()`.

> Odoo 19 removed `res_partner.signup_token`; `auth_signup` now uses stateless
> signed tokens keyed per database. The old code wrote a signup token and built a
> link from it, which Odoo rejected as "not valid or expired". Replaced by
> `query.trigger_password_reset()` (`query.py:131`), which makes the *client's own*
> instance send the mail.

### 5.2 Engine side — `saas_localhost.main()` (`saas_localhost.py:435`)

1. `host_domain` must equal the db name.
2. `run_odoo(host_domain, db)` (`:229`) — the container.
3. `shutil.copytree` the template's filestore into the client's data dir.
4. `cloning_db(...)` (`:479`) — XML-RPC `duplicate_database` from the template.
5. **Install the plan's modules** into the fresh client, with entitlement
   enforcement.
6. `client_port_map.update_client_port_map(...)` (`:536`) — nginx.
7. Restart the container once (`:550`), then report
   `response['url'] = https://<host>`.

> **Fixed here:** step 5 used to be the constant
> `{'modules_installation': True, 'modules_missed': []}`, and the `modules`
> argument was read at the top of the function and never used again.
> `saas_client.fetch_client_url()` then marked every module `installed` off that
> constant.

### 5.3 `run_odoo()` — the container spec (`saas_localhost.py:229`)

Two free ports from `find_me_an_available_port_within(8000, 9000)`
(`:110`), then odoo.conf (`:243`–`:270`):

```ini
[options]
addons_path = /usr/lib/python3/dist-packages/odoo/addons,/mnt/extra-addons
logfile = /var/log/odoo/odoo-server.log
dbfilter = <the client's db>          ; pinned to exactly one database
db_user = odoo
admin_passwd = <container_master>     ; also the auto-login signing key
db_host = host.docker.internal
db_port = 5432
db_password = <db_password>
data_dir = /opt/data-dir
server_wide_modules = base,rpc,web,saas_kit_auto_login
proxy_mode = True
db_maxconn = 4
```

Then `containers.run(...)` (`:272`):

| Parameter | Value |
|---|---|
| image | `odoo:19.0` |
| name | the client hostname (== db name) |
| volumes | `<data>/data-dir → /opt/data-dir`, `<data> → /etc/odoo`, `common-addons_v19 → /mnt/extra-addons` |
| ports | `8069→port`, `8071→lport` |
| restart_policy | `unless-stopped` |
| extra_hosts | `host.docker.internal:host-gateway` |
| tty | `True` |

#### Why each non-obvious setting exists

- **`data_dir`** — without it Odoo defaults the filestore into the container's
  anonymous volume instead of the bind mount, so every
  filestore-copy-from-template silently finds nothing. That corrupts the client:
  missing attachments and broken asset bundles (`AssetsLoadingError`, 500s).
- **`server_wide_modules` includes `saas_kit_auto_login`** — its route must be
  reachable *before* Odoo has resolved a database. Essential on the template
  container, which serves many databases.
- **`proxy_mode = True`** — otherwise `X-Forwarded-Proto` is ignored and Odoo
  emits `http://` links behind HTTPS, so browsers flag mixed content.
- **`db_maxconn = 4`** — each container keeps its own idle pool. At Odoo's default
  64, ~14 databases exhausted Postgres `max_connections` ("FATAL: sorry, too many
  clients already"), which in turn made short-lived auto-login tokens expire
  before a stalled request completed. Postgres was also raised to 300.
- **`dbfilter`** — pins a client container to exactly one database.

> ⚠️ **Stale for Odoo 19:** the code publishes `8071` as the "longpolling" port,
> but the `odoo:19.0` image's gevent port is **8072** (visible as an unmapped
> `8072/tcp`). Harmless today because no `workers` is configured, so threaded mode
> serves `/websocket` on the main port. **This must be fixed before enabling
> multi-worker.**

---

## 6. Modules: installation and entitlement

### 6.1 `install_modules()` (`saas_client_db.py:223`)

Two deliberate departures from the original:

**It calls `button_immediate_install`, not erppeek's `client.install()`.**
erppeek presses `button_install` — which only *flags* a module `to install` —
and then relies on `base.module.upgrade.upgrade_module()` to apply it. Both
reload the target registry, which routinely tears down the in-flight XML-RPC
call, so a *successful* install surfaced as a connection error. (This is exactly
why someone hand-patched this on the server.)

**Success is decided by re-reading `ir_module_module`, not by absence of an
exception.** "No exception" never proved anything: a module whose files are
missing has no `ir_module_module` row at all, and a registry-reload disconnect
looks like failure even on success. `_module_states_with_retry()` (`:207`) retries
through the reload; if state genuinely can't be read, the modules are reported
**missed** rather than assumed installed.

Supporting pieces:
- `module_states()` (`:189`) uses `execute('ir.module.module', 'search_read', …)`.
  **Not** erppeek's `client.read(model, domain, 'state')` — with a single field
  that returns a *flat list of values* (`['uninstalled']`), not dicts, so you
  cannot tell which value belongs to which module. An earlier fix got this wrong
  and would have marked **every** module failed.
- `update_module_list()` (`:160`) — see below.

### 6.2 The `update_list()` quirk

`ir.module.module.update_list()` is what makes newly-added module files visible,
and what **re-creates a row that entitlement enforcement removed**.

erppeek's `Model.update_list()` sends no positional arguments, but Odoo's XML-RPC
entry point begins:

```python
ids, args = args[0], args[1:]      # odoo/service/model.py, call_kw
```

…so it dies with `IndexError: list index out of range` before `update_list()` ever
runs. Passing an explicit empty ids list works. Databases have been observed to
disagree about which form they accept, so `update_module_list()` tries both.

**Why this mattered more than it looks:** with `update_list` broken, a module
could be hidden by entitlement enforcement and then **never un-hidden**.

### 6.3 Entitlement — `module_visibility.py`

There was previously **no entitlement mechanism at all**: no `ir.module.module`
reference, no `ir.rule`, `uninstall_module()` was a bare `pass`, and every
container mounts the same addons directory. `saas_module_ids` was a provisioning
wishlist.

`enforce_module_visibility(client, entitled_modules, common_addons_path)`
(`module_visibility.py:80`):

1. `list_custom_modules()` (`:57`) enumerates directories containing
   `__manifest__.py` in the shared addons dir. **Only these are ever considered**,
   so core Odoo modules are never touched. An unreadable path returns an empty
   set — "change nothing", not "hide everything".
2. Candidates = custom modules − entitled.
3. Only rows in `REMOVABLE_STATES = ('uninstalled', 'uninstallable')` are acted
   on. Installed modules are left strictly alone — necessary, because entitled
   modules pull in custom dependencies (`hf_basic_b2b_theme` → `mrp`,
   `point_of_sale`; `ica_web_responsive` → `app_common`, `app_odoo_customize`).
   Odoo refuses to unlink installed modules anyway
   (`ir_module.py::_unlink_except_installed`).
4. `unlink` the rows; fall back to `state='uninstallable'` if that fails.
5. Never raises — tightening visibility must not fail an otherwise-successful
   provisioning run.

**Why removal rather than flagging:** Odoo 19 still *lists* `uninstallable`
modules in Apps (greyed, and included in the "Not Installed" filter). It only
hides the Activate button (`invisible="state != 'uninstalled'"` in
`base/views/ir_module_views.xml`). Only removing the row makes a module genuinely
invisible.

**Limitation:** `Apps > Update Apps List` inside a client re-creates the rows.
This is database-level enforcement, not filesystem isolation. Airtight isolation
requires a per-plan addons directory, which requires one template container per
plan.

### 6.4 Propagation

| Action | Plan | Contract | Template | Clients |
|---|---|---|---|---|
| Edit `saas_module_ids` directly | ✅ | ✅ (`sync_contract_modules`) | ❌ | ❌ |
| **Add Module** wizard | ✅ | ✅ | ✅ | ✅ (`started` only) |
| **Reconcile Modules** | — | — | ✅ | ✅ (`started` only) |

- `sync_contract_modules()` (`saas_plan.py:775`), called from `write()` (`:585`).
  A contract snapshots the plan's modules once at creation
  (`contract_creation_wizard.py:206`, `sale.py:94`) and nothing refreshed it.
  Because `attach_modules()` builds from the **contract**, a module added to a
  plan never reached clients created afterwards.
- `add_module_to_plan_wizard.py` now names `stopped`/`inactive` clients it cannot
  reach instead of skipping them silently.
- `reconcile_modules()` (`saas_plan.py:685`) — install missing, enforce
  entitlement, then `_write_status_from_states()` (`:763`) rewrites bookkeeping
  from what the databases actually report. This is the repair tool for anything
  provisioned before these fixes.

---

## 7. Credentials and authentication

### 7.1 Who holds what

| Secret | Where | Used for |
|---|---|---|
| `template_master` | `saas.conf` + template container's `admin_passwd` | DB ops on the template container; **signs template auto-login tokens** |
| `container_master` | `saas.conf` + each client's `admin_passwd` | DB ops on clients; **signs client auto-login tokens** |
| `container_user` / `container_passwd` | `saas.conf` | The provisioned admin user's credentials |
| manager `admin_passwd` | manager odoo.conf | Gate for the View Credentials wizard (`odoo.service.db.check_super`) |

### 7.2 Credential rotation — a real trap

Rotating `container_passwd` **does not re-key databases already provisioned** —
their `res_users` row still holds the hash of whatever was current at creation.
Commit `ce99f72` rotated it, and as a result one plan template and **both live
clients** were unreachable to every management RPC: the per-client Install
button, `install_remaining_modules()` and the Add Module wizard all failed with a
bare "Connection Failure".

Compounding it, the login was taken from `container_user` — but `set_user_data()`
renames user id 2 to the customer's own email, so `container_user` is **never** a
valid login on a real client.

Fixed by:
- `candidate_passwords()` (`saas_client_db.py:108`) — `container_passwd` plus an
  optional comma-separated `container_passwd_legacy`;
- `try_connect()` (`:132`) — single-shot connect, because the existing
  `connect_db()` retries five times with 4s sleeps and is useless as a probe;
- `connect_admin()` (`:141`) — tries each password in turn;
- the login read from the target DB via `query.get_credentials()`
  (`saas_plan._admin_login_for`, `saas_plan.py:628`).

> **Operational rule:** never rotate `container_passwd` without appending the old
> value to `container_passwd_legacy`.

### 7.3 Auto-login: how the Login button works

Odoo's own `tools.hash_sign()` derives its key from the database's own
`database.secret`, so a token signed by the manager's database can never be
verified in a client's. Hence a custom scheme.

**Mint** — `auto_login_token.build_token()` (`auto_login_token.py:27`):

```python
payload = {"db": db, "uid": uid, "exp": int(time.time()) + ttl_seconds}
message = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
sig     = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
token   = base64.urlsafe_b64encode(message).rstrip("=") + "." + sig
```

`uid` defaults to 2; TTL defaults to **600s** (120s was too thin against real
internet latency plus two redirects — a validly signed token was observed
expiring by ~16s).

**Consume** — `saas_kit_auto_login/controllers/main.py`, route
`/saas_kit/auto_login/<string:token>`, `auth='none'`, `csrf=False`:

1. `ensure_db()` reads `?db=`, pins the session, and **redirects back to the same
   URL** (that's the `302` you see in the log). The `?db=` is load-bearing: the
   template container serves many databases, so Odoo must be told which one
   *before* routing.
2. `secret = get_signing_secret()` — see the critical note below.
3. `verify_token()` checks HMAC (`hmac.compare_digest`), `db`, and `exp`.
4. On success: set `pre_login`/`pre_uid` and `request.session.finalize(env)` —
   the same primitive `odoo.http.Session.finalize` uses.
5. Redirect to `/odoo`. **On any failure it redirects to `/web/login?db=…`** —
   which is exactly what "the Login button doesn't work" looks like from outside.

Entry points: `saas_plan.login_to_db_template()` (`saas_plan.py:270`, signs with
`template_master`) and `saas_client.login_to_client_instance()`
(`saas_client.py:162`, signs with `container_master`).

#### ⚠️ The Odoo 19 bug this scheme walked into

Odoo 19's `verify_admin_password()` (`odoo/tools/config.py:1037-1048`):

```python
result, updated_hash = crypt_context.verify_and_update(password, stored_hash)
if result:
    if updated_hash:
        self.options['admin_passwd'] = updated_hash   # ← plaintext becomes a pbkdf2 hash
```

The first time **any** database-manager operation authenticates — i.e. anything
through `db.dispatch` other than `db_exist`/`list`/`list_lang`/`server_version`,
which includes `create_database`, `duplicate_database` and `drop`, all of which
the SaaS Kit does constantly — the in-memory `admin_passwd` is replaced by a
hash.

The addon was reading its secret from `tools.config.get('admin_passwd')`. So from
that moment the container signs with the hash while the manager signs with the
plaintext: **every token is rejected until the container happens to restart**,
which reloads the plaintext from disk.

**This was proven, not inferred.** Restart → auto-login WORKS → one
`db.list_countries(master_passwd)` call → auto-login FAILS, with no restart, no
config change and no expiry in between.

It also retroactively explains the long-standing "flaky first login", where a
correctly signed, non-expired token was rejected and then accepted after a
restart. The post-setup container restarts in `run_odoo()` and
`create_db_template()` were masking it — which is why clients mostly worked and
the long-lived shared template container did not.

**Fix** — `get_signing_secret()` (`saas_kit_auto_login/tools.py`) reads
`admin_passwd` from `tools.config.rcfile`, i.e. **the config file on disk**, which
`verify_and_update` never rewrites, falling back to the in-memory value.

**Security properties.** The payload is base64, not encrypted — anyone holding
the URL can read it. The token is **not** single-use (no nonce/replay store) and
the route is not rate-limited, so a captured URL is replayable for up to the TTL.
It grants a full superuser session. Treat these URLs as bearer credentials.

---

## 8. Networking, nginx and TLS

### 8.1 The wildcard architecture

Per-client vhost files are obsolete. One vhost serves everything:

```nginx
server {                       # :80
    server_name ~^(?<sub>.+)\.hisabflow\.tech$;
    return 301 https://$host$request_uri;
}
server {                       # :443
    server_name ~^(?<sub>.+)\.hisabflow\.tech$;
    ssl_certificate /etc/letsencrypt/live/hisabflow.tech/fullchain.pem;
    location / {
        proxy_pass http://127.0.0.1:$client_port;
        proxy_set_header X-Forwarded-Proto $scheme;
        ...
    }
}
```

backed by `/etc/nginx/conf.d/client-ports.conf`:

```nginx
map $host $client_port {
    db19-templates.hisabflow.tech    8819;
    demo.hisabflow.tech              8003;
    audite2e.hisabflow.tech          8005;
    default                          8069;
}
```

**Adding a client is one line in that map.** No per-client vhost, no per-client
certificate.

#### ⚠️ Why per-client vhosts had to go

`nginx.conf` includes `docker_vhosts/*.conf`. Those generated files listen on
`:80` with an **exact** `server_name`, and **nginx prefers an exact
`server_name` match over a regex one** — so they shadowed the wildcard vhost's
`301 → https` block. Every client was reachable over plain HTTP with no redirect
(verified: an `http://` request returned Odoo's own 303 directly). Generation was
removed; the map update reloads nginx itself.

### 8.2 Hostname construction — `hostnames.py`

```python
def db_template_host(version, base_domain):
    return "db{}-templates.{}".format(str(version).split('.', 1)[0], base_domain)
```

Note the **hyphen**. It used to be `db19_templates.<domain>`, and the underscore
broke two things:

1. **TLS.** An underscore is not a legal DNS label character, so a strict client
   refuses to wildcard-match it against `*.hisabflow.tech` — proven directly: the
   hyphenated form validates against the very same certificate on the very same
   nginx, the underscored form does not.
2. **Its own nginx registration.** `nginx-client-map-update.sh` validates the
   hostname against a strict DNS regex that **rejects underscores**, so
   `create_db_template()` could never register the template host at all — the live
   map entry had been hand-added.

Kept in one shared helper so the manager side (URL) and the provisioning side
(map entry) cannot drift.

### 8.3 Reaching nginx from inside the container

nginx is on the host, so `nginx_vhost.execute_on_host_via_ssh()`
(`saas_localhost.py:381`) and `client_port_map.update_client_port_map()` connect
over SSH as `nginx_deploy` using `nginx_ssh_key`.

That key is locked down by an `authorized_keys` **forced command**. It can run
only two literal actions, dispatched from `$SSH_ORIGINAL_COMMAND`:

```bash
case "${SSH_ORIGINAL_COMMAND:-}" in
  "sudo nginx -t && sudo nginx -s reload") ... ;;
  update-map\ *) read -r _ HOST PORT <<< "$SSH_ORIGINAL_COMMAND"
                 sudo /usr/local/sbin/nginx-client-map-update.sh "$HOST" "$PORT" ;;
  *) echo "command not permitted" >&2; exit 1 ;;
esac
```

> **Gotcha:** because it is forced-command-only, **SFTP does not work** — the
> first implementation failed with `SSHException('EOF during negotiation')`. Hence
> the `update-map` action instead of writing the file directly.

`nginx-client-map-update.sh` validates hostname and port with strict regexes,
`sed`s the map, then `nginx -t && nginx -s reload`.

### 8.4 Certificates

| Cert name | Covers | Used by |
|---|---|---|
| `hisabflow.tech` | `*.hisabflow.tech` | wildcard-clients vhost — **every client and the template host** |
| `hisabflow.tech-0001` | `hisabflow.tech`, `www` | `odoo-portal` (the apex site) |
| `portal.hisabflow.tech` | apex + portal + www | the `portal.` block that returns 444 |

The wildcard is issued by DNS-01 via the `dns-hostinger` plugin in
`/opt/certbot-venv`, and renewed by a **dedicated root cron**:

```
17 3 * * * /opt/certbot-venv/bin/certbot renew --cert-name hisabflow.tech \
           --no-random-sleep-on-renew -q >> /var/log/letsencrypt/hostinger-renew.log 2>&1
```

with `renew_hook = nginx -t && nginx -s reload` in its renewal conf.

#### Two historical breakages worth remembering

1. **`certbot.timer` calls `/usr/bin/certbot`** (the apt package), but the
   `dns-hostinger` plugin lives only in the venv. Every scheduled renewal of this
   cert failed with "plugin does not appear to be installed" and nobody noticed.
   Hence the dedicated cron using the venv binary.
2. **The plugin's `add_txt_record()` deletes-then-adds `_acme-challenge`** on
   every call — fatal for a cert with *both* `hisabflow.tech` and
   `*.hisabflow.tech` as SANs, because certbot needs two TXT values live at once
   and the second `_perform()` wipes the first. Fixed by reissuing as
   **wildcard-only**. That in turn broke `odoo-portal`, which pointed the apex at
   the now-wildcard-only cert — a wildcard does not cover the bare apex — so those
   blocks were repointed at `hisabflow.tech-0001`.

**Rule: keep the wildcard cert wildcard-only. Never add the apex back as a SAN.**

### 8.5 Custom domains

A separate flow (`generate_ssl_custom_domain.py`, `create_certificate.py`) for
customer-owned domains, which the wildcard cannot cover: it writes a real
per-domain vhost and issues a certificate with certbot. Originally it ran
`certbot`/`nginx` as local subprocesses **inside the manager container**, where
neither binary exists; it is now SSH-based like the rest.

> The custom-domain reload path and the main map path are still two separate
> mechanisms. Worth unifying.

---

## 9. Teardown — `models/lib/client.py`

| Function | Action |
|---|---|
| `drop_db(db, url)` (`:40`) | XML-RPC `db.drop` |
| `drop_container(container_id, host)` (`:70`) | Remove the container |
| `delete_nginx_vhost(domain)` (`:88`) | Remove the vhost file |
| `delete_data_dir(domain)` (`:110`) | `shutil.rmtree` |
| `delete_remote_data_dir(domain, ssh_obj)` (`:100`) | `rm -rf` over SSH |
| `delete_template_filestore(...)` (`:122`) | Remove a template's filestore |
| `reload_nginx()` (`:150`) | Validate + reload |
| `main(...)` (`:211`) | Orchestrates client teardown |
| `main_plan(...)` (`:253`) | Template teardown |

`saas_client.unlink()` (`saas_client.py:262`) requires `state == 'cancel'` for
every record, then removes container → drops DB → deletes the data directory.
The client's line in `client-ports.conf` is **deliberately left behind** — it is
harmless and is overwritten if the subdomain is reused.

---

## 10. Direct Postgres access — `query.py` / `pg_query.py`

For things XML-RPC can't or shouldn't do. All go through `PgQuery`
(`pg_query.py`), which connects with the `db_server` details from
`saas.server.get_server_details()`.

| Function | Purpose |
|---|---|
| `is_db_exist` (`:21`) | Database existence |
| `get_user_count` (`:37`) | Per-user billing |
| `get_arrear_users` (`:54`) | Arrears billing |
| `get_credentials` (`:70`) | **user id 2's login** (and its hash — never usable for auth) |
| `update_user` (`:87`) | Write `res_users` / `res_partner` for the customer |
| `trigger_password_reset` (`:131`) | RPC into the client to send a real reset mail |
| `set_user_limt` (`:166`) | Per-user pricing limits into `ir_config_parameter` |
| `set_base_url` (`:189`) | `UPDATE ir_config_parameter … WHERE key='web.base.url'` |
| `set_contract_expiry` (`:210`) | Expiry flag into the client |
| `drop_database` (`:224`) | Drop |

> `set_base_url` is a raw UPDATE — it is a **no-op if the row doesn't exist**, and
> it does not set `web.base.url.freeze`, so Odoo may overwrite it on next login.

---

## 11. Scheduled jobs

| Cron | Interval | Method |
|---|---|---|
| Saas Client Creation Cron | 1 hour | `contract.client_creation_cron_action()` (`contract.py:206`) |
| SaaS Recurring Invoice Cron | 1 day | `create_recurring_invoice()` (`:944`) |
| SaaS Contract Expiry Cron | 1 day | `check_contract_expiry()` (`:186`) |
| Renew Mail cron | 1 day | `renew_mail_cron_action()` (`:225`) |

> A container without a `dbfilter` will run **these crons against every database
> it can see**, including the manager's. The template container was doing exactly
> that, failing with `KeyError: 'saas.contract'` because `odoo_saas_kit` isn't on
> its addons path. Fixed with `dbfilter = ^template_`.

---

## 12. Failure modes reference

| Symptom | Root cause | Where |
|---|---|---|
| Login button → plain login page | Secret became a hash in memory | §7.3 |
| Login works after restart, stops later | Same | §7.3 |
| Template modules all "installed" but absent | `alreadyexists` short-circuit + optimistic marking | §4 |
| Client has only template's modules | `main()` discarded `modules` | §5.2 |
| Install "succeeds", module absent | erppeek `install()` + no verification | §6.1 |
| Every module reported failed | `client.read(…,'state')` returns flat list | §6.1 |
| Granting a hidden module fails | `update_list()` IndexError | §6.2 |
| "Connection Failure" on Install/Add Module | `container_passwd` rotated | §7.2 |
| Client served over plain HTTP | Exact-match vhost shadowing the wildcard | §8.1 |
| Template host cert error | Underscore in hostname | §8.2 |
| Manager crons failing in template container | No `dbfilter` | §11 |
| Missing attachments / `AssetsLoadingError` | `data_dir` not set → filestore in anonymous volume | §5.3 |
| "too many clients already" | `db_maxconn` default 64 × N containers | §5.3 |
| Module never discovered, no error | Addons dir unreadable by container `odoo` user | §13 |
| "Delete the linked client first" | Orphaned `saas.module.status` rows | §2.2 |
| Wildcard cert renewal silently failing | Plugin not on `certbot.timer`'s path | §8.4 |

---

## 13. Deploying module files

**Nothing in Odoo puts module files on the server.** The addons directory that
client containers mount is not a git clone.

```bash
cd /opt/odoo19/custom-addons && git pull
cp -a /opt/odoo19/custom-addons/<module> /opt/odoo19/common-addons_v19/
chmod -R a+rX /opt/odoo19/common-addons_v19/<module>      # do not skip
```

> ⚠️ **The `chmod` is mandatory.** Files extracted as `700`/owner-only give the
> container's `odoo` user a silent `Permission denied` scanning the directory —
> the module is simply never discovered, with **no error surfaced anywhere
> obvious**. This has bitten before (`om_account_accountant`).

Transitive dependencies must be copied too; a module whose dependency is missing
is logged as `not installable, skipped`.

After copying, `update_list()` must run before the module is installable — that
is why `install_modules()` calls it first (§6.2). A container restart alone does
**not** repopulate `ir_module_module`.

---

## 14. Development workflow

1. Edit **locally** only.
2. Commit and push to GitHub.
3. On the server: `cd /opt/odoo19/custom-addons && git pull`.
4. For addons that client containers use, also copy to `common-addons_v19` and
   `chmod` (§13).
5. Restart affected containers; for schema changes, upgrade the module
   (`odoo shell` → `button_immediate_upgrade()`).

> **Never hand-edit the server clone.** It has happened, and it produced real
> divergence: `install_modules()` was rewritten in place and
> `auto_login_token.py`'s TTL changed, so a `git pull` refused outright. Recovery
> required diffing every change, folding the good parts into commits, and
> verifying `saas.conf` byte-for-byte before reverting it.

---

## 15. Known limitations

1. **One template container for all plans.** Restarting for one plan interrupts
   every other plan's template, and per-plan filesystem isolation is impossible.
   Fixing this properly means one template container per plan (new ports, hosts
   and map entries per plan).
2. **Entitlement is database-level.** `Update Apps List` inside a client undoes
   it; re-run **Reconcile Modules**.
3. **Secrets are committed to git** (`saas.conf`, `odoo.conf`). Already caused
   the rotation incident in §7.2.
4. **Auto-login tokens are replayable** within their TTL and are not rate-limited.
5. **`technical_name` is unvalidated free text** — no uniqueness, no existence
   check.
6. **No uninstall path.** `uninstall_module()` is `pass`; removing a module from a
   plan is blocked once installed.
7. **`saas_remote.py` is a near-duplicate** of `saas_localhost.py`. Fixes must be
   applied twice, and the remote path is far less exercised.
8. **Longpolling port is `8071`, should be `8072`** for Odoo 19 (§5.3).
9. **Custom-domain SSL uses a different nginx mechanism** from the main flow.
