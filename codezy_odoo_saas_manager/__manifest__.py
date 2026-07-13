{
    "name": "SaaS Manager",
    "summary": "Manage SaaS clients, plans, databases, backups, and logs in Odoo 19.",
    "description": """
SaaS Manager for Odoo 19

Manage SaaS clients, subscription plans, client databases, PostgreSQL backups,
restore operations, database health checks, and operation logs from inside Odoo.
    """,
    "version": "19.0.1.0.0",
    "category": "Administration",
    "author": "Habib Mohammed",
    "website": "https://github.com/HMA202/odoo-saas",
    "license": "LGPL-3",
    "depends": ["base"],
    "data": [
        "security/ir.model.access.csv",
        "views/saas_plan_views.xml",
        "views/saas_client_views.xml",
        "views/saas_log_views.xml",
        "views/menu.xml",
    ],
    "images": [
        "static/description/banner.png",
        "static/description/icon.png",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}