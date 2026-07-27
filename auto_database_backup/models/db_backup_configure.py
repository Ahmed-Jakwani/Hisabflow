# -*- coding: utf-8 -*-
###############################################################################
#
#    Cybrosys Technologies Pvt. Ltd.
#
#    Copyright (C) 2026-TODAY Cybrosys Technologies(<https://www.cybrosys.com>)
#    Author: Cybrosys Techno Solutions (odoo@cybrosys.com)
#
#    You can modify it under the terms of the GNU LESSER
#    GENERAL PUBLIC LICENSE (LGPL v3), Version 3.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU LESSER GENERAL PUBLIC LICENSE (LGPL v3) for more details.
#
#    You should have received a copy of the GNU LESSER GENERAL PUBLIC LICENSE
#    (LGPL v3) along with this program.
#    If not, see <http://www.gnu.org/licenses/>.
#
###############################################################################
import errno
import ftplib
import json
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta

import paramiko
import requests
from google.cloud import storage as gcs_storage
from werkzeug import urls

import odoo
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.service import db
from odoo.tools.misc import find_pg_tool, exec_pg_environ

_logger = logging.getLogger(__name__)

GOOGLE_AUTH_ENDPOINT = 'https://accounts.google.com/o/oauth2/auth'
GOOGLE_TOKEN_ENDPOINT = 'https://accounts.google.com/o/oauth2/token'
GOOGLE_API_BASE_URL = 'https://www.googleapis.com'


class DbBackupConfigure(models.Model):
    """DbBackupConfigure class provides an interface to manage database
       backups of Local Server, Remote Server (FTP/SFTP), Google Drive and
       Google Cloud Storage"""
    _name = 'db.backup.configure'
    _description = 'Automatic Database Backup'

    # Mapping of backup destination to its handler method
    _BACKUP_HANDLERS = {
        'local': '_backup_to_local',
        'ftp': '_backup_to_ftp',
        'sftp': '_backup_to_sftp',
        'google_drive': '_backup_to_google_drive',
        'google_cloud': '_backup_to_google_cloud',
    }

    name = fields.Char(string='Name', required=True, help='Add the name')
    backup_scope = fields.Selection([
        ('single', 'Single Database'),
        ('all', 'All Databases'),
        ('selected', 'Selected Databases'),
    ], string='Backup Scope', default='single', required=True,
        help="Back up one database, every database on the server, or a "
             "chosen list of databases.")
    db_name = fields.Char(
        string='Database Name',
        help='Name of the database (used when the scope is a single '
             'database)')
    selected_db_names = fields.Char(
        string='Databases',
        help="Comma-separated list of database names to back up when the "
             "scope is 'Selected Databases'.")
    master_pwd = fields.Char(
        string='Master Password', copy=False,
        help='The database master password. It is used only to authorize the '
             'configuration and is never stored on the record.')
    backup_format = fields.Selection([
        ('zip', 'Zip'),
        ('dump', 'Dump')
    ], string='Backup Format', default='zip', required=True,
        help='Format of the backup')
    backup_destination = fields.Selection([
        ('local', 'Local Storage'),
        ('google_drive', 'Google Drive'),
        ('ftp', 'FTP'),
        ('sftp', 'SFTP'),
        ('google_cloud', 'Google Cloud Storage'),
    ], string='Backup Destination', help='Destination of the backup')
    backup_frequency = fields.Selection([
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
    ], default='daily', string='Backup Frequency',
        help='Frequency of Backup Scheduling')
    backup_path = fields.Char(string='Backup Path',
                              help='Local storage directory path')
    sftp_host = fields.Char(string='SFTP Host', help='SFTP host details')
    sftp_port = fields.Char(string='SFTP Port', default=22,
                            help='SFTP port details')
    sftp_user = fields.Char(string='SFTP User', copy=False,
                            help='SFTP user details')
    sftp_password = fields.Char(string='SFTP Password', copy=False,
                                help='SFTP password')
    sftp_path = fields.Char(string='SFTP Path', help='SFTP path details')
    ftp_host = fields.Char(string='FTP Host', help='FTP host details')
    ftp_port = fields.Char(string='FTP Port', default=21,
                           help='FTP port details')
    ftp_user = fields.Char(string='FTP User', copy=False,
                           help='FTP user details')
    ftp_password = fields.Char(string='FTP Password', copy=False,
                               help='FTP password')
    ftp_path = fields.Char(string='FTP Path', help='FTP path details')
    active = fields.Boolean(default=False, string='Active',
                            help='Activate the Scheduled Action or not')
    hide_active = fields.Boolean(string="Hide Active",
                                 help="Make active field to readonly")
    auto_remove = fields.Boolean(string='Remove Old Backups',
                                 help='Remove old backups')
    days_to_remove = fields.Integer(
        string='Remove After', default=1,
        help='Automatically delete stored backups after this number of days')
    google_drive_folder_key = fields.Char(string='Drive Folder ID',
                                          help='Folder id of the drive')
    notify_user = fields.Boolean(string='Notify User',
                                 help='Send an email notification to user when'
                                      'the backup operation is successful'
                                      ' or failed')
    notify_mode = fields.Selection([
        ('all', 'All Runs'),
        ('failure', 'Only on Failure'),
    ], string='Notify On', default='all',
        help='Send a notification for every run or only when a backup fails.')
    user_id = fields.Many2one('res.users', string='User',
                              help='Name of the user')
    backup_filename = fields.Char(string='Backup Filename',
                                  help='For Storing generated backup filename')
    generated_exception = fields.Char(
        string='Exception',
        help='Exception encountered while backup generation')
    last_backup_date = fields.Datetime(
        string='Last Backup', readonly=True, copy=False,
        help='Date and time of the last backup attempt.')
    last_backup_status = fields.Selection([
        ('success', 'Success'),
        ('failed', 'Failed'),
    ], string='Last Status', readonly=True, copy=False,
        help='Result of the last backup attempt.')
    last_success_date = fields.Datetime(
        string='Last Successful Backup', readonly=True, copy=False,
        help='Date and time of the last successful backup.')
    history_ids = fields.One2many(
        'db.backup.history', 'configure_id', string='Backup History',
        help='History of backup runs for this configuration.')
    history_count = fields.Integer(
        string='History Count', compute='_compute_history_count',
        help='Number of recorded backup runs.')
    gdrive_refresh_token = fields.Char(string='Google drive Refresh Token',
                                       copy=False,
                                       help='Refresh token for google drive')
    gdrive_access_token = fields.Char(string='Google Drive Access Token',
                                      copy=False,
                                      help='Access token for google drive')
    is_google_drive_token_generated = fields.Boolean(
        string='Google drive Token Generated',
        compute='_compute_is_google_drive_token_generated', copy=False,
        help='Google drive token generated or not')
    gdrive_client_key = fields.Char(string='Google Drive Client ID',
                                    copy=False,
                                    help='Client id of the google drive')
    gdrive_client_secret = fields.Char(string='Google Drive Client Secret',
                                       copy=False,
                                       help='Client secret id of the google'
                                            ' drive')
    gdrive_token_validity = fields.Datetime(
        string='Google Drive Token Validity', copy=False,
        help='Token validity of the google drive')
    gdrive_redirect_uri = fields.Char(string='Google Drive Redirect URI',
                                      compute='_compute_redirect_uri',
                                      help='Redirect URI of the google drive')
    gcs_service_account_key = fields.Text(
        string='GCS Service Account Key', copy=False,
        help="Paste the Google Cloud service account key (JSON) with access "
             "to the target bucket.")
    gcs_bucket = fields.Char(
        string='GCS Bucket', help="Google Cloud Storage bucket name.")
    gcs_folder_name = fields.Char(
        string='GCS Folder',
        help="Optional folder (prefix) inside the bucket.")

    # -------------------------------------------------------------------------
    # Compute methods
    # -------------------------------------------------------------------------
    def _compute_redirect_uri(self):
        """Compute the OAuth redirect URI for Google Drive."""
        base_url = self.get_base_url()
        for rec in self:
            rec.gdrive_redirect_uri = base_url + '/google_drive/authentication'

    @api.depends('gdrive_access_token', 'gdrive_refresh_token')
    def _compute_is_google_drive_token_generated(self):
        """Set True if the Google Drive refresh token is generated"""
        for rec in self:
            rec.is_google_drive_token_generated = bool(
                rec.gdrive_access_token) and bool(rec.gdrive_refresh_token)

    def _compute_history_count(self):
        """Count the backup history records per configuration."""
        history_data = self.env['db.backup.history']._read_group(
            [('configure_id', 'in', self.ids)],
            groupby=['configure_id'], aggregates=['__count'])
        counts = {config.id: count for config, count in history_data}
        for rec in self:
            rec.history_count = counts.get(rec.id, 0)

    # -------------------------------------------------------------------------
    # Onchange / CRUD / constraints
    # -------------------------------------------------------------------------
    @api.onchange('backup_destination')
    def _onchange_back_up_local(self):
        """When the destination is local storage, no connection test is
        required, so the active field can be edited directly."""
        if self.backup_destination == 'local':
            self.hide_active = True

    @api.constrains('db_name', 'backup_scope', 'selected_db_names')
    def _check_db_name(self):
        """Validate the configured database(s) actually exist."""
        available = db.list_dbs(force=True)
        for rec in self:
            if rec.backup_scope == 'single':
                if not rec.db_name or rec.db_name not in available:
                    raise ValidationError(_("Invalid Database Name!"))
            elif rec.backup_scope == 'selected':
                names = rec._get_selected_db_names()
                if not names:
                    raise ValidationError(
                        _("Please enter at least one database name."))
                invalid = [name for name in names if name not in available]
                if invalid:
                    raise ValidationError(
                        _("Invalid database name(s): %s", ', '.join(invalid)))

    def _get_selected_db_names(self):
        """Parse the comma-separated selected database names."""
        return [name.strip()
                for name in (self.selected_db_names or '').split(',')
                if name.strip()]

    def _get_target_databases(self):
        """Return the databases this configuration should back up."""
        available = db.list_dbs(force=True)
        if self.backup_scope == 'all':
            return available
        if self.backup_scope == 'selected':
            return [name for name in self._get_selected_db_names()
                    if name in available]
        return [self.db_name] if self.db_name else []

    def _authorize_master_pwd(self, master_pwd, require=False):
        """Validate the database master password without persisting it.

        The master password is only used as an authorization gate when
        configuring a backup; it is never needed by the scheduled backup
        itself, so it is validated here and never stored on the record.
        """
        if not master_pwd:
            if require:
                raise ValidationError(
                    _("The master password is required to configure a "
                      "backup."))
            return
        try:
            db.check_super(master_pwd)
        except Exception:
            raise ValidationError(_("Invalid Master Password!"))

    @api.model_create_multi
    def create(self, vals_list):
        """Authorize with the master password on creation, then discard it so
        it is never stored in plain text."""
        for vals in vals_list:
            self._authorize_master_pwd(vals.get('master_pwd'), require=True)
            vals['master_pwd'] = False
        return super().create(vals_list)

    def write(self, vals):
        """Re-authorize when a master password is supplied, then discard it."""
        if 'master_pwd' in vals:
            self._authorize_master_pwd(vals.get('master_pwd'))
            vals['master_pwd'] = False
        return super().write(vals)

    # -------------------------------------------------------------------------
    # Authorization code actions
    # -------------------------------------------------------------------------
    def action_get_gdrive_auth_code(self):
        """Generate Google Drive authorization code."""
        base_url = self.get_base_url()
        action_id = self.env["ir.actions.act_window"].sudo()._for_xml_id(
            "auto_database_backup.db_backup_configure_action")['id']
        url_return = (f"{base_url}/web#id={self.id}&action={action_id}"
                      f"&view_type=form&model=db.backup.configure")
        state = {
            'backup_config_id': self.id,
            'url_return': url_return}
        params = {
            'response_type': 'code',
            'client_id': self.gdrive_client_key,
            'scope': 'https://www.googleapis.com/auth/drive '
                     'https://www.googleapis.com/auth/drive.file',
            'redirect_uri': f"{base_url}/google_drive/authentication",
            'access_type': 'offline',
            'state': json.dumps(state),
            'approval_prompt': 'force',
        }
        return {
            'type': 'ir.actions.act_url',
            'target': 'self',
            'url': f"{GOOGLE_AUTH_ENDPOINT}?{urls.url_encode(params)}",
        }

    # -------------------------------------------------------------------------
    # Token generation
    # -------------------------------------------------------------------------
    def generate_gdrive_refresh_token(self):
        """Generate Google Drive access token from refresh token if expired."""
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = {
            'refresh_token': self.gdrive_refresh_token,
            'client_id': self.gdrive_client_key,
            'client_secret': self.gdrive_client_secret,
            'grant_type': 'refresh_token',
        }
        try:
            res = requests.post(GOOGLE_TOKEN_ENDPOINT, data=data,
                                headers=headers)
            res.raise_for_status()
            response = res.json() if res.ok else {}
            if response:
                expires_in = response.get('expires_in', 0)
                self.write({
                    'gdrive_access_token': response.get('access_token'),
                    'gdrive_token_validity':
                        fields.Datetime.now() + timedelta(seconds=expires_in),
                })
        except requests.HTTPError as error:
            error_key = error.response.json().get("error", "unknown error")
            raise UserError(_(
                "An error occurred while generating the token. Your "
                "authorization code may be invalid or has already expired "
                "[%s]. Please check your Client ID and secret on the Google "
                "APIs platform or try stopping and restarting your calendar "
                "synchronization.", error_key))

    def get_gdrive_tokens(self, authorize_code):
        """Generate Google Drive tokens from authorization code."""
        base_url = self.get_base_url()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = {
            'code': authorize_code,
            'client_id': self.gdrive_client_key,
            'client_secret': self.gdrive_client_secret,
            'grant_type': 'authorization_code',
            'redirect_uri': f"{base_url}/google_drive/authentication",
        }
        try:
            res = requests.post(GOOGLE_TOKEN_ENDPOINT, data=data,
                                headers=headers)
            res.raise_for_status()
            response = res.json() if res.ok else {}
            if response:
                expires_in = response.get('expires_in', 0)
                self.write({
                    'gdrive_access_token': response.get('access_token'),
                    'gdrive_refresh_token': response.get('refresh_token'),
                    'gdrive_token_validity':
                        fields.Datetime.now() + timedelta(seconds=expires_in)
                        if expires_in else False,
                })
        except requests.HTTPError:
            raise UserError(_(
                "Something went wrong during your token generation. "
                "Your authorization code may be invalid."))

    # -------------------------------------------------------------------------
    # Connection test actions
    # -------------------------------------------------------------------------
    def _connection_success_notification(self):
        """Return a success notification for a passing connection test."""
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'title': _("Connection Test Succeeded!"),
                'message': _("Everything seems properly set up!"),
                'sticky': False,
            }
        }

    def _connection_failed_notification(self, message=None):
        """Return a danger notification for a failing connection test."""
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'danger',
                'title': _("Connection Test Failed!"),
                'message': message or _("An error occurred while testing the "
                                        "connection."),
                'sticky': False,
            }
        }

    def _get_gcs_client(self):
        """Return a Google Cloud Storage client from the service account
        key."""
        return gcs_storage.Client.from_service_account_info(
            json.loads(self.gcs_service_account_key))

    def action_gcs(self):
        """Test the Google Cloud Storage connection."""
        if not (self.gcs_service_account_key and self.gcs_bucket):
            return self._connection_failed_notification(
                _("Please enter the service account key and bucket name."))
        try:
            self._get_gcs_client().bucket(self.gcs_bucket).exists()
            self.active = self.hide_active = True
            return self._connection_success_notification()
        except Exception as error:
            self.active = self.hide_active = False
            _logger.warning("Google Cloud Storage connection test failed: %s",
                            error, exc_info=True)
            return self._connection_failed_notification()

    def action_sftp_connection(self):
        """Test the SFTP or FTP connection using the entered credentials."""
        if self.backup_destination == 'sftp':
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                client.connect(hostname=self.sftp_host,
                               username=self.sftp_user,
                               password=self.sftp_password,
                               port=self.sftp_port)
                sftp = client.open_sftp()
                sftp.close()
            except Exception as error:
                raise UserError(_("SFTP Exception: %s", error))
            finally:
                client.close()
        elif self.backup_destination == 'ftp':
            try:
                ftp_server = ftplib.FTP()
                ftp_server.connect(self.ftp_host, int(self.ftp_port))
                ftp_server.login(self.ftp_user, self.ftp_password)
                ftp_server.quit()
            except Exception as error:
                raise UserError(_("FTP Exception: %s", error))
        self.active = self.hide_active = True
        return self._connection_success_notification()

    # -------------------------------------------------------------------------
    # Backup execution - dispatcher, manual trigger and result handling
    # -------------------------------------------------------------------------
    def _schedule_auto_backup(self, frequency):
        """Run backups for every active configuration matching the given
        frequency (called by the scheduled actions)."""
        records = self.search([
            ('backup_frequency', '=', frequency),
            ('active', '=', True),
        ])
        for rec in records:
            rec._run_backup()

    def action_backup_now(self):
        """Manually run this configuration's backup immediately.

        The backup is executed as the scheduled-action user so that the
        cron-only guard in :meth:`dump_data` is satisfied without weakening
        it."""
        self.ensure_one()
        cron = self.env.ref(
            f'auto_database_backup.ir_cron_auto_db_backup_'
            f'{self.backup_frequency}')
        status = self.with_user(cron.user_id.id)._run_backup(manual=True)
        self.invalidate_recordset(['generated_exception'])
        if status == 'success':
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'type': 'success',
                    'title': _("Backup Completed"),
                    'message': _("The backup finished successfully."),
                    'sticky': False,
                },
            }
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'danger',
                'title': _("Backup Failed"),
                'message': self.generated_exception or _(
                    "The backup did not complete. See the backup history "
                    "for details."),
                'sticky': True,
            },
        }

    def action_download_backup(self):
        """Generate a fresh backup of this configuration and download it in
        the browser."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': f'/auto_database_backup/download/{self.id}',
            'target': 'self',
        }

    def _run_backup(self, manual=False):
        """Back up every target database, recording a history entry per
        database, an overall last-run status and one notification."""
        self.ensure_one()
        handler = self._BACKUP_HANDLERS.get(self.backup_destination)
        if not handler:
            return False
        failures = []
        for db_name in self._get_target_databases():
            error = self._run_one(handler, db_name, manual)
            if error is not None:
                failures.append((db_name, error))
        overall = 'failed' if failures else 'success'
        self._finalize_run(overall, failures)
        return overall

    def _run_one(self, handler, db_name, manual):
        """Back up a single database; return None on success or the error
        message on failure."""
        backup_time = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")
        backup_filename = f"{db_name}_{backup_time}.{self.backup_format}"
        self.sudo().backup_filename = backup_filename
        start = fields.Datetime.now()
        try:
            getattr(self, handler)(db_name, backup_filename, backup_time)
            self._log_history('success', db_name, backup_filename, start,
                              manual)
            return None
        except Exception as error:
            _logger.warning("Database backup failed for '%s' (%s): %s",
                            self.display_name, db_name, error, exc_info=True)
            self._log_history('failed', db_name, backup_filename, start,
                              manual, str(error))
            return str(error)

    def _log_history(self, status, db_name, backup_filename, start, manual,
                     message=False):
        """Create a history record for a single database backup run."""
        self.env['db.backup.history'].sudo().create({
            'configure_id': self.id,
            'db_name': db_name,
            'name': backup_filename,
            'backup_destination': self.backup_destination,
            'status': status,
            'is_manual': manual,
            'duration': (fields.Datetime.now() - start).total_seconds(),
            'message': message or '',
        })

    def _finalize_run(self, overall, failures):
        """Update the last-run status fields and send the notification."""
        values = {
            'last_backup_date': fields.Datetime.now(),
            'last_backup_status': overall,
        }
        if overall == 'success':
            values['last_success_date'] = fields.Datetime.now()
            values['generated_exception'] = False
        else:
            values['generated_exception'] = '; '.join(
                f"{name}: {message}" for name, message in failures)
        self.sudo().write(values)
        self._notify(overall == 'success')

    def _notify(self, success):
        """Send the success/failure email, honouring the notify settings."""
        if not (self.notify_user and self.user_id):
            return
        if success and self.notify_mode == 'failure':
            return
        template = (
            'auto_database_backup.mail_template_data_db_backup_successful'
            if success else
            'auto_database_backup.mail_template_data_db_backup_failed')
        self.env.ref(template).send_mail(self.id, force_send=True)

    def _backup_to_local(self, db_name, backup_filename, backup_time):
        """Store the backup on the local file system."""
        if not os.path.isdir(self.backup_path):
            os.makedirs(self.backup_path)
        backup_file = os.path.join(self.backup_path, backup_filename)
        with open(backup_file, "wb") as stream:
            self.dump_data(db_name, stream, self.backup_format,
                           self.backup_frequency)
        if self.auto_remove:
            for filename in os.listdir(self.backup_path):
                file = os.path.join(self.backup_path, filename)
                create_time = datetime.fromtimestamp(os.path.getctime(file))
                if (datetime.utcnow() - create_time).days >= \
                        self.days_to_remove:
                    os.remove(file)

    def _backup_to_ftp(self, db_name, backup_filename, backup_time):
        """Upload the backup to an FTP server."""
        ftp_server = ftplib.FTP()
        ftp_server.connect(self.ftp_host, int(self.ftp_port))
        ftp_server.login(self.ftp_user, self.ftp_password)
        ftp_server.encoding = "utf-8"
        temp = tempfile.NamedTemporaryFile(suffix='.%s' % self.backup_format)
        try:
            ftp_server.cwd(self.ftp_path)
        except ftplib.error_perm:
            ftp_server.mkd(self.ftp_path)
            ftp_server.cwd(self.ftp_path)
        with open(temp.name, "wb+") as tmp:
            self.dump_data(db_name, tmp, self.backup_format,
                           self.backup_frequency)
        with open(temp.name, "rb") as tmp:
            ftp_server.storbinary('STOR %s' % backup_filename, tmp)
        if self.auto_remove:
            for file in ftp_server.nlst():
                create_time = datetime.strptime(
                    ftp_server.sendcmd('MDTM ' + file)[4:], "%Y%m%d%H%M%S")
                if (datetime.now() - create_time).days >= self.days_to_remove:
                    ftp_server.delete(file)
        ftp_server.quit()

    def _backup_to_sftp(self, db_name, backup_filename, backup_time):
        """Upload the backup to an SFTP server."""
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(hostname=self.sftp_host, username=self.sftp_user,
                           password=self.sftp_password, port=self.sftp_port)
            sftp = client.open_sftp()
            temp = tempfile.NamedTemporaryFile(
                suffix='.%s' % self.backup_format)
            with open(temp.name, "wb+") as tmp:
                self.dump_data(db_name, tmp, self.backup_format,
                               self.backup_frequency)
            try:
                sftp.chdir(self.sftp_path)
            except IOError as error:
                if error.errno == errno.ENOENT:
                    sftp.mkdir(self.sftp_path)
                    sftp.chdir(self.sftp_path)
            sftp.put(temp.name, backup_filename)
            if self.auto_remove:
                expired = [
                    fl for fl in sftp.listdir()
                    if (fields.Datetime.now() - datetime.fromtimestamp(
                        sftp.stat(fl).st_mtime)).days >= self.days_to_remove]
                for file in expired:
                    sftp.unlink(file)
            sftp.close()
        finally:
            client.close()

    def _backup_to_google_drive(self, db_name, backup_filename, backup_time):
        """Upload the backup to Google Drive."""
        if not self.gdrive_token_validity or \
                self.gdrive_token_validity <= fields.Datetime.now():
            self.generate_gdrive_refresh_token()
        temp = tempfile.NamedTemporaryFile(suffix='.%s' % self.backup_format)
        with open(temp.name, "wb+") as tmp:
            self.dump_data(db_name, tmp, self.backup_format,
                           self.backup_frequency)
        headers = {"Authorization": "Bearer %s" % self.gdrive_access_token}
        para = {"name": backup_filename,
                "parents": [self.google_drive_folder_key]}
        with open(temp.name, "rb") as tmp:
            files = {
                'data': ('metadata', json.dumps(para),
                         'application/json; charset=UTF-8'),
                'file': tmp,
            }
            requests.post(
                f"{GOOGLE_API_BASE_URL}/upload/drive/v3/files"
                f"?uploadType=multipart", headers=headers, files=files)
        if self.auto_remove:
            query = "parents = '%s'" % self.google_drive_folder_key
            files_req = requests.get(
                f"{GOOGLE_API_BASE_URL}/drive/v3/files?q=%s" % query,
                headers=headers)
            for file in files_req.json().get('files', []):
                file_date_req = requests.get(
                    f"{GOOGLE_API_BASE_URL}/drive/v3/files/%s"
                    f"?fields=createdTime" % file['id'], headers=headers)
                create_time = file_date_req.json()['createdTime'][
                              :19].replace('T', ' ')
                if (fields.Datetime.now() - datetime.strptime(
                        create_time, '%Y-%m-%d %H:%M:%S')).days >= \
                        self.days_to_remove:
                    requests.delete(
                        f"{GOOGLE_API_BASE_URL}/drive/v3/files/%s" %
                        file['id'], headers=headers)

    def _backup_to_google_cloud(self, db_name, backup_filename, backup_time):
        """Upload the backup to Google Cloud Storage."""
        if not (self.gcs_service_account_key and self.gcs_bucket):
            raise ValidationError(
                _("Google Cloud Storage credentials are incomplete."))
        client = self._get_gcs_client()
        bucket = client.bucket(self.gcs_bucket)
        blob_name = (f"{self.gcs_folder_name}/{backup_filename}"
                     if self.gcs_folder_name else backup_filename)
        temp = tempfile.NamedTemporaryFile(suffix='.%s' % self.backup_format)
        with open(temp.name, "wb+") as tmp:
            self.dump_data(db_name, tmp, self.backup_format,
                           self.backup_frequency)
        bucket.blob(blob_name).upload_from_filename(temp.name)
        if self.auto_remove:
            for blob in client.list_blobs(
                    self.gcs_bucket, prefix=self.gcs_folder_name or None):
                created = blob.time_created.replace(tzinfo=None)
                if (fields.Datetime.now() - created).days >= \
                        self.days_to_remove:
                    blob.delete()

    def action_view_scheduled_actions(self):
        """Open the automatic-backup scheduled actions (cron jobs)."""
        cron_ids = [
            self.env.ref(f'auto_database_backup.ir_cron_auto_db_backup_'
                         f'{frequency}').id
            for frequency in ('daily', 'weekly', 'monthly')
        ]
        return {
            'type': 'ir.actions.act_window',
            'name': _("Backup Scheduled Actions"),
            'res_model': 'ir.cron',
            'view_mode': 'list,form',
            'domain': [('id', 'in', cron_ids)],
        }

    def action_view_history(self):
        """Open the backup history of this configuration."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Backup History"),
            'res_model': 'db.backup.history',
            'view_mode': 'list,form',
            'domain': [('configure_id', '=', self.id)],
            'context': {'default_configure_id': self.id},
        }

    # -------------------------------------------------------------------------
    # Database dump helpers
    # -------------------------------------------------------------------------
    def dump_data(self, db_name, stream, backup_format, backup_frequency):
        """Dump database `db` into file-like object `stream` if stream is None
        return a file object with the dump. """
        cron_user_id = self.env.ref(
            f'auto_database_backup.ir_cron_auto_db_backup_'
            f'{backup_frequency}').user_id.id
        if cron_user_id != self.env.user.id:
            _logger.error(
                'Unauthorized database operation. Backups should only be '
                'available from the cron job.')
            raise ValidationError(_(
                "Unauthorized database operation. Backups should only be "
                "available from the cron job."))
        _logger.info('DUMP DB: %s format %s', db_name, backup_format)
        cmd = [find_pg_tool('pg_dump'), '--no-owner', db_name]
        env = exec_pg_environ()
        if backup_format == 'zip':
            with tempfile.TemporaryDirectory() as dump_dir:
                filestore = odoo.tools.config.filestore(db_name)
                cmd.insert(-1, '--file=' + os.path.join(dump_dir, 'dump.sql'))
                subprocess.run(cmd, env=env, stdout=subprocess.DEVNULL,
                               stderr=subprocess.STDOUT, check=True)
                if os.path.exists(filestore):
                    shutil.copytree(filestore,
                                    os.path.join(dump_dir, 'filestore'))
                with open(os.path.join(dump_dir, 'manifest.json'), 'w') as fh:
                    db_connection = odoo.sql_db.db_connect(db_name)
                    with db_connection.cursor() as cr:
                        json.dump(self._dump_db_manifest(cr), fh, indent=4)
                if stream:
                    odoo.tools.osutil.zip_dir(
                        dump_dir, stream, include_dir=False,
                        fnct_sort=lambda file_name: file_name != 'dump.sql')
                else:
                    t = tempfile.TemporaryFile()
                    odoo.tools.osutil.zip_dir(
                        dump_dir, t, include_dir=False,
                        fnct_sort=lambda file_name: file_name != 'dump.sql')
                    t.seek(0)
                    return t
        else:
            cmd.insert(-1, '--format=c')
            stdout = subprocess.Popen(
                cmd, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE).stdout
            if stream:
                shutil.copyfileobj(stdout, stream)
            else:
                return stdout

    def _dump_db_manifest(self, cr):
        """ This function generates a manifest dictionary for database dump."""
        pg_version = "%d.%d" % divmod(
            cr._obj.connection.server_version / 100, 100)
        cr.execute("SELECT name, latest_version FROM ir_module_module "
                   "WHERE state = 'installed'")
        modules = dict(cr.fetchall())
        manifest = {
            'odoo_dump': '1',
            'db_name': cr.dbname,
            'version': odoo.release.version,
            'version_info': odoo.release.version_info,
            'major_version': odoo.release.major_version,
            'pg_version': pg_version,
            'modules': modules,
        }
        return manifest
