from odoo import api, fields, models


class SaasPlan(models.Model):
    _name = "saas.plan"
    _description = "SaaS Plan"

    # =============================
    # BASIC INFORMATION
    # =============================
    name = fields.Char(
        string="Name",
        required=True,
    )

    code = fields.Char(
        string="Code",
        required=True,
    )

    description = fields.Text(
        string="Description",
    )

    active = fields.Boolean(
        string="Active",
        default=True,
    )

    # =============================
    # PRICING
    # =============================
    monthly_price = fields.Float(
        string="Monthly Price",
    )

    billing_cycle = fields.Selection(
        [
            ("monthly", "Monthly"),
            ("quarterly", "Quarterly"),
            ("yearly", "Yearly"),
        ],
        string="Billing Cycle",
        default="monthly",
        required=True,
    )

    trial_days = fields.Integer(
        string="Trial Days",
        default=14,
    )

    # =============================
    # DATABASE TEMPLATE
    # =============================
    template_database = fields.Char(
        string="Template Database",
        required=True,
        help="Existing Odoo database used as a template for new SaaS clients.",
    )

    # =============================
    # LIMITS & FEATURES
    # =============================
    max_users = fields.Integer(
        string="Max Users",
        default=5,
    )

    storage_limit_mb = fields.Integer(
        string="Storage Limit (MB)",
        default=1024,
    )

    allow_backup = fields.Boolean(
        string="Allow Backup",
        default=True,
    )

    allow_restore = fields.Boolean(
        string="Allow Restore",
        default=True,
    )

    auto_suspend_on_expiry = fields.Boolean(
        string="Auto Suspend On Expiry",
        default=True,
    )

    grace_period_days = fields.Integer(
        string="Grace Period Days",
        default=0,
    )

    # =============================
    # HELPERS
    # =============================
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

    @staticmethod
    def _convert_arabic_digits(value):
        if not isinstance(value, str):
            return value

        translation_map = str.maketrans(
            "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
            "01234567890123456789",
        )

        return value.translate(translation_map)

    def _normalize_digit_fields(self, vals):
        for field_name in ["code", "template_database"]:
            if field_name in vals:
                vals[field_name] = self._convert_arabic_digits(vals[field_name])

    # =============================
    # ORM OVERRIDES
    # =============================
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._normalize_digit_fields(vals)

        return super().create(vals_list)

    def write(self, vals):
        self._normalize_digit_fields(vals)

        return super().write(vals)

    # =============================
    # ONCHANGE
    # =============================
    @api.onchange("code", "template_database")
    def _onchange_convert_arabic_digits(self):
        for record in self:
            record.code = self._convert_arabic_digits(record.code)
            record.template_database = self._convert_arabic_digits(
                record.template_database
            )

    # =============================
    # ACTIONS
    # =============================
    def action_save_record(self):
        self.ensure_one()

        return self._notify(
            self.env._("Save"),
            self.env._("Record saved successfully."),
            "success",
            False,
        )