import re
import subprocess
from datetime import datetime

from odoo import api, fields, models, _
from odoo.exceptions import UserError

PG_BIN = "/usr/local/opt/postgresql@18/bin"
PG_PORT = "5433"
PG_OWNER = "odoo"
BACKUP_DIR = "/Users/hma/odoo/19/backups"


class SaasClient(models.Model):
    _name = "saas.client"
    _description = "SaaS Client"
    _rec_name = "name"

    name = fields.Char(required=True)
    customer_email = fields.Char()
    plan_id = fields.Many2one("saas.plan", required=True)
    database_name = fields.Char(required=True)
    subdomain = fields.Char()

    start_date = fields.Date(string="Start Date", default=fields.Date.today)
    trial_end_date = fields.Date(string="Trial End Date")
    subscription_end_date = fields.Date(string="Subscription End Date")

    subscription_state = fields.Selection(
        [
            ("trial", "Trial"),
            ("paid", "Paid"),
            ("expired", "Expired"),
            ("cancelled", "Cancelled"),
        ],
        string="Subscription Status",
        default="trial",
        required=True,
    )

    allowed_users = fields.Integer(string="Allowed Users", default=5)
    internal_notes = fields.Text(string="Internal Notes")

    log_ids = fields.One2many(
        "saas.log",
        "client_id",
        string="Operation Logs",
        readonly=True,
    )
    billing_cycle = fields.Selection(
        related="plan_id.billing_cycle",
        string="Billing Cycle",
        readonly=True,
    )

    storage_limit_mb = fields.Integer(
        related="plan_id.storage_limit_mb",
        string="Storage Limit (MB)",
        readonly=True,
    )

    allow_backup = fields.Boolean(
        related="plan_id.allow_backup",
        string="Allow Backup",
        readonly=True,
    )

    allow_restore = fields.Boolean(
        related="plan_id.allow_restore",
        string="Allow Restore",
        readonly=True,
    )

    auto_suspend_on_expiry = fields.Boolean(
        related="plan_id.auto_suspend_on_expiry",
        string="Auto Suspend On Expiry",
        readonly=True,
    )

    grace_period_days = fields.Integer(
        related="plan_id.grace_period_days",
        string="Grace Period Days",
        readonly=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("creating", "Creating"),
            ("active", "Active"),
            ("error", "Error"),
            ("suspended", "Suspended"),
        ],
        default="draft",
        required=True,
    )

    database_url = fields.Char(
        string="Database URL",
        compute="_compute_database_url",
    )

    database_exists = fields.Boolean(
        string="Database Exists",
        readonly=True,
    )

    database_size = fields.Char(
        string="Database Size",
        readonly=True,
    )

    last_health_check = fields.Datetime(
        string="Last Health Check",
        readonly=True,
    )

    health_message = fields.Text(
        string="Health Message",
        readonly=True,
    )

    backup_path = fields.Char(string="Last Backup Path", readonly=True)
    backup_date = fields.Datetime(string="Last Backup Date", readonly=True)

    error_message = fields.Text(readonly=True)

    def _notify(self, title, message, notification_type="success", sticky=False):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": title,
                "message": message,
                "type": notification_type,
                "sticky": sticky,
            },
        }

    def _log_operation(self, operation, status, message):
        self.ensure_one()

        if not self.id:
            return

        self.env["saas.log"].sudo().create(
            {
                "client_id": self.id,
                "database_name": self.database_name,
                "operation": operation,
                "status": status,
                "message": message,
                "user_id": self.env.user.id,
            }
        )

    @staticmethod
    def _convert_arabic_digits(value):
        if not isinstance(value, str):
            return value

        translation_map = str.maketrans(
            "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
            "01234567890123456789",
        )
        return value.translate(translation_map)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._normalize_digit_fields(vals)
        return super().create(vals_list)

    def write(self, vals):
        self._normalize_digit_fields(vals)
        return super().write(vals)

    @api.onchange("database_name", "subdomain", "customer_email")
    def _onchange_convert_arabic_digits(self):
        for record in self:
            record.database_name = self._convert_arabic_digits(record.database_name)
            record.subdomain = self._convert_arabic_digits(record.subdomain)
            record.customer_email = self._convert_arabic_digits(record.customer_email)

    def _normalize_digit_fields(self, vals):
        for field_name in ["database_name", "subdomain", "customer_email"]:
            if field_name in vals:
                vals[field_name] = self._convert_arabic_digits(vals[field_name])

    def action_save_record(self):
        self.ensure_one()

        self._log_operation(
            "check_database",
            "success",
            self.env._("Record saved successfully."),
        )

        return self._notify(
            self.env._("Save"),
            self.env._("Record saved successfully."),
            "success",
            False,
        )

    @api.onchange("plan_id")
    def _onchange_plan_id(self):
        for record in self:
            if not record.plan_id:
                continue

            record.allowed_users = record.plan_id.max_users

            today = fields.Date.today()

            if not record.start_date:
                record.start_date = today

            if record.plan_id.trial_days:
                record.trial_end_date = fields.Date.add(
                    record.start_date or today,
                    days=record.plan_id.trial_days,
                )

    @api.depends("database_name")
    def _compute_database_url(self):
        for record in self:
            if record.database_name:
                record.database_url = (
                    f"http://localhost:8069/web?db={record.database_name}"
                )
            else:
                record.database_url = False

    @api.constrains("database_name")
    def _check_database_name(self):
        pattern = re.compile(r"^[a-z0-9_]+$")

        for record in self:
            if record.database_name and not pattern.match(record.database_name):
                raise UserError(
                    self.env._(
                        "Database name can only contain lowercase letters, "
                        "numbers, and underscores."
                    )
                )

            if record.database_name and record.database_name.startswith("template_"):
                raise UserError(self.env._("Client database name cannot start with template_."))

    def _run_pg_command(self, command):
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )

    def _database_exists(self, database_name):
        if not database_name:
            return False

        safe_database_name = database_name.replace("'", "''")

        command = [
            f"{PG_BIN}/psql",
            "-p",
            PG_PORT,
            "-d",
            "postgres",
            "-tAc",
            f"SELECT 1 FROM pg_database WHERE datname='{safe_database_name}'",
        ]

        result = self._run_pg_command(command)
        return result.stdout.strip() == "1"

    def _get_database_size(self, database_name):
        if not database_name:
            return False

        safe_database_name = database_name.replace("'", "''")

        command = [
            f"{PG_BIN}/psql",
            "-p",
            PG_PORT,
            "-d",
            "postgres",
            "-tAc",
            f"SELECT pg_size_pretty(pg_database_size('{safe_database_name}'))",
        ]

        result = self._run_pg_command(command)
        return result.stdout.strip()


    def _terminate_database_connections(self, database_name):
        safe_database_name = database_name.replace("'", "''")

        command = [
            f"{PG_BIN}/psql",
            "-p",
            PG_PORT,
            "-d",
            "postgres",
            "-c",
            f"""
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = '{safe_database_name}'
              AND pid <> pg_backend_pid();
            """,
        ]

        self._run_pg_command(command)

    def _create_database_from_template(self, template_database, new_database):
        if not template_database:
            raise UserError(self.env._("Please set Template Database on the selected plan."))

        if not self._database_exists(template_database):
            raise UserError(
                self.env._("Template database does not exist: %s", template_database)
            )

        if self._database_exists(new_database):
            raise UserError(self.env._("Client database already exists: %s", new_database))

        self._terminate_database_connections(template_database)

        command = [
            f"{PG_BIN}/createdb",
            "-p",
            PG_PORT,
            "-O",
            PG_OWNER,
            "-T",
            template_database,
            new_database,
        ]

        self._run_pg_command(command)

    def action_create_database(self):
        self.ensure_one()

        if self.state not in ["draft", "error"]:
            message = self.env._("Database can only be created from Draft or Error state.")

            self._log_operation(
                "create_database",
                "warning",
                message,
            )

            return self._notify(
                self.env._("Create Database"),
                message,
                "warning",
                True,
            )

        self.state = "creating"
        self.error_message = False

        try:
            template_database = self.plan_id.template_database
            new_database = self.database_name

            self._create_database_from_template(
                template_database=template_database,
                new_database=new_database,
            )

            self.state = "active"

            message = self.env._("Database created successfully: %s", new_database)

            self._log_operation(
                "create_database",
                "success",
                message,
            )

            return self._notify(
                self.env._("Create Database"),
                message,
                "success",
                False,
            )

        except subprocess.CalledProcessError as error:
            self.state = "error"
            self.error_message = error.stderr or error.stdout or str(error)

            self._log_operation(
                "create_database",
                "failed",
                self.error_message,
            )

            return self._notify(
                self.env._("Create Database Failed"),
                self.error_message,
                "danger",
                True,
            )

        except Exception as error:
            self.state = "error"
            self.error_message = str(error)

            self._log_operation(
                "create_database",
                "failed",
                self.error_message,
            )

            return self._notify(
                self.env._("Create Database Failed"),
                self.error_message,
                "danger",
                True,
            )

    def action_check_database(self):
        self.ensure_one()

        try:
            if not self.database_name:
                message = self.env._("Database name is required.")

                self._log_operation(
                    "check_database",
                    "warning",
                    message,
                )

                return self._notify(
                    self.env._("Database Check"),
                    message,
                    "warning",
                    True,
                )

            if self._database_exists(self.database_name):
                if self.state != "suspended":
                    self.state = "active"

                self.error_message = False
                message = self.env._("Database exists: %s", self.database_name)

                self._log_operation(
                    "check_database",
                    "success",
                    message,
                )

                return self._notify(
                    self.env._("Database Check"),
                    message,
                    "success",
                    False,
                )

            self.state = "error"
            self.error_message = self.env._("Database does not exist.")
            message = self.env._("Database does not exist: %s", self.database_name)

            self._log_operation(
                "check_database",
                "failed",
                message,
            )

            return self._notify(
                self.env._("Database Check"),
                message,
                "danger",
                True,
            )

        except subprocess.CalledProcessError as error:
            self.state = "error"
            self.error_message = error.stderr or error.stdout or str(error)

            self._log_operation(
                "check_database",
                "failed",
                self.error_message,
            )

            return self._notify(
                self.env._("Database Check Failed"),
                self.error_message,
                "danger",
                True,
            )

        except Exception as error:
            self.state = "error"
            self.error_message = str(error)

            self._log_operation(
                "check_database",
                "failed",
                self.error_message,
            )

            return self._notify(
                self.env._("Database Check Failed"),
                self.error_message,
                "danger",
                True,
            )

    def action_open_database(self):
        self.ensure_one()

        if not self.database_name:
            message = self.env._("Database name is required.")

            self._log_operation(
                "open_database",
                "warning",
                message,
            )

            return self._notify(
                self.env._("Open Database"),
                message,
                "warning",
                True,
            )

        if not self._database_exists(self.database_name):
            self.state = "error"
            self.error_message = self.env._("Database does not exist.")
            message = self.env._("Database does not exist: %s", self.database_name)

            self._log_operation(
                "open_database",
                "failed",
                message,
            )

            return self._notify(
                self.env._("Open Database"),
                message,
                "danger",
                True,
            )

        message = self.env._("Database opened: %s", self.database_name)

        self._log_operation(
            "open_database",
            "success",
            message,
        )

        return {
            "type": "ir.actions.act_url",
            "url": f"/web?db={self.database_name}",
            "target": "new",
        }

    def action_backup_database(self):
        self.ensure_one()
        if not self.allow_backup:
            message = self.env._("Backup is not allowed for this plan.")

            self._log_operation(
                "backup_database",
                "warning",
                message,
            )

            return self._notify(
                self.env._("Backup Database"),
                message,
                "warning",
                True,
            )
        if not self.database_name:
            message = self.env._("Database name is required.")

            self._log_operation(
                "backup_database",
                "warning",
                message,
            )

            return self._notify(
                self.env._("Backup Database"),
                message,
                "warning",
                True,
            )

        if not self._database_exists(self.database_name):
            self.state = "error"
            self.error_message = self.env._("Database does not exist.")
            message = self.env._("Database does not exist: %s", self.database_name)

            self._log_operation(
                "backup_database",
                "failed",
                message,
            )

            return self._notify(
                self.env._("Backup Database"),
                message,
                "danger",
                True,
            )

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = f"{BACKUP_DIR}/{self.database_name}_{timestamp}.dump"

        try:
            subprocess.run(
                ["mkdir", "-p", BACKUP_DIR],
                check=True,
                capture_output=True,
                text=True,
            )

            command = [
                f"{PG_BIN}/pg_dump",
                "-p",
                PG_PORT,
                "-Fc",
                "-f",
                backup_file,
                self.database_name,
            ]

            self._run_pg_command(command)

            self.backup_path = backup_file
            self.backup_date = fields.Datetime.now()
            self.error_message = False

            message = self.env._("Backup created successfully: %s", backup_file)

            self._log_operation(
                "backup_database",
                "success",
                message,
            )

            return self._notify(
                self.env._("Backup Database"),
                message,
                "success",
                False,
            )

        except subprocess.CalledProcessError as error:
            self.state = "error"
            self.error_message = error.stderr or error.stdout or str(error)

            self._log_operation(
                "backup_database",
                "failed",
                self.error_message,
            )

            return self._notify(
                self.env._("Backup Failed"),
                self.error_message,
                "danger",
                True,
            )

        except Exception as error:
            self.state = "error"
            self.error_message = str(error)

            self._log_operation(
                "backup_database",
                "failed",
                self.error_message,
            )

            return self._notify(
                self.env._("Backup Failed"),
                self.error_message,
                "danger",
                True,
            )

    def action_restore_last_backup(self):
        self.ensure_one()
        if not self.allow_restore:
            message = self.env._("Restore is not allowed for this plan.")

            self._log_operation(
                "restore_database",
                "warning",
                message,
            )

            return self._notify(
                self.env._("Restore Database"),
                message,
                "warning",
                True,
            )
        if not self.database_name:
            message = self.env._("Database name is required.")

            self._log_operation(
                "restore_database",
                "warning",
                message,
            )

            return self._notify(
                self.env._("Restore Database"),
                message,
                "warning",
                True,
            )

        if not self.backup_path:
            message = self.env._("No backup file found for this client.")

            self._log_operation(
                "restore_database",
                "warning",
                message,
            )

            return self._notify(
                self.env._("Restore Database"),
                message,
                "warning",
                True,
            )

        if not self._database_exists(self.database_name):
            self.state = "error"
            self.error_message = self.env._("Database does not exist.")
            message = self.env._("Database does not exist: %s", self.database_name)

            self._log_operation(
                "restore_database",
                "failed",
                message,
            )

            return self._notify(
                self.env._("Restore Database"),
                message,
                "danger",
                True,
            )

        try:
            self._terminate_database_connections(self.database_name)

            command = [
                f"{PG_BIN}/pg_restore",
                "-p",
                PG_PORT,
                "--clean",
                "--if-exists",
                "--no-owner",
                "-d",
                self.database_name,
                self.backup_path,
            ]

            self._run_pg_command(command)

            self.state = "active"
            self.error_message = False

            message = self.env._("Backup restored successfully: %s", self.backup_path)

            self._log_operation(
                "restore_database",
                "success",
                message,
            )

            return self._notify(
                self.env._("Restore Database"),
                message,
                "success",
                False,
            )

        except subprocess.CalledProcessError as error:
            self.state = "error"
            self.error_message = error.stderr or error.stdout or str(error)

            self._log_operation(
                "restore_database",
                "failed",
                self.error_message,
            )

            return self._notify(
                self.env._("Restore Failed"),
                self.error_message,
                "danger",
                True,
            )

        except Exception as error:
            self.state = "error"
            self.error_message = str(error)

            self._log_operation(
                "restore_database",
                "failed",
                self.error_message,
            )

            return self._notify(
                self.env._("Restore Failed"),
                self.error_message,
                "danger",
                True,
            )

    def action_suspend_database(self):
        self.ensure_one()

        if self.state != "active":
            message = self.env._("Only active databases can be suspended.")

            self._log_operation(
                "suspend_database",
                "warning",
                message,
            )

            return self._notify(
                self.env._("Suspend Database"),
                message,
                "warning",
                True,
            )

        if not self._database_exists(self.database_name):
            self.state = "error"
            self.error_message = self.env._("Database does not exist.")
            message = self.env._("Database does not exist: %s", self.database_name)

            self._log_operation(
                "suspend_database",
                "failed",
                message,
            )

            return self._notify(
                self.env._("Suspend Database"),
                message,
                "danger",
                True,
            )

        self.state = "suspended"
        self.error_message = False

        message = self.env._("Database suspended successfully: %s", self.database_name)

        self._log_operation(
            "suspend_database",
            "success",
            message,
        )

        return self._notify(
            self.env._("Suspend Database"),
            message,
            "success",
            False,
        )

    def action_activate_database(self):
        self.ensure_one()

        if self.state != "suspended":
            message = self.env._("Only suspended databases can be activated.")

            self._log_operation(
                "activate_database",
                "warning",
                message,
            )

            return self._notify(
                self.env._("Activate Database"),
                message,
                "warning",
                True,
            )

        if not self._database_exists(self.database_name):
            self.state = "error"
            self.error_message = self.env._("Database does not exist.")
            message = self.env._("Database does not exist: %s", self.database_name)

            self._log_operation(
                "activate_database",
                "failed",
                message,
            )

            return self._notify(
                self.env._("Activate Database"),
                message,
                "danger",
                True,
            )

        self.state = "active"
        self.error_message = False

        message = self.env._("Database activated successfully: %s", self.database_name)

        self._log_operation(
            "activate_database",
            "success",
            message,
        )

        return self._notify(
            self.env._("Activate Database"),
            message,
            "success",
            False,
        )

    def action_renew_subscription(self):
        self.ensure_one()

        today = fields.Date.today()

        self.subscription_state = "paid"
        self.subscription_end_date = fields.Date.add(today, months=1)
        self.error_message = False

        message = self.env._("Subscription renewed until: %s", self.subscription_end_date)

        self._log_operation(
            "renew_subscription",
            "success",
            message,
        )

        return self._notify(
            self.env._("Renew Subscription"),
            message,
            "success",
            False,
        )

    def _set_expired(self, reason):
        self.ensure_one()

        self.subscription_state = "expired"
        self.error_message = reason

        return True

    @api.model
    def _cron_check_subscriptions(self):
        today = fields.Date.today()

        clients = self.search(
            [
                ("subscription_state", "in", ["trial", "paid"]),
            ]
        )

        expired_count = 0

        for client in clients:
            expiry_date = False
            reason = False

            if (
                client.subscription_state == "trial"
                and client.trial_end_date
                and client.trial_end_date < today
            ):
                expiry_date = client.trial_end_date
                reason = self.env._("Trial period expired on: %s", client.trial_end_date)

            elif (
                client.subscription_state == "paid"
                and client.subscription_end_date
                and client.subscription_end_date < today
            ):
                expiry_date = client.subscription_end_date
                reason = self.env._("Subscription expired on: %s", client.subscription_end_date)

            if not reason:
                continue

            client._set_expired(reason)

            grace_days = client.grace_period_days or 0
            suspend_date = fields.Date.add(expiry_date, days=grace_days)

            if (
                client.auto_suspend_on_expiry
                and today > suspend_date
                and client.state not in ["suspended", "error"]
            ):
                client.state = "suspended"
                reason = self.env._("Client suspended after subscription expiry grace period.")

            client._log_operation(
                "check_subscription",
                "warning",
                reason,
            )

            expired_count += 1

        return expired_count

    def action_check_subscription(self):
        self.ensure_one()

        today = fields.Date.today()

        if (
            self.subscription_state == "trial"
            and self.trial_end_date
            and self.trial_end_date < today
        ):
            reason = self.env._("Trial period expired on: %s", self.trial_end_date)
            self._set_expired(reason)

            message = self.env._("Trial period expired. Client marked as expired.")

            self._log_operation(
                "check_subscription",
                "warning",
                message,
            )

            return self._notify(
                self.env._("Subscription Check"),
                message,
                "warning",
                True,
            )

        if (
            self.subscription_state == "paid"
            and self.subscription_end_date
            and self.subscription_end_date < today
        ):
            reason = self.env._("Subscription expired on: %s", self.subscription_end_date)
            self._set_expired(reason)

            message = self.env._("Subscription expired. Client marked as expired.")

            self._log_operation(
                "check_subscription",
                "warning",
                message,
            )

            return self._notify(
                self.env._("Subscription Check"),
                message,
                "warning",
                True,
            )

        message = self.env._("Subscription is still valid.")

        self._log_operation(
            "check_subscription",
            "success",
            message,
        )

        return self._notify(
            self.env._("Subscription Check"),
            message,
            "success",
            False,
        )



    def action_mark_subscription_expired(self):
        self.ensure_one()

        if self.subscription_state == "expired":
            message = self.env._("Subscription is already expired.")

            self._log_operation(
                "check_subscription",
                "warning",
                message,
            )

            return self._notify(
                self.env._("Subscription Check"),
                message,
                "warning",
                True,
            )

        self.subscription_state = "expired"

        message = self.env._("Subscription marked as expired manually.")

        self._log_operation(
            "check_subscription",
            "warning",
            message,
        )

        return self._notify(
            self.env._("Subscription Check"),
            message,
            "warning",
            False,
        )


    def action_cancel_subscription(self):
        self.ensure_one()

        if self.subscription_state == "cancelled":
            message = self.env._("Subscription is already cancelled.")

            self._log_operation(
                "check_subscription",
                "warning",
                message,
            )

            return self._notify(
                self.env._("Cancel Subscription"),
                message,
                "warning",
                True,
            )

        self.subscription_state = "cancelled"

        if self.state == "active":
            self.state = "suspended"

        message = self.env._("Subscription cancelled successfully.")

        self._log_operation(
            "check_subscription",
            "warning",
            message,
        )

        return self._notify(
            self.env._("Cancel Subscription"),
            message,
            "success",
            False,
        )

    def action_health_check(self):
        self.ensure_one()

        if not self.database_name:
            message = self.env._("Database name is required.")

            self.database_exists = False
            self.database_size = False
            self.last_health_check = fields.Datetime.now()
            self.health_message = message

            self._log_operation(
                "health_check",
                "warning",
                message,
            )

            return self._notify(
                self.env._("Health Check"),
                message,
                "warning",
                True,
            )

        try:
            exists = self._database_exists(self.database_name)

            self.database_exists = exists
            self.last_health_check = fields.Datetime.now()

            if not exists:
                message = self.env._("Database does not exist: %s", self.database_name)

                self.database_size = False
                self.health_message = message
                self.state = "error"
                self.error_message = message

                self._log_operation(
                    "health_check",
                    "failed",
                    message,
                )

                return self._notify(
                    self.env._("Health Check"),
                    message,
                    "danger",
                    True,
                )

            size = self._get_database_size(self.database_name)

            self.database_size = size
            self.health_message = self.env._("Database is healthy. Size: %s", size)
            self.error_message = False

            if self.state != "suspended":
                self.state = "active"

            self._log_operation(
                "health_check",
                "success",
                self.health_message,
            )

            return self._notify(
                self.env._("Health Check"),
                self.health_message,
                "success",
                False,
            )

        except subprocess.CalledProcessError as error:
            self.database_exists = False
            self.database_size = False
            self.last_health_check = fields.Datetime.now()
            self.health_message = error.stderr or error.stdout or str(error)
            self.error_message = self.health_message

            self._log_operation(
                "health_check",
                "failed",
                self.health_message,
            )

            return self._notify(
                self.env._("Health Check Failed"),
                self.health_message,
                "danger",
                True,
            )

        except Exception as error:
            self.database_exists = False
            self.database_size = False
            self.last_health_check = fields.Datetime.now()
            self.health_message = str(error)
            self.error_message = self.health_message

            self._log_operation(
                "health_check",
                "failed",
                self.health_message,
            )

            return self._notify(
                self.env._("Health Check Failed"),
                self.health_message,
                "danger",
                True,
            )