# -*- coding: utf-8 -*-
{
    "name": "SaaS Kit Auto Login",
    "summary": "Lets the SaaS Kit manager instance open a signed one-time superuser "
               "session on this database, for the 'Login' buttons on saas.client / "
               "saas.plan over on the manager instance.",
    "description": """
Small companion addon meant to be installed on SaaS Kit client and DB-template
instances (NOT on the manager instance). It exposes a single public route that
accepts a short-lived, HMAC-signed token and, if valid, logs the browser in as
the database's admin user (id 2) without a password prompt.

The token is signed with this database's own `admin_passwd` (the same master
password already present in this container's odoo.conf, and already known to
the manager instance for this database via odoo_saas_kit's saas.conf) - so no
new secret needs to be provisioned or shared. The token is single-purpose,
carries its own expiry, and is meaningless once expired.
    """,
    "version": "19.0.1.0.0",
    "category": "Extra Tools",
    "author": "Internal",
    "license": "LGPL-3",
    "depends": ["base"],
    "data": [],
    "installable": True,
    "application": False,
    "auto_install": False,
}
