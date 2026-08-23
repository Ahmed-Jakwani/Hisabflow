# -*- coding: utf-8 -*-
#################################################################################
#
#   Copyright (c) 2016-Present Webkul Software Pvt. Ltd. (<https://webkul.com/>)
#   See LICENSE file for full copyright and licensing details.
#   License URL : <https://store.webkul.com/license.html/>
# 
#################################################################################

from odoo import fields, models, api
from odoo.exceptions import UserError,  ValidationError
from . lib import saas_client_db
from . lib import query
from . lib import auto_login_token
from .compat import get_module_resource
import logging

_logger = logging.getLogger(__name__)

MODULE_STATUS = [('installed', "Installed"), 
                ('uninstalled', "Not Installed")]

class ModuleStatus(models.Model):
    _name = 'saas.module.status'
    _description = 'Class for managing module instalation status in client record.'

    module_id = fields.Many2one(comodel_name="saas.module", string="Module")
    technical_name = fields.Char(string="Technical Name", related="module_id.technical_name", readonly=True)
    status = fields.Selection(selection=MODULE_STATUS, default="uninstalled")
    # ondelete='cascade' on both: these rows are pure bookkeeping about one client or
    # one plan and are meaningless without it. Without a cascade the FK defaulted to
    # SET NULL, so deleting a client or plan left the rows behind pointing at nothing -
    # the live manager database had accumulated 131 such orphans. They aren't merely
    # untidy: saas.module.unlink() refuses to delete a module while ANY status row
    # references it, so an orphan row makes a module undeletable with the thoroughly
    # misleading "Delete the linked client first" for a client that no longer exists.
    client_id = fields.Many2one(comodel_name="saas.client", string="SaaS Client", ondelete='cascade')
    plan_id = fields.Many2one(comodel_name="saas.plan", string="SaaS Plan", ondelete='cascade')

    def install_module(self):
        for obj in self:
            login=None
            password=None
            host_server, db_server = obj.client_id.server_id.get_server_details()
            response = query.get_credentials(
                obj.client_id.database_name,
                host_server=host_server,
                db_server=db_server)

            if response.get('status'):
                response = response.get('result')
                login = response[0][0]
                # NOTE: response[0][1] is res_users.password - a HASH, not the real
                # password, and can never authenticate over XML-RPC. The real,
                # working password for every client's admin user is `container_passwd`
                # (see odoo_container.create_db() - it's what the account was actually
                # created with; set_user_data() only ever changes `login`, never this).
                password = auto_login_token.read_secret(
                    get_module_resource('odoo_saas_kit'), "container_passwd")
            else:
                raise UserError("ERR001: "+str(response.get('message')))
            
            endpoint = str(host_server.get('host')) if (host_server['server_type'] == 'remote') else "localhost"
            saas_port = obj.client_id.containter_port

            data = dict(
                operation="install",
                #odoo_url=obj.client_id.client_url,
                odoo_url="http://{}:{}".format(endpoint, saas_port),
                odoo_username=login,
                odoo_password=password,
                database_name=obj.client_id.database_name,
                modules_list=[obj.technical_name],
            )
            response = saas_client_db.create_saas_client(**data)
            if not response.get("modules_installation", False):
                missed_list = ", ".join(response.get('modules_missed'))
                raise UserError("Could't Install the following modules:\n{}".format(missed_list))
            else:
                obj.status = "installed"

    def uninstall_module(self):
        pass

    def upgrade_module(self):
        pass
