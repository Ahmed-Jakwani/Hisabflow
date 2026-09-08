# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # ── Schedule ──
    dbk_schedule_enabled = fields.Boolean(
        string="Automatic scheduled backup",
        config_parameter='db_backup_cloud.schedule_enabled',
        help="When off, the daily cron runs but does nothing. Per-database "
             "frequency is set on each backup target.")

    # ── Storage ──
    dbk_backup_root = fields.Char(
        string="Backup root folder",
        config_parameter='db_backup_cloud.backup_root',
        help="Directory holding one sub-folder per database. If Odoo runs in a "
             "container, this path must be bind-mounted into it.")
    dbk_default_keep = fields.Integer(
        string="Default backups to keep",
        config_parameter='db_backup_cloud.default_keep',
        help="Pre-fills 'Keep Last' on new backup targets. Changing it does not "
             "affect existing targets.")

    # ── SaaS Kit layout ──
    # Client and template databases keep their filestore in per-container data
    # dirs, not in this instance's data_dir, so the module needs to know where
    # the SaaS data root is to produce backups that include attachments.
    dbk_saas_data_root = fields.Char(
        string="SaaS data root",
        config_parameter='db_backup_cloud.saas_data_root',
        help="Matches 'odoo_saas_data' in the SaaS Kit configuration. Each client's "
             "filestore is <root>/<container>/data-dir/filestore/<database>.")
    dbk_template_container = fields.Char(
        string="Template container",
        config_parameter='db_backup_cloud.template_container',
        help="Container holding the plan template databases, e.g. odoo19_template_cont.")

    def action_db_backup_now(self):
        """Persist the form, then back up every target immediately."""
        self.ensure_one()
        self.execute()
        return self.env['db.backup'].action_backup_all()
