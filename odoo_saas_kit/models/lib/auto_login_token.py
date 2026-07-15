# -*- coding: utf-8 -*-
"""
Build side of the HMAC-signed auto-login token consumed by the `saas_kit_auto_login`
addon's `/saas_kit/auto_login/<token>` route, installed on client/template instances.

Keep this file's algorithm in sync with saas_kit_auto_login/tools.py - see that file's
docstring for why this doesn't use Odoo's own tools.hash_sign()/verify_hash_signed()
(those are keyed per-database and can't be verified across two different databases).
"""

import base64
import hashlib
import hmac
import json
import time
from configparser import ConfigParser


def read_secret(config_path, key):
    """Read `container_master` or `template_master` (the target container's own
    admin_passwd) out of saas.conf, given the module's own config_path."""
    parser = ConfigParser()
    parser.read(config_path + "/models/lib/saas.conf")
    return parser.get("options", key)


def build_token(secret, db, uid=2, ttl_seconds=120):
    """
    `uid` defaults to 2 (the database admin user) - the same convention already used
    elsewhere in this module (see query.get_credentials(), `WHERE id=2`).
    """
    payload = {"db": db, "uid": uid, "exp": int(time.time()) + ttl_seconds}
    message = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    sig = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(message).decode().rstrip("=") + "." + sig
