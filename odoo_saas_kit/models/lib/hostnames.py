# -*- coding: utf-8 -*-
"""
Hostname construction shared by the manager side (saas_plan.login_to_db_template)
and the provisioning side (saas_localhost / saas_remote create_db_template).

Kept in one place because these MUST agree exactly: the manager builds the URL the
"Login" button opens, while the provisioning code is what registers that same host
in nginx's client-ports.conf map. If they drift, the button points at a host nginx
has no backend for.
"""


def db_template_host(version, base_domain):
    """
    Hostname of the shared per-Odoo-version DB-template container,
    e.g. ("19.0", "hisabflow.tech") -> "db19-templates.hisabflow.tech".

    Note the HYPHEN. This used to be "db19_templates.<domain>", with an underscore,
    which broke two things at once:

    1. TLS. An underscore is not a legal character in a DNS hostname label, so a
       strict client will not wildcard-match it against the `*.hisabflow.tech`
       certificate - it fails with "certificate is not valid for
       db19_templates.hisabflow.tech" even though the cert covers every other
       subdomain. Verified directly: the hyphenated form validates against the very
       same cert on the very same nginx, the underscored form does not.

    2. Its own nginx map entry. The host-side helper that maintains
       client-ports.conf (nginx-client-map-update.sh) validates the hostname against
       a strict DNS-label regex that rejects underscores, so create_db_template()
       could never actually register the template host - the live entry had to be
       hand-added, and would silently never be re-created on a rebuild.
    """
    return "db{}-templates.{}".format(str(version).split('.', 1)[0], base_domain)
