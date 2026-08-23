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
import logging
import time
from configparser import ConfigParser

_logger = logging.getLogger(__name__)


def get_signing_secret():
    """
    Return the plaintext `admin_passwd` this container was configured with.

    Read it from the config FILE ON DISK, not from `odoo.tools.config`. Odoo 19's
    config.verify_admin_password() does:

        result, updated_hash = crypt_context.verify_and_update(password, stored_hash)
        if result:
            if updated_hash:
                self.options['admin_passwd'] = updated_hash

    (odoo/tools/config.py) - i.e. the first time ANY database-manager operation
    authenticates with the master password, Odoo silently replaces the in-memory
    plaintext with a pbkdf2 hash. The SaaS Kit manager performs exactly those
    operations against these containers all the time (create_database,
    duplicate_database, drop). So from the first one onwards,
    tools.config.get('admin_passwd') no longer equals the plaintext that the
    manager side signs tokens with, every signature comparison fails, and the
    "Login" button silently falls back to a plain login page - until the
    container happens to be restarted, which reloads the plaintext from disk.

    That is exactly the "auto-login works right after a restart, then stops"
    behaviour this addon was suffering from. The file on disk is never rewritten
    by verify_and_update, so it stays authoritative.
    """
    from odoo.tools import config  # imported late: keeps this module importable standalone

    rcfile = getattr(config, 'rcfile', None)
    if rcfile:
        try:
            parser = ConfigParser()
            parser.read(rcfile)
            value = parser.get('options', 'admin_passwd', fallback=None)
            if value:
                return value
        except Exception as e:  # unreadable/malformed config - fall through
            _logger.warning("Could not read admin_passwd from %s: %r", rcfile, e)

    # Fallback: the in-memory value. Correct until the first master-password
    # verification in this process, so this is never worse than the old behaviour.
    return config.get('admin_passwd')


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
