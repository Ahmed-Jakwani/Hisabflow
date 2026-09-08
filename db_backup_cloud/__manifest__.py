# -*- coding: utf-8 -*-
{
    'name': 'Database Backup (Multi-Database, Local)',
    'version': '19.0.2.0.0',
    'category': 'Administration/Tools',
    'summary': 'Scheduled per-database backups to a folder on this server, one sub-folder per database',
    'description': """
Database Backup (Multi-Database, Local)
=======================================
Create one record per database you want backed up — this instance's own
database, a SaaS client's, or a plan template — and each one is dumped to its
own folder under a single backup root on this server.

Features
--------
* **Databases** list: pick the database from a dropdown read live from the
  PostgreSQL server. One record per database, one folder per record.
* Full Odoo ``.zip`` backups (``dump.sql`` + ``filestore`` + ``manifest.json``)
  restorable from the standard database manager, or ``pg_dump`` custom archives.
* **SaaS Kit aware.** A client's filestore lives in its own container data dir,
  not in this instance's ``data_dir``. The module resolves the real filestore
  path so client backups actually contain their attachments — and marks the
  backup *Partial* instead of silently shipping an empty one when it cannot.
* Per-database frequency (daily / weekly / monthly) and retention.
* **Backup History** log with status, size, path and errors.

No external Python packages required.
""",
    'author': 'Arun A George',
    'website': 'https://arunalexgeorge.online',
    'support': 'admin@arunalexgeorge.online',
    'license': 'LGPL-3',
    'images': ['static/description/banner.png'],
    'depends': ['base'],
    'data': [
        'security/ir.model.access.csv',
        'data/db_backup_cron.xml',
        'views/db_backup_target_views.xml',
        'views/db_backup_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'external_dependencies': {
        'python': [],
    },
    'installable': True,
    'application': True,
    'auto_install': False,
}
