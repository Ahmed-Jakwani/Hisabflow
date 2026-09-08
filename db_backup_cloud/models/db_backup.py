# -*- coding: utf-8 -*-
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile

from odoo import api, fields, models, release, sql_db, _
from odoo.exceptions import UserError
from odoo.tools import config, osutil
from odoo.tools.misc import find_pg_tool, exec_pg_environ

_logger = logging.getLogger(__name__)

PARAM = 'db_backup_cloud.%s'
DEFAULT_BACKUP_ROOT = '/opt/odoo19/Backups'
DEFAULT_SAAS_DATA_ROOT = '/opt/odoo19/Odoo-SAAS-Data'
DEFAULT_TEMPLATE_CONTAINER = 'odoo19_template_cont'


def _truthy(value):
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


class DbBackup(models.Model):
    """History of backup runs, and the engine that produces them."""

    _name = 'db.backup'
    _description = 'Database Backup'
    _order = 'backup_date desc, id desc'

    name = fields.Char(string="File Name", readonly=True)
    backup_date = fields.Datetime(string="Date", readonly=True, default=fields.Datetime.now)
    target_id = fields.Many2one('db.backup.target', string="Target", readonly=True, ondelete='set null')
    db_name = fields.Char(string="Database", readonly=True)
    file_path = fields.Char(string="Stored At", readonly=True)
    file_size = fields.Float(string="Size (MB)", readonly=True, digits=(12, 2))
    state = fields.Selection([
        ('success', 'Success'),
        ('partial', 'Partial (filestore missing)'),
        ('failed', 'Failed'),
    ], string="Status", readonly=True, default='success')
    scheduled = fields.Boolean(string="Scheduled", readonly=True)
    error_message = fields.Text(string="Details", readonly=True)

    # ──────────────────────────────────────────────────────────────────
    #  Config helpers
    # ──────────────────────────────────────────────────────────────────
    @api.model
    def _get_param(self, key, default=None):
        return self.env['ir.config_parameter'].sudo().get_param(PARAM % key, default)

    @api.model
    def _get_backup_root(self, check=True):
        """Absolute directory that holds one sub-folder per database.

        With ``check``, the directory is created and tested for writability —
        which is where a missing container bind-mount shows up.
        """
        root = (self._get_param('backup_root') or DEFAULT_BACKUP_ROOT).strip()
        if not check:
            return root
        if not os.path.isabs(root):
            raise UserError(_("The backup root must be an absolute path, got %r.", root))
        try:
            os.makedirs(root, exist_ok=True)
        except OSError as e:
            raise UserError(_(
                "Cannot create the backup root '%(root)s': %(error)s\n\n"
                "If Odoo runs inside a container, this path must be bind-mounted "
                "into it — otherwise Odoo cannot see the host directory.",
                root=root, error=e))
        if not os.access(root, os.W_OK):
            raise UserError(_(
                "The backup root '%s' exists but is not writable by the Odoo user.", root))
        return root

    # ──────────────────────────────────────────────────────────────────
    #  Filestore resolution
    # ──────────────────────────────────────────────────────────────────
    def _filestore_candidates(self, db_name):
        """Possible filestore locations for ``db_name``, best guess first.

        Odoo's own ``config.filestore()`` only ever answers for *this* instance's
        data_dir, so it is wrong for SaaS client and template databases: those
        live in per-container data dirs under the SaaS data root. Getting this
        wrong is silent — the dump simply comes out with no attachments.
        """
        saas_root = (self._get_param('saas_data_root') or DEFAULT_SAAS_DATA_ROOT).strip()
        template_container = (self._get_param('template_container')
                              or DEFAULT_TEMPLATE_CONTAINER).strip()
        own = config.filestore(db_name)
        candidates = []

        # This instance's own database.
        if db_name == self.env.cr.dbname:
            candidates.append(own)

        if saas_root:
            # A SaaS client: the container name is authoritative when saas_kit
            # is installed here, and equals the database name in practice.
            if 'saas.client' in self.env:
                client = self.env['saas.client'].sudo().search(
                    [('database_name', '=', db_name)], limit=1)
                if client and client.container_name:
                    candidates.append(os.path.join(
                        saas_root, client.container_name, 'data-dir', 'filestore', db_name))
            candidates.append(os.path.join(
                saas_root, db_name, 'data-dir', 'filestore', db_name))
            # Plan templates all live inside one shared template container.
            if template_container:
                candidates.append(os.path.join(
                    saas_root, template_container, 'data-dir', 'filestore', db_name))

        candidates.append(own)

        seen, unique = set(), []
        for path in candidates:
            if path and path not in seen:
                seen.add(path)
                unique.append(path)
        return unique

    def _resolve_filestore(self, db_name):
        """First candidate filestore that exists on disk, or None."""
        for path in self._filestore_candidates(db_name):
            if os.path.isdir(path):
                return path
        return None

    # ──────────────────────────────────────────────────────────────────
    #  Dumping
    # ──────────────────────────────────────────────────────────────────
    def _dump_manifest(self, db_name):
        """Odoo restore manifest, read from the database being dumped."""
        connection = sql_db.db_connect(db_name)
        with connection.cursor() as cr:
            pg_version = "%d.%d" % divmod(cr._obj.connection.server_version / 100, 100)
            cr.execute("SELECT name, latest_version FROM ir_module_module "
                       "WHERE state = 'installed'")
            modules = dict(cr.fetchall())
        return {
            'odoo_dump': '1',
            'db_name': db_name,
            'version': release.version,
            'version_info': release.version_info,
            'major_version': release.major_version,
            'pg_version': pg_version,
            'modules': modules,
        }

    def _run_pg_dump(self, args):
        """Run pg_dump with Odoo's configured PostgreSQL credentials."""
        cmd = [find_pg_tool('pg_dump'), '--no-owner'] + args
        result = subprocess.run(
            cmd, env=exec_pg_environ(),
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=False)
        if result.returncode:
            stderr = (result.stderr or b'').decode(errors='replace').strip()
            raise UserError(_("pg_dump failed (exit %(code)s): %(error)s",
                              code=result.returncode, error=stderr or 'no output'))

    def _dump_zip(self, db_name, file_path):
        """Standard Odoo .zip backup: dump.sql + filestore/ + manifest.json.

        Returns the filestore path used, or None when none was found.
        """
        filestore = self._resolve_filestore(db_name)
        with tempfile.TemporaryDirectory() as dump_dir:
            self._run_pg_dump(['--file=' + os.path.join(dump_dir, 'dump.sql'), db_name])
            if filestore:
                shutil.copytree(filestore, os.path.join(dump_dir, 'filestore'))
            with open(os.path.join(dump_dir, 'manifest.json'), 'w') as fh:
                json.dump(self._dump_manifest(db_name), fh, indent=4)
            with open(file_path, 'wb') as stream:
                osutil.zip_dir(
                    dump_dir, stream, include_dir=False,
                    fnct_sort=lambda file_name: file_name != 'dump.sql')
        return filestore

    def _dump_custom(self, db_name, file_path):
        """pg_dump custom-format archive. SQL only — never has a filestore."""
        self._run_pg_dump(['--format=c', '--file=' + file_path, db_name])
        return None

    # ──────────────────────────────────────────────────────────────────
    #  Core
    # ──────────────────────────────────────────────────────────────────
    def _run_backup(self, target, scheduled=False):
        """Back up one target and record the result. Never raises."""
        target.ensure_one()
        db_name = target.db_name
        # UTC, to match backup_date — otherwise file names and the history log
        # disagree on a server whose local time is not UTC.
        stamp = fields.Datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        filename = '%s_%s.%s' % (db_name, stamp, target.backup_format)
        file_path = None
        vals = {
            'name': filename,
            'target_id': target.id,
            'db_name': db_name,
            'scheduled': scheduled,
        }
        try:
            folder = os.path.join(self._get_backup_root(), target.folder_name)
            os.makedirs(folder, exist_ok=True)
            file_path = os.path.join(folder, filename)

            if target.backup_format == 'zip':
                filestore = self._dump_zip(db_name, file_path)
            else:
                filestore = self._dump_custom(db_name, file_path)

            vals.update({
                'file_path': file_path,
                'file_size': round(os.path.getsize(file_path) / (1024.0 * 1024.0), 2),
            })
            if target.backup_format == 'zip' and not filestore:
                # Loud on purpose: a filestore-less zip restores without any
                # attachment, and looks perfectly healthy until you try one.
                vals.update({
                    'state': 'partial',
                    'error_message': _(
                        "The database was dumped, but no filestore directory was found, "
                        "so this backup contains no attachments.\nLooked in:\n%s",
                        "\n".join(self._filestore_candidates(db_name))),
                })
                _logger.warning(
                    "db_backup_cloud: no filestore found for %s; backup has no attachments",
                    db_name)
            else:
                vals.update({'state': 'success', 'error_message': False})
                if filestore:
                    vals['error_message'] = _("Filestore taken from: %s", filestore)

            # Housekeeping, deliberately outside the block above: failing to
            # prune old files must never invalidate the backup just written.
            try:
                self._apply_retention(target, folder)
            except Exception as e:
                _logger.warning("db_backup_cloud: retention failed in %s: %s", folder, e)
        except Exception as e:
            _logger.exception("db_backup_cloud: backup of %s failed", db_name)
            vals.update({
                'state': 'failed',
                'error_message': str(e),
                'file_path': False,
                'file_size': 0.0,
            })
            # Do not leave a half-written archive behind to be mistaken for good.
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass

        backup = self.create(vals)
        target.write({
            'last_backup_date': backup.backup_date,
            'last_state': backup.state,
            'last_error': backup.error_message if backup.state != 'success' else False,
        })
        return backup

    def _apply_retention(self, target, folder):
        """Keep only the newest ``keep_last`` files for this target."""
        if target.keep_last <= 0:
            return
        # Match this module's own file names exactly, in either format. A bare
        # "startswith(db_name + '_')" would also match a *different* database
        # whose name merely starts the same (hisab_ vs hisab_prod_...), and
        # filtering on one extension would orphan files from the other after a
        # format change.
        pattern = re.compile(
            r'^%s_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.(?:zip|dump)$'
            % re.escape(target.db_name))
        try:
            names = sorted(
                (f for f in os.listdir(folder) if pattern.match(f)),
                reverse=True)
        except OSError as e:
            _logger.warning("db_backup_cloud: cannot list %s: %s", folder, e)
            return
        # The newest file is the one just written, so keep_last counts from here.
        for stale in names[target.keep_last:]:
            try:
                os.remove(os.path.join(folder, stale))
                _logger.info("db_backup_cloud: removed old backup %s", stale)
            except OSError as e:
                _logger.warning("db_backup_cloud: cannot remove %s: %s", stale, e)

    # ──────────────────────────────────────────────────────────────────
    #  Entry points
    # ──────────────────────────────────────────────────────────────────
    def _run_and_notify(self, targets, scheduled=False):
        """Back up several targets, then summarise the outcome in the UI."""
        if not targets:
            raise UserError(_(
                "There are no backup targets yet. Create one under "
                "Backups ▸ Databases and pick the database to back up."))
        self._get_backup_root()  # fail fast and clearly on a bad root
        backups = self.browse()
        for target in targets:
            backups |= self._run_backup(target, scheduled=scheduled)

        failed = backups.filtered(lambda b: b.state == 'failed')
        partial = backups.filtered(lambda b: b.state == 'partial')
        if failed:
            level, message = 'danger', _(
                "%(count)s of %(total)s backups failed: %(names)s",
                count=len(failed), total=len(backups),
                names=", ".join(failed.mapped('db_name')))
        elif partial:
            level, message = 'warning', _(
                "%(total)s backups written, but %(count)s have no filestore: %(names)s",
                total=len(backups), count=len(partial),
                names=", ".join(partial.mapped('db_name')))
        else:
            level, message = 'success', _(
                "%s backup(s) completed.", len(backups))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Database Backup"),
                'message': message,
                'type': level,
                'sticky': level != 'success',
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def action_backup_all(self):
        """Back up every active target now, regardless of frequency.

        Not @api.model: view buttons are called with the selected ids as a
        positional argument, which an @api.model method cannot accept.
        """
        return self._run_and_notify(
            self.env['db.backup.target'].search([]), scheduled=False)

    @api.model
    def _cron_run_backup(self):
        """Back up each active target that is due."""
        if not _truthy(self._get_param('schedule_enabled', 'False')):
            _logger.info("db_backup_cloud: scheduled backup disabled, skipping.")
            return
        targets = self.env['db.backup.target'].search([])
        if not targets:
            _logger.info("db_backup_cloud: no backup targets configured.")
            return
        try:
            self._get_backup_root()
        except UserError as e:
            _logger.error("db_backup_cloud: backup root unusable, skipping run: %s", e)
            return
        now = fields.Datetime.now()
        for target in targets:
            if not target._is_due(now):
                continue
            # The savepoint un-poisons the transaction after a database error;
            # it does not swallow the exception, so the except is what actually
            # keeps one broken target from aborting the rest of the run.
            try:
                with self.env.cr.savepoint():
                    self._run_backup(target, scheduled=True)
            except Exception:
                _logger.exception(
                    "db_backup_cloud: scheduled backup of %s could not be recorded",
                    target.db_name)
