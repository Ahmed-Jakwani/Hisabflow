# -*- coding: utf-8 -*-
import logging

from odoo import http, tools
from odoo.http import request
from odoo.addons.web.controllers.utils import ensure_db

from ..tools import verify_token

_logger = logging.getLogger(__name__)


class SaasKitAutoLogin(http.Controller):

    @http.route('/saas_kit/auto_login/<string:token>', type='http', auth='none', csrf=False, sitemap=False)
    def auto_login(self, token, **kw):
        """
        Consume a one-time, HMAC-signed token minted by the SaaS Kit manager instance
        (odoo_saas_kit's "Login" buttons) and open a superuser session on THIS database,
        without a password prompt. See tools.py in this addon for the signing scheme.

        `auth='none'` + ensure_db() mirrors exactly how Odoo's own /web/login works: this
        addon is only installed inside specific databases, so its route is NOT reachable
        until Odoo has already picked a database for the request - but on a host serving
        MANY databases with no dbfilter (like the shared per-version template container),
        Odoo can't reliably infer which one from a stale/absent session cookie alone. This
        addon is added to `server_wide_modules` (see saas_localhost.py/saas_remote.py) so
        its route is always reachable regardless of db context; ensure_db() then reads the
        `?db=` query param, pins the session to it, and - since that changes the session
        cookie - redirects back to this same URL. The retry request now carries a session
        cookie Odoo resolves correctly, dispatches into that database's own registry, and
        this method runs again, this time with a normal request.env to work with.
        """
        ensure_db()  # may abort() with a redirect - if so, everything below is skipped

        db = request.db
        secret = tools.config.get('admin_passwd')

        payload = verify_token(secret, token, db) if secret else None
        if not payload:
            _logger.warning("Rejected invalid/expired auto-login token for db %r", db)
            return request.redirect('/web/login?db=%s' % db)

        user = request.env['res.users'].sudo().browse(payload.get('uid'))
        if not user.exists() or not user.active:
            _logger.warning("Auto-login token for db %r points at a missing/inactive user", db)
            return request.redirect('/web/login?db=%s' % db)

        # Same primitive Odoo itself uses to complete a login once the uid is known
        # (see odoo.http.Session.finalize) - this deliberately skips the password/
        # credential check since the caller was already authenticated by the
        # signature check above.
        request.session['pre_login'] = user.login
        request.session['pre_uid'] = user.id
        request.session.finalize(request.env)

        return request.redirect('/odoo')
