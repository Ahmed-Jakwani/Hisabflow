# Database Backup (Multi-Database, Local)

Back up several PostgreSQL databases from one Odoo instance to a folder tree on
the same server — one sub-folder per database.

Built for a SaaS Kit manager, where the databases worth backing up are not just
this instance's own: they are the client databases and the plan templates too.

## How it works

**Backups ▸ Databases** holds one record per database to back up:

| Field | Meaning |
|---|---|
| Database | Dropdown of the databases on this PostgreSQL server |
| Folder | Sub-folder under the backup root. Defaults to the database name |
| Format | `Zip` (database + filestore) or `Dump` (pg_dump custom, SQL only) |
| Frequency | Daily / Weekly / Monthly |
| Keep Last | How many files to keep in the folder before the oldest is deleted |

Resulting layout, with the root set to `/opt/odoo19/Backups`:

```
/opt/odoo19/Backups/
├── test/
│   └── test_2026-09-09_23-00-00.zip
├── hunain-traders.hisabflow.tech/
│   └── hunain-traders.hisabflow.tech_2026-09-09_23-00-00.zip
└── template_accounting_tid_3/
    └── template_accounting_tid_3_2026-09-09_23-00-00.zip
```

A `.zip` is the standard Odoo backup — `dump.sql`, `filestore/`, `manifest.json`
— so it restores from the normal database manager screen.

**Backups ▸ Backup History** logs every run with status, size, path and errors.
A single daily cron runs the backups; each target is only touched when its own
frequency says it is due. The cron does nothing until *Automatic scheduled
backup* is switched on in Settings.

## The filestore, and why this module exists

Odoo's own `dump_db()` resolves the filestore with `config.filestore(db)`, which
always answers inside *this* instance's `data_dir`. For a SaaS client database
that path does not exist — the client's attachments live in its own container
data dir:

```
<saas data root>/<container>/data-dir/filestore/<database>
```

Odoo does not treat a missing filestore as an error, so a naive backup of a
client database produces a perfectly valid-looking `.zip` with **no
attachments**, and nothing tells you until a restore.

This module resolves the real filestore path instead, and when it genuinely
cannot find one it records the backup as **Partial** and lists every path it
tried, rather than reporting success.

## Settings

| Setting | Default |
|---|---|
| Backup root folder | `/opt/odoo19/Backups` |
| Default backups to keep | `7` |
| SaaS data root | `/opt/odoo19/Odoo-SAAS-Data` |
| Template container | `odoo19_template_cont` |

The last two should match `odoo_saas_data` and `odoo_template_v19` in the SaaS
Kit configuration.

## Requirements

- `pg_dump` on the Odoo host, and a PostgreSQL role that can read the databases
  being backed up. Both the dump and the filestore copy use the credentials
  already in `odoo.conf`.
- **If Odoo runs in a container, the backup root and the SaaS data root must be
  bind-mounted into it.** Odoo cannot write to a host path it cannot see; the
  module raises a clear error rather than failing quietly.
- No external Python packages.
