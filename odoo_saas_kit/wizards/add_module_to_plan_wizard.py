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
        # saas.module.status(plan_id=..., status='uninstalled') row and (since this
        # touches saas_module_ids) syncs the plan's contracts via
        # saas.plan.sync_contract_modules(), so clients created later inherit it too.
        plan.write({'saas_module_ids': [(4, module.id)]})

        if plan.state == 'confirm' and plan.db_template:
            plan.install_remaining_modules()

        all_clients = self.env['saas.client'].search([
            ('saas_contract_id.plan_id', '=', plan.id),
        ])
        clients = all_clients.filtered(lambda c: c.state == 'started')
        # A stopped/inactive client can't be installed into over XML-RPC - its
        # container isn't listening. Previously those were skipped in silence, so the
        # module was reported as rolled out while some clients never received it. Name
        # them instead, so they can be started and topped up deliberately.
        deferred = all_clients - clients

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
                continue
            # Same reasoning as the template restart in install_remaining_modules() -
            # the RPC install only updates this client's database; its own running
            # container needs a restart to actually show the module's menus/assets.
            try:
                client.restart_client()
            except Exception as e:
                _logger.error("Installed %s on client %s but could not restart its container: %r", module.technical_name, client.name, e)

        msg = "Module <b>%s</b> added to the plan" % module.name
        msg += (" and installed on: %s." % ", ".join(installed_on)) if installed_on else "."
        if deferred:
            msg += ("<br/>Not installed on these clients because they are not running "
                    "(start them, then use the Install button on the client's SaaS "
                    "Modules tab): %s" % ", ".join(
                        "%s [%s]" % (c.name, c.state) for c in deferred))
        if failures:
            msg += "<br/>Failed on: %s" % "; ".join(failures)
        plan.message_post(body=msg)

        if failures:
            raise UserError(
                "%s was added to the plan and its DB template, but failed to install on:\n%s\n\n"
                "You can retry per client from that client's 'SaaS Modules' tab (Install button)."
                % (module.name, "\n".join(failures))
            )
