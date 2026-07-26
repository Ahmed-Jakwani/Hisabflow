# -*- coding: utf-8 -*-
"""
"View Credentials" button on saas.plan (DB template) and saas.client forms.

Gated behind the Hisabflow manager instance's own master/admin password
(the same password Odoo's own Database Manager asks for) via
odoo.service.db.check_super() - NOT any SaaS-Kit-specific secret.

IMPORTANT LIMITATION (see notes in action_validate()): Odoo never stores a
recoverable password, only a salted hash (res_users.password). This wizard
can only ever show:
  - every active user's *login*, always accurate.
  - the *password* for user id 2 (the account the SaaS Kit itself
    provisions) as it was AT CREATION TIME - a single value shared by every
    template and every client, read from this module's own saas.conf
    (container_user/container_passwd; see odoo_container.create_db() in
    models/lib/saas_localhost.py). Once a client's own instance owner
    completes a password reset (see saas.contract.set_user_data() ->
    query.trigger_password_reset()), this value silently goes stale - there
    is no way to detect that from here, so it's shown with a caveat rather
    than omitted.
  - nothing for any other user (secondary accounts the client created
    themselves inside their own instance) - those passwords were never known
    to the SaaS Kit in the first place.
"""

import logging

from odoo import fields, models
from odoo.exceptions import AccessDenied, UserError
from odoo.service.db import check_super

from ..models.compat import get_module_resource
from ..models.lib import auto_login_token
from ..models.lib.pg_query import PgQuery

_logger = logging.getLogger(__name__)

STATES = [('ask', "Ask Password"), ('result', "Result")]

DEFAULT_ADMIN_NOTE = (
    "Default password set by SaaS Kit at creation (same value for every "
    "template/client). If the client has since completed their own "
    "password reset, or this login was personalized, it may no longer work."
)
UNKNOWN_USER_NOTE = (
    "Created directly on the instance, not provisioned by SaaS Kit - the "
    "real password was never known here and cannot be recovered."
)


class SaasCredentialsViewer(models.TransientModel):
    _name = 'saas.credentials.viewer'
    _description = "View SaaS DB/Client Users & Known Passwords"

    state = fields.Selection(selection=STATES, default='ask')
    res_model = fields.Char(string="Source Model")
    res_id = fields.Integer(string="Source Record")
    database_label = fields.Char(string="Database", readonly=True)
    master_password = fields.Char(string="Hisabflow Master Password")
    line_ids = fields.One2many(
        comodel_name='saas.credentials.viewer.line',
        inverse_name='wizard_id',
        string="Users")

    def _get_target_db(self):
        self.ensure_one()
        if self.res_model == 'saas.plan':
            record = self.env['saas.plan'].browse(self.res_id)
            db_name = record.exists() and record.db_template
            server = record.exists() and record.server_id
        elif self.res_model == 'saas.client':
            record = self.env['saas.client'].browse(self.res_id)
            db_name = record.exists() and record.database_name
            server = record.exists() and record.saas_contract_id.server_id
        else:
            raise UserError("Unsupported source record.")

        if not db_name:
            raise UserError("This record does not have a database yet.")
        if not server:
            raise UserError("No SaaS Server configured for this record.")

        _, db_server = server.get_server_details()
        return db_name, db_server

    def action_validate(self):
        self.ensure_one()
        try:
            check_super(self.master_password or '')
        except AccessDenied:
            raise UserError("Incorrect master password for this Hisabflow instance.")

        db_name, db_server = self._get_target_db()

        config_path = get_module_resource('odoo_saas_kit')
        container_passwd = auto_login_token.read_secret(config_path, "container_passwd")

        pgX = PgQuery(db_server['host'], db_name, db_server['user'], db_server['password'], db_server['port'])
        with pgX as pg:
            if not pg.get('status'):
                raise UserError("Could not connect to database %r: %s" % (db_name, pg.get('message')))
            rows = pgX.selectQuery("SELECT id, login FROM res_users WHERE active = true ORDER BY id;")

        if rows is False:
            raise UserError("Could not read users from database %r." % db_name)

        lines = []
        for uid, login in rows:
            if uid == 2:
                lines.append((0, 0, {'login': login, 'password': container_passwd, 'note': DEFAULT_ADMIN_NOTE}))
            else:
                lines.append((0, 0, {'login': login, 'password': "—", 'note': UNKNOWN_USER_NOTE}))

        self.write({
            'state': 'result',
            'database_label': db_name,
            'master_password': False,  # don't linger with the plaintext password
            'line_ids': [(5, 0, 0)] + lines,
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }


class SaasCredentialsViewerLine(models.TransientModel):
    _name = 'saas.credentials.viewer.line'
    _description = "SaaS Credentials Viewer Line"

    wizard_id = fields.Many2one(comodel_name='saas.credentials.viewer', required=True, ondelete='cascade')
    login = fields.Char(string="Username", readonly=True)
    password = fields.Char(string="Password", readonly=True)
    note = fields.Char(string="Note", readonly=True)
