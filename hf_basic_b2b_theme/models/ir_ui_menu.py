# -*- coding: utf-8 -*-
from odoo import api, models


class IrUiMenu(models.Model):
    _inherit = "ir.ui.menu"

    @api.model
    def _visible_menu_ids(self, debug=False):
        """Hide only the HisabFlow dashboard tree when the user checkbox is off.

        The rest of the HisabFlow backend theme remains active for the user.
        This deliberately does not depend on a res.groups XML ID, so menu
        visibility is controlled only by the boolean on res.users.
        """
        visible_ids = super()._visible_menu_ids(debug=debug)

        if self.env.user.hf_hisabflow_menu_access:
            return visible_ids

        root_menu = self.env.ref(
            "hf_basic_b2b_theme.menu_hf_root",
            raise_if_not_found=False,
        )
        if not root_menu:
            return visible_ids

        hisabflow_menu_ids = set(
            self.with_context(active_test=False)
            .search([("id", "child_of", root_menu.id)])
            .ids
        )
        return frozenset(set(visible_ids) - hisabflow_menu_ids)
