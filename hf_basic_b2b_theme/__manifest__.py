# -*- coding: utf-8 -*-
{
    "name": "HisabFlow Basic B2B Theme",
    "version": "19.0.1.6.0",
    "category": "Themes/Backend",
    "summary": "Professional HisabFlow sidebar and white Odoo backend header",
    "description": """
HisabFlow Basic B2B Theme — Modern Backend UI
==============================================
A production-ready Odoo 19 backend shell for the HisabFlow Basic B2B plan.

Key features:
* Responsive navy/teal application sidebar with native Odoo app artwork
* Native Odoo application submenus/dropdowns preserved in the top header
* Standard Odoo top navbar with native company switcher and systray controls
* No duplicate custom header or application-level search bar
* Reliable scrolling for custom dashboards and standard Odoo actions
* Live current-company dashboard values with clickable KPI record drill-down
* Dynamic date filters, recent records, quick actions and responsive layouts
* Access-safe navigation through Odoo's own menu and action services
    """,
    "author": "Owais Ali / HisabFlow.tech",
    "website": "https://hisabflow.tech",
    "license": "LGPL-3",
    # hisabflow_clean_urls and hisabflow_custom_login are hard dependencies, not
    # optional add-ons:
    #   * the sidebar brand link in static/src/xml/navbar.xml points at "/app",
    #     a route that only hisabflow_clean_urls serves. Without it that link is
    #     a 404.
    #   * the login page this theme expects is the one hisabflow_custom_login
    #     installs (this module's own login views are defined then deactivated
    #     at the bottom of views/login_templates.xml).
    # Declaring them here is also what keeps them alive in client databases:
    # enforce_module_visibility() only unlinks custom modules in state
    # 'uninstalled'/'uninstallable', so being pulled in as a dependency of an
    # entitled module means they are installed and therefore skipped. Adding
    # them to every plan's module list by hand would work too, but would have
    # to be repeated for each new plan.
    "depends": [
        "web",
        "mail",
        "sale_management",
        "purchase_stock",
        "stock_account",
        "account",
        "mrp",
        "point_of_sale",
        "hisabflow_clean_urls",
        "hisabflow_custom_login",
    ],
    "data": [
        "views/res_users_views.xml",
        "views/webclient_templates.xml",
        "views/login_templates.xml",
        "views/dashboard_actions.xml",
    ],
    "assets": {
        "web._assets_primary_variables": [
            ("prepend", "hf_basic_b2b_theme/static/src/scss/hisabflow_primary_variables.scss"),
        ],
        "web.assets_backend": [
            "hf_basic_b2b_theme/static/src/js/navbar_patch.js",
            "hf_basic_b2b_theme/static/src/js/dashboard.js",
            "hf_basic_b2b_theme/static/src/xml/navbar.xml",
            "hf_basic_b2b_theme/static/src/xml/dashboard.xml",
            "hf_basic_b2b_theme/static/src/css/variables.css",
            "hf_basic_b2b_theme/static/src/css/backend_shell.css",
            "hf_basic_b2b_theme/static/src/css/dashboard.css",
            "hf_basic_b2b_theme/static/src/css/teal_backend_overrides.css",
            "hf_basic_b2b_theme/static/src/css/zz_hisabflow_teal_final.css",
        ],
        "web.assets_frontend": [
            "hf_basic_b2b_theme/static/src/css/teal_portal_overrides.css",
        ],
    },
    "images": [
        "static/description/banner.png",
        "static/description/icon.png",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
