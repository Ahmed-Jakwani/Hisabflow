# -*- coding: utf-8 -*-
import logging
import os
import re
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# How much time must pass before a target is due again.
FREQUENCY_DAYS = {'daily': 1, 'weekly': 7, 'monthly': 30}

# Anything outside this set is replaced by '-' in a folder name, so a database
# name can never escape the backup root (e.g. '../../etc').
_UNSAFE_FOLDER_CHARS = re.compile(r'[^A-Za-z0-9._-]+')


class DbBackupTarget(models.Model):
    """One record per database you want backed up.

    The database is picked from a dropdown of the databases that actually exist
    on this server, and each target owns one sub-folder under the backup root.
    """

    _name = 'db.backup.target'
    _description = 'Database Backup Target'
    _order = 'db_name'
    _rec_name = 'db_name'

    # ──────────────────────────────────────────────────────────────────
    #  Fields
    # ──────────────────────────────────────────────────────────────────
    @api.model
    def _default_keep_last(self):
        try:
            return int(self.env['db.backup']._get_param('default_keep', 7) or 7)
        except (TypeError, ValueError):
            return 7

    @api.model
    def _available_databases(self):
        """Every usable database on this PostgreSQL cluster.

        Deliberately not ``odoo.service.db.list_dbs()``: when odoo.conf sets
        ``db_name`` and leaves ``dbfilter`` empty — the usual SaaS manager setup
        — that function short-circuits and returns only this instance's own
        database, which would hide every client database from the dropdown.
        ``pg_database`` is a cluster-wide catalog, so this sees them all.
        """
        self.env.cr.execute("""
            SELECT datname FROM pg_database
             WHERE datistemplate = false
               AND datallowconn = true
               AND datname <> 'postgres'
             ORDER BY datname
        """)
        return [name for (name,) in self.env.cr.fetchall()]

    @api.model
    def _selection_db_name(self):
        """Databases offered in the dropdown."""
        names = {self.env.cr.dbname}
        # Savepoints, because a failing statement aborts the whole PostgreSQL
        # transaction and would break every later query in this request.
        try:
            with self.env.cr.savepoint():
                names.update(self._available_databases())
        except Exception as e:
            # Never make the view unopenable just because listing failed.
            _logger.warning("db_backup_cloud: could not list databases: %s", e)
        try:
            # Keep already-stored values selectable. If a database is dropped
            # while a target still points at it, omitting the value here makes
            # the list and form unrenderable in the web client.
            with self.env.cr.savepoint():
                self.env.cr.execute(
                    "SELECT DISTINCT db_name FROM db_backup_target "
                    "WHERE db_name IS NOT NULL")
                names.update(name for (name,) in self.env.cr.fetchall())
        except Exception:
            pass
        return [(name, name) for name in sorted(names)]

    db_name = fields.Selection(
        selection='_selection_db_name', string="Database", required=True,
        help="Database to back up. The list is read live from the PostgreSQL "
             "server, so client and template databases appear here too.")
    folder_name = fields.Char(
        string="Folder", compute='_compute_folder_name', store=True, readonly=False,
        help="Sub-folder under the backup root where this database's backups are "
             "kept. Defaults to the database name.")
    folder_path = fields.Char(
        string="Full Path", compute='_compute_folder_path',
        help="Where the backup files for this database are written.")
    backup_format = fields.Selection(
        [('zip', 'Zip (database + filestore)'),
         ('dump', 'Dump (database only, no filestore)')],
        string="Format", default='zip', required=True,
        help="Zip is the standard Odoo backup and can be restored from the "
             "database manager. Dump is a pg_dump custom archive and does NOT "
             "include the filestore.")
    frequency = fields.Selection(
        [('daily', 'Daily'), ('weekly', 'Weekly'), ('monthly', 'Monthly')],
        string="Frequency", default='daily', required=True)
    keep_last = fields.Integer(
        string="Keep Last", default=lambda self: self._default_keep_last(),
        help="Number of backup files to keep in this folder. Older ones are "
             "deleted after each successful run. Set 0 to keep everything.")
    active = fields.Boolean(default=True)

    last_backup_date = fields.Datetime(string="Last Backup", readonly=True)
    last_state = fields.Selection(
        [('success', 'Success'), ('partial', 'Partial'), ('failed', 'Failed')],
        string="Last Result", readonly=True)
    last_error = fields.Text(string="Last Error", readonly=True)
    backup_ids = fields.One2many('db.backup', 'target_id', string="Backups")
    backup_count = fields.Integer(compute='_compute_backup_count', string="Backups")

    # Odoo 19 dropped _sql_constraints in favour of models.Constraint.
    _db_name_uniq = models.Constraint(
        'unique(db_name)',
        "There is already a backup target for this database.")

    # ──────────────────────────────────────────────────────────────────
    #  Computes / constraints
    # ──────────────────────────────────────────────────────────────────
    @api.depends('db_name')
    def _compute_folder_name(self):
        for rec in self:
            rec.folder_name = self._sanitize_folder(rec.db_name) if rec.db_name else False

    @api.depends('folder_name')
    def _compute_folder_path(self):
        root = self.env['db.backup']._get_backup_root(check=False)
        for rec in self:
            rec.folder_path = os.path.join(root, rec.folder_name) if rec.folder_name else root

    @api.depends('backup_ids')
    def _compute_backup_count(self):
        counts = dict(self.env['db.backup']._read_group(
            [('target_id', 'in', self.ids)], ['target_id'], ['__count'],
        ))
        for rec in self:
            rec.backup_count = counts.get(rec, 0)

    @api.constrains('folder_name')
    def _check_folder_name(self):
        for rec in self:
            if not rec.folder_name:
                raise ValidationError(_(
                    "A backup target needs a folder name — it is the sub-folder "
                    "its backups are written to."))
            if rec.folder_name != self._sanitize_folder(rec.folder_name):
                raise ValidationError(_(
                    "The folder name %r contains characters that are not allowed. "
                    "Use only letters, digits, dot, dash and underscore.",
                    rec.folder_name))

    @api.constrains('keep_last')
    def _check_keep_last(self):
        for rec in self:
            if rec.keep_last < 0:
                raise ValidationError(_("Keep Last cannot be negative."))

    @api.model
    def _sanitize_folder(self, name):
        """Reduce a name to something that is always a single safe path segment."""
        cleaned = _UNSAFE_FOLDER_CHARS.sub('-', (name or '').strip()).strip('.-')
        return cleaned or 'unnamed'

    # ──────────────────────────────────────────────────────────────────
    #  Scheduling
    # ──────────────────────────────────────────────────────────────────
    def _is_due(self, now=None):
        """True when this target has waited long enough for another backup."""
        self.ensure_one()
        if not self.last_backup_date:
            return True
        if self.last_state == 'failed':
            # Retry at the next cron run rather than waiting out a whole
            # weekly/monthly interval on a database that never got backed up.
            return True
        now = now or fields.Datetime.now()
        interval = timedelta(days=FREQUENCY_DAYS.get(self.frequency, 1))
        # One hour of slack, otherwise a daily cron that drifts a few minutes
        # later each run eventually skips a whole day.
        return (now - self.last_backup_date) >= interval - timedelta(hours=1)

    # ──────────────────────────────────────────────────────────────────
    #  Actions
    # ──────────────────────────────────────────────────────────────────
    def action_backup_now(self):
        """Back up the selected targets immediately, ignoring the schedule."""
        return self.env['db.backup']._run_and_notify(self, scheduled=False)

    def action_backup_all(self):
        """Back up every active target immediately, ignoring the schedule.

        Not @api.model: view buttons are called with the selected ids as a
        positional argument, which an @api.model method cannot accept.
        """
        return self.env['db.backup']._run_and_notify(self.search([]), scheduled=False)

    def action_view_backups(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Backups of %s", self.db_name),
            'res_model': 'db.backup',
            'view_mode': 'list,form',
            'domain': [('target_id', '=', self.id)],
        }
