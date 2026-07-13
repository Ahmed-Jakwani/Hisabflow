from odoo import fields, models


class SaasLog(models.Model):
    _name = "saas.log"
    _description = "SaaS Operation Log"
    _order = "create_date desc"

    log_date = fields.Datetime(
        string="Log Date",
        related="create_date",
        readonly=True,
        store=False,
    )

    client_id = fields.Many2one(
        "saas.client",
        string="Client",
        ondelete="cascade",
        required=True,
    )

    database_name = fields.Char(string="Database Name")
    operation = fields.Selection(
        [
            ("create_database", "Create Database"),
            ("check_database", "Check Database"),
            ("restore_database", "Restore Database"),
            ("health_check", "Health Check"),
            ("open_database", "Open Database"),
            ("backup_database", "Backup Database"),
            ("suspend_database", "Suspend Database"),
            ("activate_database", "Activate Database"),
            ("renew_subscription", "Renew Subscription"),
            ("check_subscription", "Check Subscription"),
            ("error", "Error"),
        ],
        string="Operation",
        required=True,
    )
    status = fields.Selection(
        [
            ("success", "Success"),
            ("warning", "Warning"),
            ("failed", "Failed"),
        ],
        string="Status",
        required=True,
        default="success",
    )

    message = fields.Text(string="Message")
    user_id = fields.Many2one(
        "res.users",
        string="User",
        default=lambda self: self.env.user,
        readonly=True,
    )