/** @odoo-module **/

import { NavBar } from "@web/webclient/navbar/navbar";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { useEffect } from "@odoo/owl";

/**
 * HisabFlow backend shell extension.
 *
 * Desktop navigation intentionally keeps only Odoo's top-level applications
 * in the fixed HisabFlow sidebar.  Once an application is opened, its normal
 * Odoo sections/dropdowns remain in the native top navbar.  This avoids a
 * second custom submenu column and keeps complex apps such as Discuss,
 * Accounting, Inventory and POS compatible with their standard layouts.
 *
 * Odoo's menu service remains the source of truth for navigation and access
 * rights.  No action IDs or business-menu URLs are hard-coded here.
 */
patch(NavBar.prototype, {
    setup() {
        super.setup(...arguments);

        this.hfNotification = useService("notification");

        // v19.0.1.5.9 no longer uses the old collapsed/secondary-sidebar
        // states. Remove those classes proactively in case cached assets left
        // them on <body>, then only track whether the HisabFlow home app is
        // active so the dashboard search can keep its dedicated header space.
        document.body.classList.remove("hf-sidebar-is-collapsed", "hf-sidebar-has-submenu");
        document.body.classList.toggle("hf-hisabflow-home", this.isHFHomeApp());

        useEffect(
            () => {
                document.body.classList.remove(
                    "hf-sidebar-is-collapsed",
                    "hf-sidebar-has-submenu"
                );
                document.body.classList.toggle("hf-hisabflow-home", this.isHFHomeApp());

                return () => {
                    document.body.classList.remove(
                        "hf-hisabflow-home",
                        "hf-sidebar-is-collapsed",
                        "hf-sidebar-has-submenu"
                    );
                };
            },
            () => [this.currentApp?.id]
        );
    },

    async openHFHome(ev) {
        ev?.preventDefault?.();

        const apps = this.menuService.getApps() || [];
        const homeApp = apps.find((app) =>
            String(app?.xmlid || "") === "hf_basic_b2b_theme.menu_hf_root" ||
            this._hfNormalize(app?.name) === "hisabflow"
        );

        if (!homeApp) {
            this.hfNotification.add("HisabFlow home menu is not accessible.", {
                type: "warning",
            });
            return;
        }

        await this._hfNavigateToMenu(homeApp);
    },

    isHFHomeApp() {
        const xmlid = String(this.currentApp?.xmlid || "");
        return (
            xmlid === "hf_basic_b2b_theme.menu_hf_root" ||
            this._hfNormalize(this.currentApp?.name) === "hisabflow"
        );
    },

    openHFGlobalSearch() {
        document.dispatchEvent(
            new KeyboardEvent("keydown", {
                key: "k",
                code: "KeyK",
                ctrlKey: true,
                bubbles: true,
                cancelable: true,
            })
        );
    },

    _hfNormalize(value) {
        return String(value || "")
            .normalize("NFD")
            .replace(/[\u0300-\u036f]/g, "")
            .toLowerCase()
            .trim();
    },

    /** Return the first accessible menu item that owns an Odoo action. */
    _hfFirstActionable(menu) {
        if (!menu) {
            return null;
        }
        if (menu.actionID) {
            return menu;
        }

        let children = menu.childrenTree;
        if (!children && menu.id) {
            try {
                children = this.menuService.getMenuAsTree(menu.id)?.childrenTree || [];
            } catch {
                children = [];
            }
        }

        for (const child of children || []) {
            const actionable = this._hfFirstActionable(child);
            if (actionable) {
                return actionable;
            }
        }
        return null;
    },

    getHFMenuHref(menu) {
        const target = this._hfFirstActionable(menu);
        return target ? this.getMenuItemHref(target) : "/odoo";
    },

    getHFAppFallbackIcon(app) {
        const name = this._hfNormalize(app?.name);
        const icons = [
            [["sale", "crm"], "fa-line-chart"],
            [["purchase"], "fa-shopping-cart"],
            [["inventory", "stock"], "fa-cubes"],
            [["account", "invoice"], "fa-calculator"],
            [["manufactur", "mrp"], "fa-industry"],
            [["point of sale", "pos"], "fa-credit-card"],
            [["employee", "hr"], "fa-users"],
            [["recruit"], "fa-user-plus"],
            [["calendar"], "fa-calendar"],
            [["contact"], "fa-address-book"],
            [["website"], "fa-globe"],
            [["event"], "fa-ticket"],
            [["expense"], "fa-money"],
            [["discuss"], "fa-comments"],
            [["dashboard"], "fa-pie-chart"],
            [["link tracker"], "fa-link"],
            [["setting", "apps"], "fa-cog"],
        ];
        for (const [needles, icon] of icons) {
            if (needles.some((needle) => name.includes(needle))) {
                return icon;
            }
        }
        return "fa-th-large";
    },

    async _hfNavigateToMenu(menu) {
        const target = this._hfFirstActionable(menu);
        if (!target) {
            this.hfNotification.add(
                `${menu?.name || "This menu"} does not contain an accessible action.`,
                { type: "warning" }
            );
            return;
        }

        try {
            await this.menuService.selectMenu(target);
        } catch (error) {
            console.error("HisabFlow sidebar navigation failed", { menu, target, error });
            this.hfNotification.add(
                `Unable to open ${target.name || menu?.name || "the selected application"}. Please check the user's access rights.`,
                { type: "danger", sticky: false }
            );
        }
    },

    async onHFSidebarMenuSelect(menu) {
        await this._hfNavigateToMenu(menu);
    },
});
