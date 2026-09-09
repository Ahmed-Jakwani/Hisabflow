# -*- coding: utf-8 -*-
from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    hf_hisabflow_menu_access = fields.Boolean(
        string="HisabFlow Menu",
        default=False,
        help="Tick this checkbox to show the HisabFlow dashboard menu for this user.",
    )

    def write(self, vals):
        result = super().write(vals)
        if "hf_hisabflow_menu_access" in vals:
            # Odoo caches menu trees per user. Clear the registry caches so the
            # checkbox takes effect immediately on the user's next refresh.
            self.env.registry.clear_cache()
        return result
