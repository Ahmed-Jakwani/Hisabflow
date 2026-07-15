# -*- coding: utf-8 -*-
"""
HMAC-signed, short-lived, single-purpose "auto login" token.

Deliberately NOT using Odoo's own tools.hash_sign()/verify_hash_signed(): those derive
their signing key from THIS database's own `database.secret` config parameter, which is
generated independently per database - so a token signed by the manager instance's
database could never be verified here, on the client's database, and vice versa.

Instead this signs/verifies with `admin_passwd` (the Odoo database master password),
read straight from this process's own config (`odoo.conf` / `--db_password`... no -
`admin_passwd`). That value is already:
  - present in this container's own odoo.conf (every SaaS Kit container gets one), and
  - already known to the manager instance for this exact database, via
    odoo_saas_kit's models/lib/saas.conf (`container_master` for regular clients,
    `template_master` for the shared per-version template container).
So both sides can derive the same signature without any new secret being provisioned.

Keep this file's algorithm in sync with odoo_saas_kit/models/lib/auto_login_token.py -
they must implement the exact same scheme.
"""

import base64
import hashlib
import hmac
import json
import time


def verify_token(secret, token, expected_db):
    """Return the decoded payload dict if `token` is valid for `expected_db`, else None."""
    try:
        message_b64, sig = token.rsplit(".", 1)
        padded = message_b64 + "=" * (-len(message_b64) % 4)
        message = base64.urlsafe_b64decode(padded.encode())
        expected_sig = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        payload = json.loads(message)
        if payload.get("db") != expected_db:
            return None
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None
