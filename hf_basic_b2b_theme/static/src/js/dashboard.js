/** @odoo-module **/

import { Component, onMounted, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";


function localIsoDate(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
}

function defaultDashboardDates() {
    // The browser is the source of truth for the user's local calendar date.
    // This prevents a UTC server/container from ending the dashboard one day
    // behind the date shown on Odoo records around midnight.
    const today = new Date();
    const yearStart = new Date(today.getFullYear(), 0, 1);
    return {
        dateFrom: localIsoDate(yearStart),
        dateTo: localIsoDate(today),
    };
}

const DASHBOARD_ACTIONS = {
    overview: "hf_basic_b2b_theme.action_hf_overview",
    sales: "hf_basic_b2b_theme.action_hf_sales",
    purchases: "hf_basic_b2b_theme.action_hf_purchases",
    inventory: "hf_basic_b2b_theme.action_hf_inventory",
    accounting: "hf_basic_b2b_theme.action_hf_accounting",
    manufacturing: "hf_basic_b2b_theme.action_hf_manufacturing",
    pos: "hf_basic_b2b_theme.action_hf_pos",
    kitchen: "hf_basic_b2b_theme.action_hf_kitchen",
    receipt: "hf_basic_b2b_theme.action_hf_receipt",
};

export class HisabFlowDashboard extends Component {
    static template = "hf_basic_b2b_theme.Dashboard";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.menu = useService("menu");
        this.notification = useService("notification");

        // OWL evaluates parameterized template handlers such as
        // `() => openMetric(kpi)` as plain function calls.  Bind every method
        // exposed to those handlers so the component instance (and therefore
        // its action/notification services) is always available in Odoo 19.
        for (const methodName of [
            "openMetric",
            "onMetricKeydown",
            "runQuickAction",
            "openAllRecords",
            "openRecord",
            "openApp",
            "setKitchenFilter",
            "updateKitchenState",
        ]) {
            this[methodName] = this[methodName].bind(this);
        }

        const context = this.props.action?.context || {};
        this.kind = context.dashboard_type || "overview";
        const initialDates = defaultDashboardDates();
        this.state = useState({
            loading: true,
            error: "",
            data: null,
            dateFrom: initialDates.dateFrom,
            dateTo: initialDates.dateTo,
            kitchenFilter: "all",
        });
        this.refreshTimer = null;
        onWillStart(() => this.loadData());
        onMounted(() => {
            if (this.kind === "kitchen") {
                this.refreshTimer = window.setInterval(() => this.loadData(true), 15000);
            }
        });
        onWillUnmount(() => {
            if (this.refreshTimer) {
                window.clearInterval(this.refreshTimer);
            }
        });
    }

    async loadData(silent = false) {
        if (!silent) {
            this.state.loading = true;
        }
        this.state.error = "";
        try {
            const data = await this.orm.call("hf.b2b.dashboard", "get_dashboard_data", [
                this.kind,
                this.state.dateFrom || false,
                this.state.dateTo || false,
            ]);
            this.state.data = data;
            this.state.dateFrom = data.date_from;
            this.state.dateTo = data.date_to;
        } catch (error) {
            console.error("HisabFlow dashboard failed to load", error);
            this.state.error = error?.message || "The dashboard could not be loaded.";
        } finally {
            if (!silent) {
                this.state.loading = false;
            }
        }
    }

    onDateFromChange(ev) {
        this.state.dateFrom = ev.target.value;
    }

    onDateToChange(ev) {
        this.state.dateTo = ev.target.value;
    }

    async applyDateFilter() {
        await this.loadData();
    }

    async openDashboard(kind) {
        const actionXmlid = DASHBOARD_ACTIONS[kind];
        if (actionXmlid) {
            await this.action.doAction(actionXmlid, { clearBreadcrumbs: true });
        }
    }

    _firstActionableMenu(menu) {
        if (!menu) {
            return null;
        }
        if (menu.actionID) {
            return menu;
        }

        let children = menu.childrenTree;
        if (!children && menu.id) {
            try {
                children = this.menu.getMenuAsTree(menu.id)?.childrenTree || [];
            } catch {
                children = [];
            }
        }
        for (const child of children || []) {
            const target = this._firstActionableMenu(child);
            if (target) {
                return target;
            }
        }
        return null;
    }

    async openApp(appName) {
        const normalized = String(appName || "").toLowerCase().trim();
        const app = (this.menu.getApps() || []).find((item) => {
            const name = String(item.name || "").toLowerCase();
            return name === normalized || name.includes(normalized) || normalized.includes(name);
        });
        if (!app) {
            this.notification.add(`The ${appName} application is not available for this user.`, {
                type: "warning",
            });
            return;
        }

        const target = this._firstActionableMenu(app);
        if (!target) {
            this.notification.add(`${app.name} has no accessible menu for this user.`, {
                type: "warning",
            });
            return;
        }

        try {
            await this.menu.selectMenu(target);
        } catch (error) {
            console.error("HisabFlow application navigation failed", { app, target, error });
            this.notification.add(
                error?.message || `Unable to open ${app.name}. Please check access rights.`,
                { type: "danger" }
            );
        }
    }

    async _runWindowAction(spec, fallbackName = "Records") {
        if (!spec?.model) {
            return;
        }
        try {
            const resolvedAction = await this.orm.call(
                "hf.b2b.dashboard",
                "resolve_dashboard_action",
                [{ ...spec, name: spec.name || fallbackName }]
            );
            if (resolvedAction?.warning) {
                this.notification.add(resolvedAction.warning, { type: "warning" });
                return;
            }
            await this.action.doAction(resolvedAction);
        } catch (error) {
            console.error("HisabFlow dashboard action failed", { spec, error });
            this.notification.add(
                error?.message || `Unable to open ${spec.name || fallbackName}. Please check access rights.`,
                { type: "danger" }
            );
        }
    }

    async openMetric(item) {
        if (item?.action) {
            await this._runWindowAction(item.action, item.label);
        }
    }

    onMetricKeydown(ev, item) {
        if (!item?.action) {
            return;
        }
        if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            this.openMetric(item);
        }
    }

    async runQuickAction(item) {
        if (!item) {
            return;
        }
        try {
            if (item.action_xmlid) {
                return await this.action.doAction(item.action_xmlid);
            }
            if (item.action) {
                return await this._runWindowAction(item.action, item.label);
            }
            if (item.dashboard) {
                return await this.openDashboard(item.dashboard);
            }
            if (item.model) {
                return await this.action.doAction({
                    type: "ir.actions.act_window",
                    name: item.label || "New Record",
                    res_model: item.model,
                    views: [[false, "form"]],
                    target: "current",
                    context: item.context || {},
                });
            }
            if (item.app) {
                return await this.openApp(item.app);
            }
        } catch (error) {
            console.error("HisabFlow quick action failed", { item, error });
            this.notification.add(
                error?.message || `Unable to open ${item.label || "the selected action"}. Please check access rights.`,
                { type: "danger" }
            );
        }
    }

    async openAllRecords(appName) {
        const actionSpec = this.state.data?.all_records_action;
        if (actionSpec) {
            return await this._runWindowAction(actionSpec, "Business Activity");
        }
        return await this.openApp(appName);
    }

    async openRecord(row) {
        if (!row?.model || !row?.id) {
            return;
        }
        try {
            const resolvedAction = await this.orm.call(
                "hf.b2b.dashboard",
                "resolve_record_action",
                [row.model, row.id, row.name || row.product || "Record"]
            );
            if (resolvedAction?.warning) {
                this.notification.add(resolvedAction.warning, { type: "warning" });
                return;
            }
            await this.action.doAction(resolvedAction);
        } catch (error) {
            console.error("HisabFlow record action failed", { row, error });
            this.notification.add(
                error?.message || "Unable to open this record. Please check access rights.",
                { type: "danger" }
            );
        }
    }

    formatValue(value, type = "number") {
        const number = Number(value || 0);
        if (type === "currency") {
            const currency = this.state.data?.currency?.name || "PKR";
            try {
                return new Intl.NumberFormat("en-PK", {
                    style: "currency",
                    currency,
                    maximumFractionDigits: 0,
                }).format(number);
            } catch {
                return `${this.state.data?.currency?.symbol || currency} ${new Intl.NumberFormat("en-PK", {
                    maximumFractionDigits: 0,
                }).format(number)}`;
            }
        }
        if (type === "percent") {
            return `${number.toFixed(1)}%`;
        }
        return new Intl.NumberFormat("en-PK", {
            maximumFractionDigits: Math.abs(number % 1) > 0 ? 2 : 0,
        }).format(number);
    }

    formatDate(value) {
        if (!value) {
            return "—";
        }
        const parsed = new Date(value.replace(" ", "T"));
        if (Number.isNaN(parsed.getTime())) {
            return value;
        }
        return new Intl.DateTimeFormat("en-PK", {
            day: "2-digit",
            month: "short",
            year: "numeric",
        }).format(parsed);
    }

    formatTime(value) {
        if (!value) {
            return "—";
        }
        const parsed = new Date(value.replace(" ", "T"));
        if (Number.isNaN(parsed.getTime())) {
            return value;
        }
        return new Intl.DateTimeFormat("en-PK", {
            hour: "2-digit",
            minute: "2-digit",
        }).format(parsed);
    }

    trendMax(keys = ["primary", "secondary"]) {
        const rows = this.state.data?.trend || [];
        let maximum = 0;
        for (const row of rows) {
            for (const key of keys) {
                maximum = Math.max(maximum, Number(row[key] || 0));
            }
        }
        return maximum || 1;
    }

    barHeight(row, key) {
        const percentage = (Number(row[key] || 0) / this.trendMax()) * 100;
        return `height: ${Math.max(percentage, row[key] ? 5 : 0)}%`;
    }

    linePoints(key) {
        const rows = this.state.data?.trend || [];
        if (!rows.length) {
            return "";
        }
        const maximum = this.trendMax([key]);
        return rows
            .map((row, index) => {
                const x = rows.length === 1 ? 50 : (index / (rows.length - 1)) * 100;
                const y = 92 - (Number(row[key] || 0) / maximum) * 78;
                return `${x.toFixed(2)},${y.toFixed(2)}`;
            })
            .join(" ");
    }

    areaPoints(key) {
        const points = this.linePoints(key);
        if (!points) {
            return "";
        }
        return `0,100 ${points} 100,100`;
    }

    donutStyle(items) {
        if (!items?.length) {
            return "background: conic-gradient(#e7edf3 0 100%)";
        }
        const colors = ["#0a9f9a", "#1b68cc", "#6fd15c", "#f4b942", "#ef6363"];
        const total = items.reduce((sum, item) => sum + Number(item.value || 0), 0) || 1;
        let cursor = 0;
        const stops = items.map((item, index) => {
            const start = cursor;
            cursor += (Number(item.value || 0) / total) * 100;
            return `${colors[index % colors.length]} ${start.toFixed(2)}% ${cursor.toFixed(2)}%`;
        });
        return `background: conic-gradient(${stops.join(", ")})`;
    }

    planningStyle(index, row) {
        const left = (index % 4) * 10;
        const width = Math.max(28, Math.min(72, Number(row.quantity || 0) % 72));
        return `margin-left:${left}%;width:${width}%`;
    }

    setKitchenFilter(filter) {
        this.state.kitchenFilter = filter;
    }

    get visibleKitchenTickets() {
        const tickets = this.state.data?.tickets || [];
        if (this.state.kitchenFilter === "all") {
            return tickets;
        }
        return tickets.filter((ticket) => ticket.stage === this.state.kitchenFilter);
    }

    kitchenCount(stage) {
        const tickets = this.state.data?.tickets || [];
        if (stage === "all") {
            return tickets.length;
        }
        return tickets.filter((ticket) => ticket.stage === stage).length;
    }

    async updateKitchenState(ticket, nextState) {
        if (!nextState) {
            return this.openRecord(ticket);
        }
        try {
            await this.orm.write("pos.order", [ticket.id], { hf_kitchen_state: nextState });
            await this.loadData(true);
            this.notification.add(`Kitchen ticket ${ticket.name} updated.`, { type: "success" });
        } catch (error) {
            console.error("Kitchen ticket update failed", error);
            this.notification.add(error?.message || "The kitchen status could not be updated.", {
                type: "danger",
            });
        }
    }

    printReceipt() {
        window.print();
    }
}

registry.category("actions").add("hf_basic_b2b_theme.Dashboard", HisabFlowDashboard);
