# -*- coding: utf-8 -*-
#################################################################################
#
#   Copyright (c) 2016-Present Webkul Software Pvt. Ltd. (<https://webkul.com/>)
#   See LICENSE file for full copyright and licensing details.
#   License URL : <https://store.webkul.com/license.html/>
#
#################################################################################

import logging

from odoo import fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AddModuleToPlan(models.TransientModel):
    _name = "saas.plan.add.module"
    _description = "Add a module to an already-confirmed SaaS Plan and push it to its running clients"

    plan_id = fields.Many2one(comodel_name="saas.plan", string="SaaS Plan", required=True)
    module_id = fields.Many2one(comodel_name="saas.module", string="Module", required=True)

    def action_add_module(self):
        self.ensure_one()
        plan = self.plan_id
        module = self.module_id

        if module.id in plan.saas_module_ids.ids:
            raise UserError("%s is already part of this plan." % module.name)

        # Triggers saas.plan.write()'s existing handling, which creates the matching
        # saas.module.status(plan_id=..., status='uninstalled') row for us.
        plan.write({'saas_module_ids': [(4, module.id)]})

        if plan.state == 'confirm' and plan.db_template:
            plan.install_remaining_modules()

        clients = self.env['saas.client'].search([
            ('saas_contract_id.plan_id', '=', plan.id),
            ('state', '=', 'started'),
        ])

        installed_on = []
        failures = []
        for client in clients:
            status = self.env['saas.module.status'].search([
                ('client_id', '=', client.id),
                ('module_id', '=', module.id),
            ], limit=1)
            if not status:
                status = self.env['saas.module.status'].create({
                    'client_id': client.id,
                    'module_id': module.id,
                })
            if status.status == 'installed':
                installed_on.append(client.name)
                continue
            try:
                status.install_module()
                installed_on.append(client.name)
            except Exception as e:
                _logger.error("Could not install %s on client %s: %r", module.technical_name, client.name, e)
                failures.append("%s: %s" % (client.name, e))

        msg = "Module <b>%s</b> added to the plan" % module.name
        msg += " and installed on: %s." % ", ".join(installed_on) if installed_on else "."
        if failures:
            msg += "<br/>Failed on: %s" % "; ".join(failures)
        # The install RPC only updates the database - an already-running container's
        # process doesn't reload its own module/asset registry from that, so the
        # module can look "installed" yet remain invisible until the container that
        # actually serves that database is restarted (docker restart <container>).
        msg += ("<br/><i>Note: the template container and each affected client container "
                "may need a restart (docker restart) before this module's UI/assets actually "
                "show up for users - installing it here only updates the database.</i>")
        plan.message_post(body=msg)

        if failures:
            raise UserError(
                "%s was added to the plan and its DB template, but failed to install on:\n%s\n\n"
                "You can retry per client from that client's 'SaaS Modules' tab (Install button)."
                % (module.name, "\n".join(failures))
            )
