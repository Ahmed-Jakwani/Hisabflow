# -*- coding: utf-8 -*-
import logging

from odoo import http, tools
from odoo.http import request

from ..tools import verify_token

_logger = logging.getLogger(__name__)


class SaasKitAutoLogin(http.Controller):

    @http.route('/saas_kit/auto_login/<string:token>', type='http', auth='public', csrf=False, sitemap=False)
    def auto_login(self, token, **kw):
        """
        Consume a one-time, HMAC-signed token minted by the SaaS Kit manager instance
        (odoo_saas_kit's "Login" buttons) and open a superuser session on THIS database,
        without a password prompt. See tools.py in this addon for the signing scheme.
        """
        db = request.env.cr.dbname
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
