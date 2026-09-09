# -*- coding: utf-8 -*-
{
    "name": "HisabFlow Clean Backend URLs",
    "summary": "Remove the visible /odoo prefix while preserving Odoo Website and portal routes",
    "version": "19.0.1.0.4",
    "category": "Hidden/Tools",
    "author": "Owais Ali / HisabFlow.tech",
    "website": "https://hisabflow.tech",
    "license": "LGPL-3",
    # "website" was removed here (2026-09-04). Nothing in this module imports
    # from website - _serve_fallback is defined on base ir.http - but declaring
    # the dependency force-installed the whole Website app into client
    # databases. Verified afterwards: installing this leaves website
    # uninstalled, and the clean-URL redirect still fires correctly on
    # databases where Website happens to be present.
    "depends": [
        "web",
    ],
    "data": [],
    "assets": {
        "web.assets_backend": [
            "hisabflow_clean_urls/static/src/js/clean_router.js",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
