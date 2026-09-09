# -*- coding: utf-8 -*-
{
    "name": "HisabFlow Custom Login",
    "version": "19.0.1.0.2",
    "summary": "Split-screen branded login page for HisabFlow",
    "category": "Website",
    "author": "Owais Ali / HisabFlow.tech",
    "website": "https://hisabflow.tech",
    "license": "LGPL-3",
    # No "website" here on purpose. This module only restyles the login page,
    # and anchoring on web.login_layout (see views/login_templates.xml) means
    # the Website app is not needed. Depending on it silently installed
    # website + website_links/_mail/_payment/_sms into every client database
    # that received this module.
    "depends": [
        "web",
        "auth_signup",
    ],
    "data": [
        "views/login_templates.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "hisabflow_custom_login/static/src/css/login.css",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
