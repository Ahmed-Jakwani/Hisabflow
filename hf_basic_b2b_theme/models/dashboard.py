# -*- coding: utf-8 -*-
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from odoo import api, fields, models
from odoo.exceptions import AccessError
from odoo.osv import expression

_logger = logging.getLogger(__name__)


class PosOrder(models.Model):
    _inherit = "pos.order"

    hf_kitchen_state = fields.Selection(
        [
            ("new", "New"),
            ("preparing", "Preparing"),
            ("ready", "Ready"),
            ("completed", "Completed"),
        ],
        string="Kitchen Status",
        default="new",
        copy=False,
        index=True,
    )



class HisabFlowB2BDashboard(models.AbstractModel):
    _name = "hf.b2b.dashboard"
    _description = "HisabFlow Basic B2B Dashboard Service"

    # ---------------------------------------------------------------------
    # Generic helpers
    # ---------------------------------------------------------------------
    def _has_model(self, model_name):
        return model_name in self.env.registry.models

    def _model(self, model_name):
        return self.env[model_name] if self._has_model(model_name) else None

    def _company_domain(self, model, company=None):
        company = company or self.env.company
        if model is not None and "company_id" in model._fields:
            return [("company_id", "=", company.id)]
        return []

    def _date_bounds(self, date_from=None, date_to=None):
        today = fields.Date.context_today(self)
        if date_from:
            start = fields.Date.to_date(date_from)
        else:
            # Use year-to-date by default so existing business activity is
            # reflected on first load instead of showing misleading zeros when
            # the current month has no transactions yet. Explicit date filters
            # still override this range.
            start = today.replace(month=1, day=1)
        if date_to:
            end = fields.Date.to_date(date_to)
        else:
            end = today
        if end < start:
            start, end = end, start
        return start, end

    def _dt_domain(self, field_name, start, end):
        """Build an inclusive local-date domain for UTC datetime fields.

        Odoo stores datetimes in UTC but users choose dashboard dates in their
        local timezone.  Converting local midnight boundaries to UTC avoids
        the common midnight mismatch where a record displayed as 29 Aug is
        excluded by a 28/29 Aug UTC boundary.
        """
        tz_name = self.env.context.get("tz") or self.env.user.tz or "UTC"
        try:
            user_tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            user_tz = timezone.utc

        local_start = datetime.combine(start, time.min).replace(tzinfo=user_tz)
        local_end = datetime.combine(end + timedelta(days=1), time.min).replace(tzinfo=user_tz)
        start_dt = local_start.astimezone(timezone.utc).replace(tzinfo=None)
        end_dt = local_end.astimezone(timezone.utc).replace(tzinfo=None)
        return [
            (field_name, ">=", fields.Datetime.to_string(start_dt)),
            (field_name, "<", fields.Datetime.to_string(end_dt)),
        ]

    def _date_domain(self, field_name, start, end):
        return [(field_name, ">=", fields.Date.to_string(start)),
                (field_name, "<=", fields.Date.to_string(end))]

    def _safe_count(self, model_name, domain=None):
        """Count exactly the records the current user can drill into.

        Dashboard actions are opened with ``model.search(domain)`` semantics.
        Using the same search here prevents a KPI from showing 0 while its
        click-through action still contains visible records.
        """
        model = self._model(model_name)
        if model is None:
            return 0
        domain = domain or []
        try:
            return len(model.search(domain))
        except Exception:
            _logger.exception("HisabFlow count failed for %s with domain %s", model_name, domain)
            return 0

    def _decorate_metric_counts(self, data):
        """Recompute KPI values from the exact recordset opened by the tile.

        Every clickable KPI carries an ``aggregate`` descriptor.  We search
        the *same model and domain* used by its drill-down action and derive
        both the record count and (where applicable) the displayed sum.  This
        guarantees that a tile cannot show 0 while clicking it opens records.
        """
        for collection_name in ("kpis", "summary"):
            for item in data.get(collection_name, []):
                if not isinstance(item, dict):
                    continue
                action = item.get("action")
                if not isinstance(action, dict):
                    continue
                model_name = action.get("model")
                model = self._model(model_name) if model_name else None
                if model is None:
                    continue
                domain = action.get("domain") or []
                try:
                    records = model.search(domain)
                    item["record_count"] = len(records)

                    aggregate = item.get("aggregate") or {}
                    operation = aggregate.get("operation")
                    if operation == "count":
                        item["value"] = len(records)
                    elif operation == "sum":
                        field_name = aggregate.get("field")
                        if field_name and field_name in model._fields:
                            total = sum((value or 0.0) for value in records.mapped(field_name))
                            if aggregate.get("absolute"):
                                total = abs(total)
                            item["value"] = float(total or 0.0)
                except Exception:
                    _logger.exception(
                        "HisabFlow KPI aggregation failed for %s with domain %s",
                        model_name, domain,
                    )
        return data

    def _safe_sum(self, model_name, field_name, domain=None):
        """Sum a field from the same accessible recordset used by drill-down."""
        model = self._model(model_name)
        if model is None or field_name not in model._fields:
            return 0.0
        try:
            records = model.search(domain or [])
            values = records.mapped(field_name)
            return float(sum(value or 0.0 for value in values) or 0.0)
        except Exception:
            _logger.exception(
                "HisabFlow sum failed for %s.%s with domain %s",
                model_name, field_name, domain or [],
            )
            return 0.0

    def _safe_records(self, model_name, domain=None, fields_list=None, order=None, limit=None):
        model = self._model(model_name)
        if model is None:
            return []
        usable_fields = [name for name in (fields_list or ["display_name"]) if name in model._fields]
        try:
            return model.search_read(domain or [], usable_fields, order=order, limit=limit)
        except Exception:
            return []

    def _display_name(self, value):
        if isinstance(value, (list, tuple)) and len(value) > 1:
            return value[1]
        return value or "—"

    def _number(self, value):
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _status_label(self, value):
        mapping = {
            "draft": "Draft",
            "sent": "Sent",
            "sale": "Confirmed",
            "done": "Completed",
            "cancel": "Cancelled",
            "purchase": "Confirmed",
            "to approve": "To Approve",
            "posted": "Posted",
            "paid": "Paid",
            "partial": "Partially Paid",
            "not_paid": "Unpaid",
            "in_payment": "In Payment",
            "assigned": "Ready",
            "confirmed": "Waiting",
            "waiting": "Waiting",
            "progress": "In Progress",
            "to_close": "To Close",
            "closed": "Closed",
            "opening_control": "Opening",
            "closing_control": "Closing",
            "opened": "Open",
            "invoiced": "Invoiced",
        }
        return mapping.get(value, str(value or "—").replace("_", " ").title())

    def _status_tone(self, value):
        if value in {"sale", "done", "purchase", "posted", "paid", "assigned", "closed", "invoiced"}:
            return "success"
        if value in {"sent", "confirmed", "waiting", "to approve", "partial", "in_payment", "progress", "opened"}:
            return "info"
        if value in {"cancel", "not_paid"}:
            return "danger"
        return "warning"

    def _bucket_period(self, start, end):
        days = max((end - start).days + 1, 1)
        if days <= 14:
            return "day"
        if days <= 90:
            return "week"
        return "month"

    def _period_key(self, dt_value, period):
        if not dt_value:
            return None
        if isinstance(dt_value, str):
            try:
                dt_value = fields.Datetime.to_datetime(dt_value)
            except Exception:
                try:
                    dt_value = fields.Date.to_date(dt_value)
                except Exception:
                    return None
        d = dt_value.date() if isinstance(dt_value, datetime) else dt_value
        if period == "day":
            return d, d.strftime("%d %b")
        if period == "week":
            monday = d - timedelta(days=d.weekday())
            return monday, monday.strftime("%d %b")
        first = d.replace(day=1)
        return first, first.strftime("%b %Y")

    def _trend_from_records(self, records, date_field, primary_field, secondary_field=None,
                            start=None, end=None):
        period = self._bucket_period(start, end)
        grouped = defaultdict(lambda: {"primary": 0.0, "secondary": 0.0, "label": ""})
        for row in records:
            key_data = self._period_key(row.get(date_field), period)
            if not key_data:
                continue
            key, label = key_data
            grouped[key]["label"] = label
            grouped[key]["primary"] += self._number(row.get(primary_field))
            if secondary_field:
                grouped[key]["secondary"] += self._number(row.get(secondary_field))
        return [
            {
                "label": grouped[key]["label"],
                "primary": round(grouped[key]["primary"], 2),
                "secondary": round(grouped[key]["secondary"], 2),
            }
            for key in sorted(grouped)
        ]

    def _currency(self):
        currency = self.env.company.currency_id
        return {
            "name": currency.name,
            "symbol": currency.symbol or currency.name,
            "position": currency.position,
            "decimal_places": currency.decimal_places,
        }

    def _base(self, kind, start, end):
        title_map = {
            "overview": ("Dashboard", "Track your business performance from one live workspace."),
            "sales": ("Sales", "Track quotations, sales orders, revenue and customer receivables."),
            "purchases": ("Purchases", "Manage procurement, suppliers and incoming goods."),
            "inventory": ("Inventory", "Track stock levels, movements and warehouse availability."),
            "accounting": ("Accounting", "Track cash, receivables, payables, income and expenses."),
            "manufacturing": ("Manufacturing", "Plan, execute and track production operations."),
            "pos": ("Point of Sale", "Monitor live POS sales, sessions, payments and recent orders."),
            "kitchen": ("Kitchen Display", "Live operational view of POS orders for preparation teams."),
            "receipt": ("Customer Receipt", "Preview the latest POS order using the HisabFlow receipt design."),
        }
        title, subtitle = title_map.get(kind, title_map["overview"])
        return {
            "kind": kind,
            "title": title,
            "subtitle": subtitle,
            "date_from": fields.Date.to_string(start),
            "date_to": fields.Date.to_string(end),
            "company": self.env.company.name,
            "user": self.env.user.name,
            "currency": self._currency(),
            "kpis": [],
            "trend": [],
            "rows": [],
            "summary": [],
            "quick_actions": [],
            "all_records_action": False,
            "empty_message": "No records were found for the selected period.",
        }

    def _action_payload(self, name, model, domain=None, context=None, views=None):
        """Return a JSON-safe window action used by clickable dashboard metrics."""
        if not self._has_model(model):
            return False
        try:
            self.env[model].check_access("read")
        except AccessError:
            return False
        return {
            "name": name,
            "model": model,
            "domain": domain or [],
            "context": context or {},
            "views": views or ["list", "form"],
        }

    @api.model
    def resolve_dashboard_action(self, action_spec):
        """Build and validate a drill-down action on the server.

        The dashboard is intentionally data-driven, so KPI domains change with
        the selected company and date range.  Resolving the final action here
        lets Odoo validate model access and the domain before the web client
        attempts to open a controller.
        """
        action_spec = action_spec if isinstance(action_spec, dict) else {}
        model_name = str(action_spec.get("model") or "").strip()
        if not model_name or not self._has_model(model_name):
            return {"warning": "The requested application is not installed."}

        model = self.env[model_name]
        try:
            model.check_access("read")
        except AccessError:
            return {
                "warning": (
                    f"You do not have permission to view "
                    f"{model._description or model_name} records."
                )
            }

        domain = action_spec.get("domain") or []
        try:
            domain = expression.normalize_domain(domain)
            # Validate field names and domain operators without loading a
            # complete recordset.
            model.search(domain, limit=1)
        except Exception:
            return {
                "warning": (
                    "This dashboard filter is no longer valid. "
                    "Please upgrade the HisabFlow theme module."
                )
            }

        requested_views = action_spec.get("views") or ["list", "form"]
        allowed_views = {"list", "form", "kanban", "calendar", "pivot", "graph"}
        view_types = []
        for view_type in requested_views:
            view_type = str(view_type or "").strip()
            if view_type in allowed_views and view_type not in view_types:
                view_types.append(view_type)
        if not view_types:
            view_types = ["list", "form"]

        action_context = action_spec.get("context")
        if not isinstance(action_context, dict):
            action_context = {}

        return {
            "type": "ir.actions.act_window",
            "name": str(action_spec.get("name") or model._description or "Records"),
            "res_model": model_name,
            "view_mode": ",".join(view_types),
            "views": [(False, view_type) for view_type in view_types],
            "domain": domain,
            "context": action_context,
            "target": "current",
        }

    @api.model
    def resolve_record_action(self, model_name, record_id, title=None):
        """Return an access-safe form action for a recent activity row."""
        model_name = str(model_name or "").strip()
        if not model_name or not self._has_model(model_name):
            return {"warning": "The requested record type is not available."}
        model = self.env[model_name]
        try:
            model.check_access("read")
            record = model.browse(int(record_id)).exists()
            if not record:
                return {"warning": "This record no longer exists."}
            record.check_access("read")
        except (AccessError, TypeError, ValueError):
            return {"warning": "You do not have permission to open this record."}
        return {
            "type": "ir.actions.act_window",
            "name": str(title or record.display_name or model._description),
            "res_model": model_name,
            "res_id": record.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
        }

    # ---------------------------------------------------------------------
    # Sales
    # ---------------------------------------------------------------------
    def _sales_data(self, start, end):
        data = self._base("sales", start, end)
        model = self._model("sale.order")
        company_domain = self._company_domain(model)
        period_domain = self._dt_domain("date_order", start, end)
        confirmed_domain = company_domain + period_domain + [("state", "in", ["sale", "done"])]
        quotation_domain = company_domain + period_domain + [("state", "in", ["draft", "sent"])]

        quotations = self._safe_count("sale.order", quotation_domain)
        confirmed = self._safe_count("sale.order", confirmed_domain)
        revenue = self._safe_sum("sale.order", "amount_total", confirmed_domain)
        receivable_domain = (
            self._company_domain(self._model("account.move"))
            + self._date_domain("invoice_date", start, end)
            + [
                ("state", "=", "posted"),
                ("move_type", "=", "out_invoice"),
                ("amount_residual", ">", 0),
            ]
        )
        receivables = self._safe_sum(
            "account.move", "amount_residual_signed", receivable_domain
        )
        data["kpis"] = [
            {
                "label": "Quotations", "value": quotations, "type": "number",
                "aggregate": {"operation": "count"},
                "hint": "Draft and sent", "icon": "fa-file-text-o", "tone": "teal",
                "action": self._action_payload("Quotations", "sale.order", quotation_domain),
            },
            {
                "label": "Confirmed Sales Orders", "value": confirmed, "type": "number",
                "aggregate": {"operation": "count"},
                "hint": "Current period", "icon": "fa-shopping-cart", "tone": "cyan",
                "action": self._action_payload("Confirmed Sales Orders", "sale.order", confirmed_domain),
            },
            {
                "label": "Revenue", "value": revenue, "type": "currency",
                "aggregate": {"operation": "sum", "field": "amount_total"},
                "hint": "Confirmed order value", "icon": "fa-line-chart", "tone": "green",
                "action": self._action_payload("Sales Revenue", "sale.order", confirmed_domain),
            },
            {
                "label": "Customer Receivables", "value": abs(receivables), "type": "currency",
                "aggregate": {"operation": "sum", "field": "amount_residual_signed", "absolute": True},
                "hint": "Open posted invoices", "icon": "fa-credit-card", "tone": "blue",
                "action": self._action_payload("Customer Receivables", "account.move", receivable_domain),
            },
        ]

        state_map = [
            ("draft", "New"), ("sent", "Quoted"), ("sale", "Confirmed"), ("done", "Delivered"), ("cancel", "Cancelled")
        ]
        data["pipeline"] = []
        for state, label in state_map:
            domain = company_domain + period_domain + [("state", "=", state)]
            data["pipeline"].append({
                "label": label,
                "count": self._safe_count("sale.order", domain),
                "value": self._safe_sum("sale.order", "amount_total", domain),
                "tone": self._status_tone(state),
                "action": self._action_payload(f"Sales - {label}", "sale.order", domain),
            })

        trend_records = self._safe_records(
            "sale.order", confirmed_domain,
            ["date_order", "amount_total", "amount_tax"], order="date_order asc"
        )
        data["trend"] = self._trend_from_records(
            trend_records, "date_order", "amount_total", "amount_tax", start, end
        )
        recent = self._safe_records(
            "sale.order", company_domain + period_domain,
            ["name", "partner_id", "date_order", "amount_total", "state", "user_id"],
            order="date_order desc", limit=8,
        )
        data["rows"] = [
            {
                "id": row["id"], "model": "sale.order", "name": row.get("name"),
                "partner": self._display_name(row.get("partner_id")), "date": row.get("date_order"),
                "amount": self._number(row.get("amount_total")),
                "status": self._status_label(row.get("state")),
                "tone": self._status_tone(row.get("state")),
                "owner": self._display_name(row.get("user_id")),
            }
            for row in recent
        ]
        data["quick_actions"] = [
            {"label": "New Quotation", "model": "sale.order", "icon": "fa-plus"},
            {"label": "Sales Orders", "app": "Sales", "icon": "fa-shopping-cart"},
            {"label": "Customers", "app": "Contacts", "icon": "fa-users"},
            {"label": "Reports", "app": "Sales", "icon": "fa-bar-chart"},
        ]
        return data

    # ---------------------------------------------------------------------
    # Purchases
    # ---------------------------------------------------------------------
    def _purchase_data(self, start, end):
        data = self._base("purchases", start, end)
        model = self._model("purchase.order")
        company_domain = self._company_domain(model)
        period_domain = self._dt_domain("date_order", start, end)
        po_domain = company_domain + period_domain + [("state", "in", ["purchase", "done"])]
        rfq_domain = company_domain + period_domain + [("state", "in", ["draft", "sent", "to approve"])]
        po_count = self._safe_count("purchase.order", po_domain)
        pending = self._safe_count("purchase.order", rfq_domain)
        purchase_value = self._safe_sum("purchase.order", "amount_total", po_domain)
        payable_domain = (
            self._company_domain(self._model("account.move"))
            + self._date_domain("invoice_date", start, end)
            + [
                ("state", "=", "posted"),
                ("move_type", "=", "in_invoice"),
                ("amount_residual", ">", 0),
            ]
        )
        payable = self._safe_sum("account.move", "amount_residual_signed", payable_domain)
        data["kpis"] = [
            {
                "label": "Total Purchase Orders", "value": po_count, "type": "number",
                "aggregate": {"operation": "count"},
                "hint": "Confirmed and done", "icon": "fa-file-text-o", "tone": "teal",
                "action": self._action_payload("Purchase Orders", "purchase.order", po_domain),
            },
            {
                "label": "Pending RFQs", "value": pending, "type": "number",
                "aggregate": {"operation": "count"},
                "hint": "Awaiting confirmation", "icon": "fa-list-alt", "tone": "cyan",
                "action": self._action_payload("Pending RFQs", "purchase.order", rfq_domain),
            },
            {
                "label": "Goods Received (Value)", "value": purchase_value, "type": "currency",
                "aggregate": {"operation": "sum", "field": "amount_total"},
                "hint": "Confirmed purchase value", "icon": "fa-truck", "tone": "green",
                "action": self._action_payload("Confirmed Purchase Orders", "purchase.order", po_domain),
            },
            {
                "label": "Payable Amount", "value": abs(payable), "type": "currency",
                "aggregate": {"operation": "sum", "field": "amount_residual_signed", "absolute": True},
                "hint": "Open posted vendor bills", "icon": "fa-credit-card", "tone": "blue",
                "action": self._action_payload("Vendor Payables", "account.move", payable_domain),
            },
        ]

        supplier_rows = self._safe_records(
            "purchase.order", po_domain,
            ["partner_id", "amount_total", "state"], order="amount_total desc"
        )
        suppliers = defaultdict(lambda: {"amount": 0.0, "orders": 0})
        for row in supplier_rows:
            name = self._display_name(row.get("partner_id"))
            suppliers[name]["amount"] += self._number(row.get("amount_total"))
            suppliers[name]["orders"] += 1
        data["suppliers"] = [
            {"name": name, "amount": values["amount"], "orders": values["orders"]}
            for name, values in sorted(suppliers.items(), key=lambda item: item[1]["amount"], reverse=True)[:6]
        ]

        trend_records = self._safe_records(
            "purchase.order", po_domain, ["date_order", "amount_total"], order="date_order asc"
        )
        data["trend"] = self._trend_from_records(trend_records, "date_order", "amount_total", None, start, end)
        recent = self._safe_records(
            "purchase.order", company_domain + period_domain,
            ["name", "partner_id", "date_order", "amount_total", "state"],
            order="date_order desc", limit=8,
        )
        data["rows"] = [
            {
                "id": row["id"], "model": "purchase.order", "name": row.get("name"),
                "partner": self._display_name(row.get("partner_id")), "date": row.get("date_order"),
                "amount": self._number(row.get("amount_total")),
                "status": self._status_label(row.get("state")), "tone": self._status_tone(row.get("state")),
            }
            for row in recent
        ]
        data["quick_actions"] = [
            {"label": "Create RFQ", "model": "purchase.order", "icon": "fa-file-text-o"},
            {"label": "Purchase Orders", "app": "Purchase", "icon": "fa-shopping-cart"},
            {"label": "Receive Products", "app": "Inventory", "icon": "fa-truck"},
            {"label": "Vendor Bill", "model": "account.move", "context": {"default_move_type": "in_invoice"}, "icon": "fa-file-text"},
        ]
        return data

    # ---------------------------------------------------------------------
    # Inventory
    # ---------------------------------------------------------------------
    def _inventory_data(self, start, end):
        data = self._base("inventory", start, end)
        quant_model = self._model("stock.quant")
        picking_model = self._model("stock.picking")
        company_domain = self._company_domain(quant_model)
        internal_domain = company_domain + [("location_id.usage", "=", "internal")]
        on_hand = self._safe_sum("stock.quant", "quantity", internal_domain)
        reserved = self._safe_sum("stock.quant", "reserved_quantity", internal_domain)
        incoming_domain = (
            self._company_domain(picking_model)
            + [("state", "not in", ["done", "cancel"]), ("picking_type_id.code", "=", "incoming")]
        )
        outgoing_domain = (
            self._company_domain(picking_model)
            + [("state", "not in", ["done", "cancel"]), ("picking_type_id.code", "=", "outgoing")]
        )
        incoming = self._safe_count("stock.picking", incoming_domain)
        outgoing = self._safe_count("stock.picking", outgoing_domain)
        low_stock = self._safe_count(
            "stock.warehouse.orderpoint",
            [("qty_to_order", ">", 0)],
        )
        data["kpis"] = [
            {
                "label": "On Hand Quantity", "value": on_hand, "type": "number",
                "aggregate": {"operation": "sum", "field": "quantity"},
                "hint": "All internal locations", "icon": "fa-cubes", "tone": "teal",
                "action": self._action_payload("Stock On Hand", "stock.quant", internal_domain),
            },
            {
                "label": "Reserved Quantity", "value": reserved, "type": "number",
                "aggregate": {"operation": "sum", "field": "reserved_quantity"},
                "hint": "Committed stock", "icon": "fa-shield", "tone": "green",
                "action": self._action_payload(
                    "Reserved Stock", "stock.quant", internal_domain + [("reserved_quantity", ">", 0)]
                ),
            },
            {
                "label": "Incoming Transfers", "value": incoming, "type": "number",
                "aggregate": {"operation": "count"},
                "hint": "Open receipts", "icon": "fa-truck", "tone": "blue",
                "action": self._action_payload("Incoming Transfers", "stock.picking", incoming_domain),
            },
            {
                "label": "Outgoing Deliveries", "value": outgoing, "type": "number",
                "aggregate": {"operation": "count"},
                "hint": "Open deliveries", "icon": "fa-truck", "tone": "navy",
                "action": self._action_payload("Outgoing Deliveries", "stock.picking", outgoing_domain),
            },
        ]

        quant_rows = self._safe_records(
            "stock.quant", internal_domain,
            ["location_id", "product_id", "quantity", "reserved_quantity"], order="quantity desc"
        )
        locations = defaultdict(lambda: {"on_hand": 0.0, "reserved": 0.0, "products": set()})
        for row in quant_rows:
            name = self._display_name(row.get("location_id"))
            locations[name]["on_hand"] += self._number(row.get("quantity"))
            locations[name]["reserved"] += self._number(row.get("reserved_quantity"))
            product = row.get("product_id")
            if product:
                locations[name]["products"].add(product[0])
        data["locations"] = [
            {
                "name": name,
                "on_hand": values["on_hand"],
                "reserved": values["reserved"],
                "products": len(values["products"]),
                "status": "Healthy" if values["on_hand"] > values["reserved"] else "Low Stock",
                "tone": "success" if values["on_hand"] > values["reserved"] else "warning",
            }
            for name, values in sorted(locations.items(), key=lambda item: item[1]["on_hand"], reverse=True)[:6]
        ]

        category_totals = defaultdict(float)
        for row in quant_rows:
            product = row.get("product_id")
            if not product:
                continue
            product_rec = self.env["product.product"].browse(product[0])
            category_totals[product_rec.categ_id.display_name] += self._number(row.get("quantity"))
        total_category_qty = sum(category_totals.values()) or 1.0
        data["categories"] = [
            {"name": name, "value": value, "percentage": round(value * 100 / total_category_qty, 1)}
            for name, value in sorted(category_totals.items(), key=lambda item: item[1], reverse=True)[:5]
        ]

        move_model = self._model("stock.move")
        move_domain = self._company_domain(move_model) + self._dt_domain("date", start, end) + [("state", "=", "done")]
        moves = self._safe_records(
            "stock.move", move_domain,
            ["date", "product_uom_qty", "location_id", "location_dest_id"], order="date asc"
        )
        period = self._bucket_period(start, end)
        grouped = defaultdict(lambda: {"label": "", "primary": 0.0, "secondary": 0.0})
        for row in moves:
            key_data = self._period_key(row.get("date"), period)
            if not key_data:
                continue
            key, label = key_data
            grouped[key]["label"] = label
            src_id = row.get("location_id", [False])[0] if row.get("location_id") else False
            dst_id = row.get("location_dest_id", [False])[0] if row.get("location_dest_id") else False
            qty = self._number(row.get("product_uom_qty"))
            if dst_id and self.env["stock.location"].browse(dst_id).usage == "internal":
                grouped[key]["primary"] += qty
            if src_id and self.env["stock.location"].browse(src_id).usage == "internal":
                grouped[key]["secondary"] += qty
        data["trend"] = [
            {"label": grouped[key]["label"], "primary": grouped[key]["primary"], "secondary": grouped[key]["secondary"]}
            for key in sorted(grouped)
        ]

        products = []
        product_totals = defaultdict(lambda: {"on_hand": 0.0, "reserved": 0.0, "name": "", "category": ""})
        for row in quant_rows:
            product = row.get("product_id")
            if not product:
                continue
            product_id = product[0]
            product_totals[product_id]["name"] = product[1]
            product_totals[product_id]["on_hand"] += self._number(row.get("quantity"))
            product_totals[product_id]["reserved"] += self._number(row.get("reserved_quantity"))
        for product_id, values in sorted(product_totals.items(), key=lambda item: item[1]["on_hand"], reverse=True)[:10]:
            rec = self.env["product.product"].browse(product_id)
            available = values["on_hand"] - values["reserved"]
            products.append({
                "id": product_id, "model": "product.product", "name": values["name"],
                "category": rec.categ_id.display_name, "on_hand": values["on_hand"],
                "reserved": values["reserved"], "available": available,
                "status": "Healthy" if available > 0 else "Critical",
                "tone": "success" if available > 0 else "danger",
            })
        data["rows"] = products
        data["quick_actions"] = [
            {"label": "Products", "app": "Inventory", "icon": "fa-cube"},
            {"label": "Transfers", "app": "Inventory", "icon": "fa-exchange"},
            {"label": "Receipts", "app": "Inventory", "icon": "fa-download"},
            {"label": "Delivery Orders", "app": "Inventory", "icon": "fa-truck"},
        ]
        return data

    # ---------------------------------------------------------------------
    # Accounting
    # ---------------------------------------------------------------------
    def _accounting_data(self, start, end):
        data = self._base("accounting", start, end)
        move_model = self._model("account.move")
        move_line_model = self._model("account.move.line")
        move_company_domain = self._company_domain(move_model)
        line_company_domain = self._company_domain(move_line_model)

        receivable_domain = move_company_domain + [
            ("state", "=", "posted"), ("move_type", "=", "out_invoice"), ("amount_residual", ">", 0)
        ]
        payable_domain = move_company_domain + [
            ("state", "=", "posted"), ("move_type", "=", "in_invoice"), ("amount_residual", ">", 0)
        ]
        receivable = abs(self._safe_sum(
            "account.move", "amount_residual_signed", receivable_domain,
        ))
        payable = abs(self._safe_sum(
            "account.move", "amount_residual_signed", payable_domain,
        ))
        income_domain = line_company_domain + self._date_domain("date", start, end) + [
            ("parent_state", "=", "posted"), ("account_id.account_type", "in", ["income", "income_other"])
        ]
        expense_domain = line_company_domain + self._date_domain("date", start, end) + [
            ("parent_state", "=", "posted"), ("account_id.account_type", "in", ["expense", "expense_depreciation", "expense_direct_cost"])
        ]
        income = abs(self._safe_sum("account.move.line", "balance", income_domain))
        expense = abs(self._safe_sum("account.move.line", "balance", expense_domain))
        # Only liquidity-side journal items belong in the cash/bank balance.
        # Summing every line of a bank journal also includes the counterpart
        # lines, so balanced journal entries cancel each other out to zero.
        bank_domain = line_company_domain + [
            ("parent_state", "=", "posted"),
            ("journal_id.type", "in", ["bank", "cash"]),
            ("account_type", "in", ["asset_cash", "liability_credit_card"]),
        ]
        bank_balance = self._safe_sum("account.move.line", "balance", bank_domain)
        net_profit = income - expense
        profit_domain = line_company_domain + self._date_domain("date", start, end) + [
            ("parent_state", "=", "posted"),
            ("account_id.account_type", "in", [
                "income", "income_other", "expense", "expense_depreciation", "expense_direct_cost"
            ]),
        ]
        data["kpis"] = [
            {
                "label": "Bank Balance", "value": bank_balance, "type": "currency",
                "aggregate": {"operation": "sum", "field": "balance"},
                "hint": "Bank and cash journal lines", "icon": "fa-bank", "tone": "teal",
                "action": self._action_payload("Bank and Cash Entries", "account.move.line", bank_domain),
            },
            {
                "label": "Total Receivables", "value": receivable, "type": "currency",
                "aggregate": {"operation": "sum", "field": "amount_residual_signed", "absolute": True},
                "hint": "Open customer invoices", "icon": "fa-credit-card", "tone": "blue",
                "action": self._action_payload("Customer Receivables", "account.move", receivable_domain),
            },
            {
                "label": "Total Payables", "value": payable, "type": "currency",
                "aggregate": {"operation": "sum", "field": "amount_residual_signed", "absolute": True},
                "hint": "Open vendor bills", "icon": "fa-file-text-o", "tone": "orange",
                "action": self._action_payload("Vendor Payables", "account.move", payable_domain),
            },
            {
                "label": "Net Profit", "value": net_profit, "type": "currency",
                "hint": "Selected period", "icon": "fa-line-chart", "tone": "green",
                "action": self._action_payload("Income and Expense Entries", "account.move.line", profit_domain),
            },
        ]

        line_rows = self._safe_records(
            "account.move.line", income_domain + [], ["date", "balance"], order="date asc"
        )
        expense_rows = self._safe_records(
            "account.move.line", expense_domain + [], ["date", "balance"], order="date asc"
        )
        period = self._bucket_period(start, end)
        grouped = defaultdict(lambda: {"label": "", "primary": 0.0, "secondary": 0.0})
        for row in line_rows:
            key_data = self._period_key(row.get("date"), period)
            if key_data:
                key, label = key_data
                grouped[key]["label"] = label
                grouped[key]["primary"] += abs(self._number(row.get("balance")))
        for row in expense_rows:
            key_data = self._period_key(row.get("date"), period)
            if key_data:
                key, label = key_data
                grouped[key]["label"] = label
                grouped[key]["secondary"] += abs(self._number(row.get("balance")))
        data["trend"] = [
            {"label": grouped[key]["label"], "primary": grouped[key]["primary"], "secondary": grouped[key]["secondary"]}
            for key in sorted(grouped)
        ]

        today = fields.Date.context_today(self)
        invoice_rows = self._safe_records(
            "account.move",
            move_company_domain + [("state", "=", "posted"), ("move_type", "=", "out_invoice"), ("amount_residual", ">", 0)],
            ["invoice_date_due", "amount_residual"], order="invoice_date_due asc"
        )
        aged = {"0-30 Days": 0.0, "31-60 Days": 0.0, "61-90 Days": 0.0, "90+ Days": 0.0}
        for row in invoice_rows:
            due = fields.Date.to_date(row.get("invoice_date_due")) if row.get("invoice_date_due") else today
            overdue = max((today - due).days, 0)
            amount = self._number(row.get("amount_residual"))
            if overdue <= 30:
                aged["0-30 Days"] += amount
            elif overdue <= 60:
                aged["31-60 Days"] += amount
            elif overdue <= 90:
                aged["61-90 Days"] += amount
            else:
                aged["90+ Days"] += amount
        aged_total = sum(aged.values()) or 1.0
        data["aged"] = [
            {"label": label, "value": value, "percentage": round(value * 100 / aged_total, 1), "tone": tone}
            for (label, value), tone in zip(aged.items(), ["success", "info", "warning", "danger"])
        ]

        recent = self._safe_records(
            "account.move",
            move_company_domain + self._date_domain("invoice_date", start, end)
            + [("move_type", "in", ["out_invoice", "in_invoice", "out_refund", "in_refund"])],
            ["name", "partner_id", "invoice_date_due", "amount_total", "payment_state", "move_type"],
            order="invoice_date desc, id desc", limit=8,
        )
        data["rows"] = [
            {
                "id": row["id"], "model": "account.move", "name": row.get("name"),
                "partner": self._display_name(row.get("partner_id")), "date": row.get("invoice_date_due"),
                "amount": self._number(row.get("amount_total")),
                "status": self._status_label(row.get("payment_state")),
                "tone": self._status_tone(row.get("payment_state")),
                "document_type": "Invoice" if row.get("move_type") in ["out_invoice", "out_refund"] else "Bill",
            }
            for row in recent
        ]
        data["quick_actions"] = [
            {"label": "Customer Invoice", "model": "account.move", "context": {"default_move_type": "out_invoice"}, "icon": "fa-file-text-o"},
            {"label": "Vendor Bill", "model": "account.move", "context": {"default_move_type": "in_invoice"}, "icon": "fa-file-text"},
            {"label": "Payments", "app": "Accounting", "icon": "fa-credit-card"},
            {"label": "Journal Entries", "app": "Accounting", "icon": "fa-book"},
        ]
        return data

    # ---------------------------------------------------------------------
    # Manufacturing
    # ---------------------------------------------------------------------
    def _manufacturing_data(self, start, end):
        data = self._base("manufacturing", start, end)
        production_model = self._model("mrp.production")
        company_domain = self._company_domain(production_model)
        period_domain = self._dt_domain("date_start", start, end)
        active_domain = company_domain + [("state", "in", ["confirmed", "progress", "to_close"])]
        completed_domain = company_domain + period_domain + [("state", "=", "done")]
        active = self._safe_count("mrp.production", active_domain)
        completed = self._safe_count("mrp.production", completed_domain)
        planned_qty = self._safe_sum("mrp.production", "product_qty", company_domain + period_domain)
        produced_qty = self._safe_sum("mrp.production", "qty_produced", company_domain + period_domain)
        efficiency = (produced_qty / planned_qty * 100.0) if planned_qty else 0.0
        workorder_domain = self._company_domain(self._model("mrp.workorder")) + [("state", "=", "progress")]
        workorders = self._safe_count("mrp.workorder", workorder_domain)
        production_period_domain = company_domain + period_domain
        data["kpis"] = [
            {
                "label": "Active Manufacturing Orders", "value": active, "type": "number",
                "aggregate": {"operation": "count"},
                "hint": "Confirmed and in progress", "icon": "fa-file-text-o", "tone": "teal",
                "action": self._action_payload("Active Manufacturing Orders", "mrp.production", active_domain),
            },
            {
                "label": "Completed MOs", "value": completed, "type": "number",
                "aggregate": {"operation": "count"},
                "hint": "Selected period", "icon": "fa-check-circle", "tone": "green",
                "action": self._action_payload("Completed Manufacturing Orders", "mrp.production", completed_domain),
            },
            {
                "label": "Work Orders In Progress", "value": workorders, "type": "number",
                "aggregate": {"operation": "count"},
                "hint": "Live shop floor", "icon": "fa-bar-chart", "tone": "blue",
                "action": self._action_payload("Work Orders In Progress", "mrp.workorder", workorder_domain),
            },
            {
                "label": "Production Efficiency", "value": efficiency, "type": "percent",
                "hint": "Produced versus planned", "icon": "fa-line-chart", "tone": "teal",
                "action": self._action_payload("Manufacturing Orders - Selected Period", "mrp.production", production_period_domain),
            },
        ]

        state_labels = [
            ("draft", "Not Started"), ("confirmed", "Planned"), ("progress", "In Progress"),
            ("to_close", "To Close"), ("done", "Completed"), ("cancel", "Cancelled")
        ]
        data["statuses"] = [
            {
                "label": label,
                "count": self._safe_count("mrp.production", company_domain + [("state", "=", state)]),
                "tone": self._status_tone(state),
                "action": self._action_payload(
                    f"Manufacturing - {label}", "mrp.production", company_domain + [("state", "=", state)]
                ),
            }
            for state, label in state_labels
        ]
        recent = self._safe_records(
            "mrp.production", company_domain + period_domain,
            ["name", "product_id", "product_qty", "product_uom_id", "date_start", "date_finished", "state", "priority"],
            order="date_start desc", limit=10,
        )
        data["rows"] = [
            {
                "id": row["id"], "model": "mrp.production", "name": row.get("name"),
                "product": self._display_name(row.get("product_id")), "quantity": self._number(row.get("product_qty")),
                "uom": self._display_name(row.get("product_uom_id")), "date": row.get("date_start"),
                "due_date": row.get("date_finished"), "status": self._status_label(row.get("state")),
                "tone": self._status_tone(row.get("state")), "priority": row.get("priority") or "0",
            }
            for row in recent
        ]
        data["planning"] = data["rows"][:6]
        bom_rows = self._safe_records(
            "mrp.bom", self._company_domain(self._model("mrp.bom")),
            ["display_name", "product_tmpl_id", "product_qty", "product_uom_id", "bom_line_ids"],
            order="write_date desc", limit=1,
        )
        if bom_rows:
            bom = bom_rows[0]
            data["bom"] = {
                "id": bom["id"], "model": "mrp.bom", "name": bom.get("display_name"),
                "product": self._display_name(bom.get("product_tmpl_id")), "quantity": self._number(bom.get("product_qty")),
                "uom": self._display_name(bom.get("product_uom_id")), "components": len(bom.get("bom_line_ids") or []),
            }
        else:
            data["bom"] = {}
        data["quick_actions"] = [
            {"label": "Create MO", "model": "mrp.production", "icon": "fa-plus-square"},
            {"label": "Bill of Materials", "app": "Manufacturing", "icon": "fa-sitemap"},
            {"label": "Work Orders", "app": "Manufacturing", "icon": "fa-clipboard"},
            {"label": "Planning", "app": "Manufacturing", "icon": "fa-calendar"},
        ]
        return data

    # ---------------------------------------------------------------------
    # POS / kitchen / receipt
    # ---------------------------------------------------------------------
    def _pos_data(self, start, end):
        data = self._base("pos", start, end)
        order_model = self._model("pos.order")
        company_domain = self._company_domain(order_model)
        period_domain = self._dt_domain("date_order", start, end)
        paid_domain = company_domain + period_domain + [("state", "in", ["paid", "done", "invoiced"])]
        sales = self._safe_sum("pos.order", "amount_total", paid_domain)
        orders = self._safe_count("pos.order", paid_domain)
        tax = self._safe_sum("pos.order", "amount_tax", paid_domain)
        session_domain = self._company_domain(self._model("pos.session")) + [
            ("state", "in", ["opening_control", "opened", "closing_control"])
        ]
        sessions = self._safe_count("pos.session", session_domain)
        data["kpis"] = [
            {
                "label": "POS Sales", "value": sales, "type": "currency",
                "aggregate": {"operation": "sum", "field": "amount_total"},
                "hint": "Selected period", "icon": "fa-shopping-basket", "tone": "teal",
                "action": self._action_payload("Paid POS Orders", "pos.order", paid_domain),
            },
            {
                "label": "Orders", "value": orders, "type": "number",
                "aggregate": {"operation": "count"},
                "hint": "Paid, done and invoiced", "icon": "fa-file-text-o", "tone": "blue",
                "action": self._action_payload("POS Orders", "pos.order", paid_domain),
            },
            {
                "label": "Sales Tax", "value": tax, "type": "currency",
                "aggregate": {"operation": "sum", "field": "amount_tax"},
                "hint": "Tax collected", "icon": "fa-percent", "tone": "orange",
                "action": self._action_payload("POS Orders with Tax", "pos.order", paid_domain + [("amount_tax", "!=", 0)]),
            },
            {
                "label": "Open Sessions", "value": sessions, "type": "number",
                "aggregate": {"operation": "count"},
                "hint": "Active cashier sessions", "icon": "fa-desktop", "tone": "green",
                "action": self._action_payload("Open POS Sessions", "pos.session", session_domain),
            },
        ]
        trend_records = self._safe_records(
            "pos.order", paid_domain, ["date_order", "amount_total", "amount_tax"], order="date_order asc"
        )
        data["trend"] = self._trend_from_records(trend_records, "date_order", "amount_total", "amount_tax", start, end)
        recent = self._safe_records(
            "pos.order", company_domain + period_domain,
            ["name", "partner_id", "date_order", "amount_total", "state", "user_id", "config_id"],
            order="date_order desc", limit=10,
        )
        data["rows"] = [
            {
                "id": row["id"], "model": "pos.order", "name": row.get("name"),
                "partner": self._display_name(row.get("partner_id")), "date": row.get("date_order"),
                "amount": self._number(row.get("amount_total")), "status": self._status_label(row.get("state")),
                "tone": self._status_tone(row.get("state")), "owner": self._display_name(row.get("user_id")),
                "location": self._display_name(row.get("config_id")),
            }
            for row in recent
        ]
        payments = self._safe_records(
            "pos.payment", self._company_domain(self._model("pos.payment")) + self._dt_domain("payment_date", start, end),
            ["payment_method_id", "amount"], order="amount desc"
        )
        payment_totals = defaultdict(float)
        for row in payments:
            payment_totals[self._display_name(row.get("payment_method_id"))] += self._number(row.get("amount"))
        data["payments"] = [
            {"name": name, "value": value} for name, value in sorted(payment_totals.items(), key=lambda item: item[1], reverse=True)[:6]
        ]
        data["quick_actions"] = [
            {"label": "Open POS", "app": "Point of Sale", "icon": "fa-desktop"},
            {"label": "Orders", "app": "Point of Sale", "icon": "fa-list"},
            {"label": "Sessions", "app": "Point of Sale", "icon": "fa-clock-o"},
            {"label": "Products", "app": "Point of Sale", "icon": "fa-cube"},
        ]
        return data

    def _kitchen_data(self, start, end):
        data = self._base("kitchen", start, end)
        order_model = self._model("pos.order")
        company_domain = self._company_domain(order_model)
        recent = self._safe_records(
            "pos.order", company_domain + self._dt_domain("date_order", start, end),
            ["name", "date_order", "amount_total", "state", "hf_kitchen_state", "table_id", "lines", "partner_id"],
            order="date_order desc", limit=12,
        )
        tickets = []
        for index, row in enumerate(recent):
            lines = []
            if row.get("lines"):
                line_rows = self._safe_records(
                    "pos.order.line", [("id", "in", row["lines"])],
                    ["product_id", "qty", "customer_note"], order="id asc", limit=8,
                )
                lines = [
                    {"name": self._display_name(line.get("product_id")), "qty": self._number(line.get("qty")), "note": line.get("customer_note") or ""}
                    for line in line_rows
                ]
            state = row.get("state")
            stage = row.get("hf_kitchen_state") or ("new" if state == "draft" else "ready" if state == "paid" else "completed")
            order_dt = fields.Datetime.to_datetime(row.get("date_order")) if row.get("date_order") else fields.Datetime.now()
            elapsed_minutes = max(int((fields.Datetime.now() - order_dt).total_seconds() // 60), 0)
            tickets.append({
                "id": row["id"], "model": "pos.order", "name": row.get("name"),
                "date": row.get("date_order"), "amount": self._number(row.get("amount_total")),
                "status": self._status_label(state), "tone": self._status_tone(state),
                "stage": stage, "table": self._display_name(row.get("table_id")),
                "partner": self._display_name(row.get("partner_id")), "lines": lines,
                "elapsed": f"{elapsed_minutes // 60:02d}:{elapsed_minutes % 60:02d}",
            })
        data["tickets"] = tickets
        kitchen_period_domain = company_domain + self._dt_domain("date_order", start, end)
        data["kpis"] = [
            {
                "label": "Active Orders", "value": len([t for t in tickets if t["stage"] != "completed"]),
                "type": "number", "hint": "Live kitchen tickets", "icon": "fa-clipboard", "tone": "green",
                "action": self._action_payload(
                    "Active Kitchen Orders", "pos.order", kitchen_period_domain + [("hf_kitchen_state", "!=", "completed")]
                ),
            },
            {
                "label": "New", "value": len([t for t in tickets if t["stage"] == "new"]),
                "type": "number",
                "aggregate": {"operation": "count"}, "hint": "Awaiting preparation", "icon": "fa-clock-o", "tone": "blue",
                "action": self._action_payload("New Kitchen Orders", "pos.order", kitchen_period_domain + [("hf_kitchen_state", "=", "new")]),
            },
            {
                "label": "Preparing", "value": len([t for t in tickets if t["stage"] == "preparing"]),
                "type": "number",
                "aggregate": {"operation": "count"}, "hint": "Currently in kitchen", "icon": "fa-cutlery", "tone": "teal",
                "action": self._action_payload("Preparing Kitchen Orders", "pos.order", kitchen_period_domain + [("hf_kitchen_state", "=", "preparing")]),
            },
            {
                "label": "Ready", "value": len([t for t in tickets if t["stage"] == "ready"]),
                "type": "number",
                "aggregate": {"operation": "count"}, "hint": "Ready to serve", "icon": "fa-check", "tone": "green",
                "action": self._action_payload("Ready Kitchen Orders", "pos.order", kitchen_period_domain + [("hf_kitchen_state", "=", "ready")]),
            },
        ]
        return data

    def _receipt_data(self, start, end):
        data = self._base("receipt", start, end)
        order_model = self._model("pos.order")
        company_domain = self._company_domain(order_model)
        rows = self._safe_records(
            "pos.order", company_domain + [("state", "in", ["paid", "done", "invoiced"])],
            ["name", "date_order", "partner_id", "amount_total", "amount_tax", "amount_paid", "amount_return", "lines"],
            order="date_order desc", limit=1,
        )
        receipt = {}
        if rows:
            row = rows[0]
            line_rows = self._safe_records(
                "pos.order.line", [("id", "in", row.get("lines") or [])],
                ["product_id", "qty", "price_unit", "price_subtotal_incl", "discount"], order="id asc"
            )
            lines = [
                {
                    "name": self._display_name(line.get("product_id")), "qty": self._number(line.get("qty")),
                    "price": self._number(line.get("price_unit")), "total": self._number(line.get("price_subtotal_incl")),
                    "discount": self._number(line.get("discount")),
                }
                for line in line_rows
            ]
            receipt = {
                "id": row["id"], "model": "pos.order", "name": row.get("name"),
                "date": row.get("date_order"), "partner": self._display_name(row.get("partner_id")),
                "amount_total": self._number(row.get("amount_total")),
                "amount_tax": self._number(row.get("amount_tax")),
                "amount_paid": self._number(row.get("amount_paid")),
                "change": self._number(row.get("amount_return")),
                "subtotal": sum(line["total"] for line in lines) - self._number(row.get("amount_tax")),
                "loyalty_points": max(1, round(self._number(row.get("amount_total")) / 100.0)),
                "lines": lines,
            }
        data["receipt"] = receipt
        return data

    # ---------------------------------------------------------------------
    # Overview combines the live values from the specialist dashboards
    # ---------------------------------------------------------------------
    def _overview_data(self, start, end):
        data = self._base("overview", start, end)
        sales = self._sales_data(start, end)
        purchases = self._purchase_data(start, end)
        inventory = self._inventory_data(start, end)
        accounting = self._accounting_data(start, end)
        manufacturing = self._manufacturing_data(start, end)
        pos = self._pos_data(start, end)
        data["kpis"] = [
            {
                **sales["kpis"][2],
                "label": "Sales", "hint": "Confirmed sales orders", "tone": "teal",
            },
            {
                **pos["kpis"][0],
                "label": "POS Sales", "hint": "Paid POS orders", "tone": "cyan",
            },
            {
                **accounting["kpis"][1],
                "label": "Receivables", "hint": "Open customer invoices", "tone": "blue",
            },
            {
                **accounting["kpis"][2],
                "label": "Payables", "hint": "Open vendor bills", "tone": "orange",
            },
            {
                **inventory["kpis"][0],
                "label": "Stock On Hand", "hint": "Internal locations", "tone": "green",
            },
        ]
        labels = sorted({row["label"] for row in sales["trend"] + purchases["trend"]})
        sales_map = {row["label"]: row["primary"] for row in sales["trend"]}
        purchase_map = {row["label"]: row["primary"] for row in purchases["trend"]}
        data["trend"] = [
            {"label": label, "primary": sales_map.get(label, 0.0), "secondary": purchase_map.get(label, 0.0)}
            for label in labels
        ]
        data["summary"] = [
            {
                "label": "Purchase Orders", "value": purchases["kpis"][0]["value"],
                "type": "number", "tone": "info", "action": purchases["kpis"][0].get("action"),
                "aggregate": purchases["kpis"][0].get("aggregate"),
            },
            {
                "label": "Incoming Transfers", "value": inventory["kpis"][2]["value"],
                "type": "number", "tone": "success", "action": inventory["kpis"][2].get("action"),
                "aggregate": inventory["kpis"][2].get("aggregate"),
            },
            {
                "label": "Active Manufacturing", "value": manufacturing["kpis"][0]["value"],
                "type": "number", "tone": "warning", "action": manufacturing["kpis"][0].get("action"),
                "aggregate": manufacturing["kpis"][0].get("aggregate"),
            },
            {
                "label": "Net Profit", "value": accounting["kpis"][3]["value"],
                "type": "currency", "tone": "success", "action": accounting["kpis"][3].get("action"),
            },
        ]
        rows = []
        for row in sales["rows"][:3]:
            rows.append({**row, "source": "Sales"})
        for row in purchases["rows"][:3]:
            rows.append({**row, "source": "Purchase"})
        for row in accounting["rows"][:3]:
            rows.append({**row, "source": row.get("document_type", "Accounting")})
        data["rows"] = rows[:8]
        # Use explicit Odoo window actions instead of selecting root app menus.
        # Root application menus frequently do not own an action themselves,
        # which caused the Quick Action buttons to raise a client error.
        sale_domain = (
            self._company_domain(self._model("sale.order"))
            + self._dt_domain("date_order", start, end)
        )
        purchase_domain = (
            self._company_domain(self._model("purchase.order"))
            + self._dt_domain("date_order", start, end)
        )
        inventory_domain = self._company_domain(self._model("stock.quant")) + [
            ("location_id.usage", "=", "internal"),
        ]
        accounting_domain = (
            self._company_domain(self._model("account.move"))
            + self._date_domain("date", start, end)
            + [
                ("move_type", "in", ["out_invoice", "out_refund", "in_invoice", "in_refund"]),
            ]
        )
        manufacturing_domain = (
            self._company_domain(self._model("mrp.production"))
            + self._dt_domain("date_start", start, end)
        )
        pos_domain = (
            self._company_domain(self._model("pos.order"))
            + self._dt_domain("date_order", start, end)
        )
        data["all_records_action"] = self._action_payload(
            "Recent Invoices and Vendor Bills",
            "account.move",
            accounting_domain,
        )

        data["quick_actions"] = [
            {
                "label": "Sales",
                "icon": "fa-line-chart",
                "action": self._action_payload("Sales", "sale.order", sale_domain),
            },
            {
                "label": "Purchases",
                "icon": "fa-shopping-cart",
                "action": self._action_payload("Purchases", "purchase.order", purchase_domain),
            },
            {
                "label": "Inventory",
                "icon": "fa-cubes",
                "action": self._action_payload(
                    "Inventory On Hand", "stock.quant", inventory_domain, views=["list"]
                ),
            },
            {
                "label": "Accounting",
                "icon": "fa-calculator",
                "action": self._action_payload(
                    "Customer Invoices and Vendor Bills", "account.move", accounting_domain
                ),
            },
            {
                "label": "Manufacturing",
                "icon": "fa-industry",
                "action": self._action_payload(
                    "Manufacturing Orders", "mrp.production", manufacturing_domain
                ),
            },
            {
                "label": "Point of Sale",
                "icon": "fa-desktop",
                "action": self._action_payload("Point of Sale Orders", "pos.order", pos_domain),
            },
        ]
        return data

    @api.model
    def get_dashboard_data(self, kind="overview", date_from=None, date_to=None):
        if not self.env.user.hf_hisabflow_menu_access:
            raise AccessError("You do not have access to the HisabFlow dashboard.")
        start, end = self._date_bounds(date_from, date_to)
        builders = {
            "overview": self._overview_data,
            "sales": self._sales_data,
            "purchases": self._purchase_data,
            "inventory": self._inventory_data,
            "accounting": self._accounting_data,
            "manufacturing": self._manufacturing_data,
            "pos": self._pos_data,
            "kitchen": self._kitchen_data,
            "receipt": self._receipt_data,
        }
        data = builders.get(kind, self._overview_data)(start, end)
        return self._decorate_metric_counts(data)
