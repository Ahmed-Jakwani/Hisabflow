# HisabFlow SaaS Kit — Functional / Operator Guide

How to actually run the SaaS Kit: create the catalogue, publish a plan, hand a
customer their own Odoo instance, and add custom modules later — plus the
precautions that stop you corrupting a live customer.

This is the **functional** guide. For internals (Docker, nginx, SSL, the crypto
behind the Login button, code references) see
[SAAS_KIT_TECHNICAL_REFERENCE.md](SAAS_KIT_TECHNICAL_REFERENCE.md).

---

## 1. What the SaaS Kit actually does

Read this before clicking anything — several of the names in the UI suggest
something different from what happens.

| You do this | What actually happens |
|---|---|
| Create a **Module** record | Nothing technical. It's a catalogue row holding a technical name. **It does not put any files on the server.** |
| Create a **Plan** | Nothing technical. A sellable definition: which modules, price, billing cycle, which server. |
| Click **Create DB Template** | A **new database** is created — inside the **shared** `odoo19_template_cont` container. The plan's modules are installed into it. **No new container is created per plan.** |
| Create a **Contract** | A commercial record for one customer. It takes a **snapshot** of the plan's module list. |
| Click **Create & Confirm Client** | A **new Docker container + new database** are created for that customer, on their own subdomain, with HTTPS. The database is cloned from the plan's DB template. |

So: **one shared container holds every plan's template database; every client
gets its own container.**

### The chain

```
Module Category  ─┐
Module           ─┤
                  ├─► SaaS Plan ──► DB Template (database in the shared container)
Product          ─┘        │
                           └─► Contract (per customer) ──► Client (own container + own DB + own subdomain)
```

---

## 2. Prerequisites (one-off, already done on hisabflow.tech)

You don't need to redo these, but you need to know they exist because most
failures trace back to one of them.

1. **A SaaS Server record** — `SaaS KIT > Configuration > SaaS Server`.
   The live one is:
   - Name `Hisabflow`, Server Type `containerized`, Host Server `self`
   - Server Domain `hisabflow.tech` — **client subdomains are built from this**
   - Max Clients `10` — client creation is refused past this
   - Module Installation Limit `20` — caps how many modules install per run
2. **The shared custom-addons directory** on the server:
   `/opt/odoo19/common-addons_v19`. It is mounted into **every** template and
   client container as `/mnt/extra-addons`. **A module that isn't in this
   directory cannot be installed anywhere, no matter what the plan says.**
3. **Wildcard DNS** — `*.hisabflow.tech` resolves to the server, so a new
   subdomain works with no DNS change.
4. **Wildcard TLS certificate** — covers `*.hisabflow.tech`, so a new subdomain
   is HTTPS immediately with no certificate work. Renews by a nightly root cron.

---

## 3. Navigation map

```
SaaS KIT
├── SaaS
│   ├── SaaS Plans
│   ├── Saas Contracts
│   │   ├── Live Contracts
│   │   └── Contracts
│   └── Clients
└── Configuration
    ├── SaaS Server
    ├── Module Categories
    └── Modules
```

---

## 4. Step 1 — Create a Module Category

`SaaS KIT > Configuration > Module Categories > New`

| Field | Notes |
|---|---|
| **Category Name** | Required. |
| Parent Category | Optional. Categories nest; the displayed name becomes `Parent / Child`. |
| Image | Optional, cosmetic. |

Purely for grouping in the UI. Nothing technical depends on it.

---

## 5. Step 2 — Create a Module

`SaaS KIT > Configuration > Modules > New`

| Field | Notes |
|---|---|
| **Name** | Required. Human label, e.g. `Accounting v19`. |
| **Technical Name** | Required. **This is the load-bearing field.** |
| Module Category | Optional grouping. |
| Description / Image | Optional. |

### ⚠️ Technical Name — the single most common source of failure

`Technical Name` must be **exactly** the module's directory name / manifest name,
e.g. `om_account_accountant`, `sale_management`, `point_of_sale`.

It is **free text with no validation** — no uniqueness constraint, and nothing
checks the module exists. A typo looks identical to a deployment problem until
install time.

**Before creating a Module record for a custom module, the module's files must
already be on the server** in `/opt/odoo19/common-addons_v19`. See
[§10 Deploying a brand-new custom module](#10-deploying-a-brand-new-custom-module).

Core Odoo modules (`purchase`, `stock`, `point_of_sale`, `mrp`, …) ship in the
image and need no deployment.

---

## 6. Step 3 — Create a Product

`Sales > Products > Products > New`, or from the plan's **Related Products** tab.

Create the product, then set its **SaaS Plan** field to the plan. The product is
what a customer actually buys; confirming a sale order for it can create the
contract automatically.

For back-office-only provisioning you still need a product, because the contract
uses it for invoicing.

---

## 7. Step 4 — Create a SaaS Plan

`SaaS KIT > SaaS > SaaS Plans > New`

### Required fields (creation is refused without them)

| Field | Notes |
|---|---|
| **Name** | Becomes the database template name — see the warning below. |
| **SaaS Server** | Pick `Hisabflow`. |
| **Base URL** (`saas_base_url`) | `hisabflow.tech`. Client subdomains are built from this. Not nullable. |
| **Billing Criteria** | `Fixed Rate` or `Based on the No. of users`. Not nullable. |
| **Default Billing Cycle** | Must be **≥ 1**. |
| Complimentary Free days | Must be **≥ 0**. |

If Billing Criteria is `Based on the No. of users`, **Min Users**, **Max Users**
and **User Product** also become required.

### ⚠️ The plan Name becomes the database name

`db_template` is computed as `<name lowercased, spaces → underscores>_tid_<id>`,
and on **Create DB Template** it is prefixed with `template_`. So
`HisabFlow Theme B2B` (id 14) → `template_hisabflow_theme_b2b_tid_14`.

**This is computed once and never recomputed.** Renaming the plan afterwards does
not rename the database. Choose the name deliberately.

### Tabs

| Tab | Purpose |
|---|---|
| **Related Modules** | The modules this plan grants. This is the entitlement list. |
| **Installed Modules** | Read-only status per module (`Installed` / `Not Installed`). |
| **Remote Servers** | Only when Multi Server is on. |
| **Related Products** | Products that sell this plan. |
| **Description** | Free text. |

Add your modules to **Related Modules**, then save.

> You do **not** need to add `saas_kit_auto_login` — it is appended
> automatically and is always entitled. It's what makes the **Login** button work.

---

## 8. Step 5 — Create the DB Template

On the saved plan (state `Draft`), click **Create DB Template**.

What happens:
1. The template database is created in the shared `odoo19_template_cont`
   container, named `template_<plan>_tid_<id>`.
2. Every module in **Related Modules** is installed into it, plus
   `saas_kit_auto_login`.
3. Custom modules the plan is **not** entitled to are removed from that
   database's Apps list.
4. The host `db19-templates.hisabflow.tech` is registered in nginx.
5. The shared template container is restarted so newly installed modules load.
6. The plan moves to **Confirmed**.

### ⚠️ Precautions

- **This restarts the shared template container**, which briefly interrupts
  *every other plan's* template. Harmless for customers (clients have their own
  containers) but don't do it in a demo to someone.
- **Takes minutes**, not seconds — installing modules runs Odoo migrations.
- **Check the result.** Open the **Installed Modules** tab. Anything showing
  `Not Installed` genuinely failed — the most likely cause is that the module's
  files aren't in `common-addons_v19`. A warning is also posted in the chatter
  listing exactly which modules failed.
- **Once confirmed, the plan's Name, Server, Base URL and Related Modules become
  read-only.** Use **Add Module** to add modules afterwards.
- **Skip This Step** (`force_confirm`) confirms the plan *without* creating a
  template. Only use it when you are deliberately reusing an existing template
  database.

### Verify it worked

Click **Login**. A new tab should open already logged into the template
database. Then check `Apps` — you should see all standard Odoo apps plus **only**
your plan's custom modules.

---

## 9. Step 6 — Contract, then Client

### 9a. Create the Contract

From the confirmed plan, click **Create Contract**, or let a confirmed sale
order create it.

In the wizard, set the **Customer** and the **subdomain**.

#### ⚠️ Subdomain rules

The subdomain becomes both the hostname and the database name, e.g. `demo` →
`demo.hisabflow.tech`.

- Lowercase letters, digits and hyphens only.
- **No underscores.** An underscore is not legal in a hostname and the TLS
  certificate will not match it — the site will show a certificate error. (This
  is not theoretical: the template host itself had this bug.)
- Must be unique across all contracts and custom domains — checked, and refused.

If you don't know the customer's preferred subdomain, use **Ask from customer**
to email them a form.

The contract **snapshots** the plan's module list at this point.

### 9b. Create the Client

On the contract, click **Create & Confirm Client**.

What happens:
1. Domain uniqueness and the server's Max Clients limit are checked.
2. Two free host ports are found (scanning 8000–9000).
3. A per-client `odoo.conf` is written (pinned to that one database).
4. **A new Docker container is started** from `odoo:19.0`.
5. The template's filestore is copied, then the template database is cloned.
6. The plan's modules are installed (a no-op if the template already had them).
7. Non-entitled custom modules are removed from the client's Apps.
8. The subdomain is registered in nginx → HTTPS works immediately.
9. The container is restarted once, then the client is marked **Started**.
10. `web.base.url` is set to the client's `https://` address.
11. The customer is emailed a password-reset link so they set their own password.

This takes **several minutes**. Don't click twice.

### Verify

On the client record: **State** = `Started`, **Client URL** = `https://…`.
Click **Login** — you should land in the customer's instance as admin.

Then confirm in a browser that `https://<subdomain>.hisabflow.tech` loads with a
valid padlock, and that `http://` redirects to `https://`.

### Client controls

| Button | Effect |
|---|---|
| **Login** | Opens the client instance as admin, no password needed. |
| **View Credentials** | Lists the instance's users. See the caveat in §12. |
| **Start / Stop / Restart** | Container lifecycle. **Stop takes the customer offline.** |
| **Create Client Instance** | Retry provisioning if it failed partway. |
| **Inactive Client** → **Drop DB** / **Drop Container** | Teardown. Destructive. |
| **Cancel** | Required before a client can be deleted. |

---

## 10. Deploying a brand-new custom module

**Nothing in the Odoo UI can put module files on the server.** This part is
manual and must be done *before* the module is any use.

1. Commit and push the module to the GitHub repo.
2. On the server, pull into the addons clone:
   ```
   cd /opt/odoo19/custom-addons && git pull
   ```
3. Copy it into the shared directory that client containers actually mount:
   ```
   cp -a /opt/odoo19/custom-addons/<module> /opt/odoo19/common-addons_v19/
   ```
4. **Fix permissions — do not skip this:**
   ```
   chmod -R a+rX /opt/odoo19/common-addons_v19/<module>
   ```
   Without it the container's `odoo` user silently cannot read the directory and
   simply never discovers the module. **No error appears anywhere obvious.**
5. Copy its dependencies too, if they aren't core Odoo. A module whose
   dependency is missing is silently skipped as "not installable".
6. Now create the **Module** record (§5) and add it to the plan (§11).

---

## 11. Adding a module to a live plan

Once a plan is Confirmed, **Related Modules is read-only**. Use the
**Add Module** button.

It will:
1. Add the module to the plan.
2. **Sync the plan's contracts** so clients created later inherit it.
3. Install it into the plan's DB template and restart that container.
4. Install it into every **Started** client and restart each one.

### ⚠️ Precautions

- **Editing the module list any other way propagates nothing.** Only this button
  pushes to template and clients.
- **Stopped / Inactive clients are skipped** — they can't be reached over RPC.
  The wizard now *names* them in the plan's chatter. Start them, then use the
  **Install** button on that client's SaaS Modules tab.
- **Each affected client is restarted** — a brief interruption for that customer.
- The module's files must already be in `common-addons_v19` (§10).
- If it reports a failure, read the plan's chatter: it lists exactly which
  clients failed and why.

### Repairing drift — "Reconcile Modules"

Use this when things are already inconsistent (a template built before a fix, a
client that missed a rollout, or bookkeeping you don't trust).

For the plan's template and every Started client it will:
- install any entitled module that's missing,
- remove non-entitled custom modules from Apps,
- and **rewrite the Installed Modules bookkeeping from what the databases
  actually report** — rather than from an assumption that installs worked.

It's safe to run repeatedly and posts a per-target summary in the chatter.

---

## 12. Module visibility — what customers can and cannot see

Every client sees:
- **All standard Odoo 19 Community apps** (from the image), plus
- **Only the custom modules in their plan** and whatever those pull in as
  dependencies.

### Things that will confuse you if you don't know them

- **Dependencies come along, and that is correct.** `hf_basic_b2b_theme`
  depends on `mrp` and `point_of_sale`, so those appear installed even though
  they may not be listed in the plan. `ica_web_responsive` pulls in
  `app_common` / `app_odoo_customize`. This is not drift.
- **Non-entitled custom modules are removed from the Apps list**, so they can't
  be found or installed.
- **A customer running `Apps > Update Apps List` re-exposes them.** Enforcement
  is at the database level, not the filesystem — every container mounts the same
  addons directory. If you need airtight isolation, that requires one template
  container per plan (not built yet). Re-run **Reconcile Modules** to re-hide.
- **Removing a module from a plan does not uninstall it** from anywhere. The kit
  refuses to remove an already-installed module from a plan, and there is no
  working uninstall.

### About "View Credentials"

Odoo never stores a recoverable password — only a hash. So this shows a usable
password **only** for the account the kit provisioned, and even then it's the
default from config, which is stale once the customer completes their password
reset. Every other user shows "real password unknown". Use **Login** instead —
it needs no password at all.

---

## 13. Precautions — the short list

1. **Deploy module files to `common-addons_v19` first, and `chmod -R a+rX`.**
   The most common failure, and the quietest.
2. **Get `Technical Name` exactly right.** No validation.
3. **Subdomains: lowercase, digits, hyphens. Never underscores.** They break TLS.
4. **Choose the plan Name carefully** — it becomes the database name permanently.
5. **Never edit code directly on the server.** Edit locally → push → pull. The
   server clone has been hand-edited before and it caused real divergence.
6. **Never rotate `container_passwd` in `saas.conf` without recording the old
   value** in `container_passwd_legacy`. Rotating it does *not* re-key existing
   databases, and every management action on older clients (Install, Add Module,
   Reconcile) will fail with a bare "Connection Failure".
7. **Creating a DB template restarts the shared template container**, briefly
   affecting all other plans' templates.
8. **Adding a module to a plan restarts each affected client.**
9. **Watch Max Clients** (currently 10) — creation is refused past it.
10. **Module Installation Limit** (currently 20) caps modules installed per run;
    use **Install remaining Modules** to continue.
11. **Don't trust "Installed" blindly on old records.** Bookkeeping written
    before the fixes could be wrong; **Reconcile Modules** rewrites it from
    reality.
12. **A client must be Cancelled before it can be deleted**, and deletion really
    does destroy the container, database and data directory.

---

## 14. Troubleshooting

| Symptom | Most likely cause | Fix |
|---|---|---|
| **Login** button opens a normal login page | Token rejected, or `saas_kit_auto_login` not installed in that database | Check the target's log for `Rejected invalid/expired auto-login token`. Run **Reconcile Modules**. |
| Module stuck `Not Installed` | Files missing from `common-addons_v19`, or unreadable permissions | §10, including `chmod -R a+rX`. Then **Reconcile Modules**. |
| Module installed on server but invisible in a client | Its `ir_module_module` row was removed as non-entitled, or Apps list is stale | Add it to the plan via **Add Module**. |
| "Connection Failure" on Install / Add Module | `container_passwd` was rotated after that database was provisioned | Add the previous password to `container_passwd_legacy` in `saas.conf`. |
| Client site shows a certificate warning | Subdomain contains an underscore, or isn't in the nginx port map | Recreate with a valid hostname; check `/etc/nginx/conf.d/client-ports.conf`. |
| Client reachable on `http://` without redirecting | A stale per-client vhost in `docker_vhosts/` is shadowing the wildcard | Delete that `.conf`, `nginx -t && nginx -s reload`. |
| Client creation fails partway | Port exhaustion, image pull, or template filestore missing | Use **Create Client Instance** to retry; check the manager log. |
| "Delete the linked client first" when deleting a module | A `saas.module.status` row still references it | Was caused by orphaned rows; these now cascade. |
| Plan won't save | Missing Billing Cycle / Base URL / Billing Criteria | §7. |

### Where to look

- **Manager**: `docker logs odoo19`
- **Template container**: `docker exec odoo19_template_cont tail -100 /var/log/odoo/odoo-server.log`
- **A client**: `docker exec <subdomain>.hisabflow.tech tail -100 /var/log/odoo/odoo-server.log`
- **nginx**: `/var/log/nginx/clients.error.log`

---

## 15. Automation you should know is running

| Cron | Interval | Purpose |
|---|---|---|
| Saas Client Creation Cron | hourly | Provisions clients for contracts awaiting one |
| SaaS Recurring Invoice Cron | daily | Recurring invoices |
| SaaS Contract Expiry Cron | daily | Expires contracts, flags instances |
| Renew Mail cron | daily | Renewal reminder emails |

Because client creation is also a cron, a contract may get its client
**without** anyone clicking — don't assume a missing client means failure until
you've checked the contract state.

---

## 16. Teardown

1. Client → **Cancel**
2. **Inactive Client**
3. **Drop DB** and **Drop Container**
4. Delete the client record if you want it gone entirely — this also removes the
   container, database and data directory.

The subdomain's line in the nginx port map is deliberately left behind; it is
harmless and gets overwritten if the name is reused.
