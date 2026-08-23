# -*- coding: utf-8 -*-
"""
Restrict which CUSTOM modules a template/client database can see in its Apps list.

Why this is needed
------------------
Every client container and the shared template container bind-mount the SAME
`common-addons_v19` directory as `/mnt/extra-addons`. So every custom module that
exists anywhere on the host is on the addons path of every client, and - since the
SaaS Kit's own auto-login hands out a superuser session - any client admin could
install any of them. A plan's `saas_module_ids` was only ever a provisioning
wishlist, never an entitlement: nothing in the module referenced
`ir.module.module`, there was no `ir.rule`, and `saas.module.status.uninstall_module()`
was a bare `pass`.

What this does
--------------
After provisioning (and after any change to a plan's module list) it removes the
`ir_module_module` rows for custom modules the plan is NOT entitled to, in that one
database. A module with no row is genuinely absent from Apps - it cannot be
searched, opened or installed.

Deliberate choices
------------------
* Only ever touches modules that physically live in the shared custom-addons
  directory. Core Odoo CE modules are never considered, so every stock app stays
  available exactly as it is today.
* Only ever touches rows in state 'uninstalled'/'uninstallable'. Anything already
  installed is left strictly alone - which matters because plan modules pull in
  custom dependencies of their own (hf_basic_b2b_theme, for instance, depends on
  mrp and point_of_sale), and yanking those would break a live client. Odoo itself
  refuses to unlink installed modules anyway (ir_module.py `_unlink_except_installed`).
* Setting `state = 'uninstallable'` is NOT sufficient on its own: Odoo 19 still
  lists uninstallable modules in Apps (greyed out, and included in the "Not
  Installed" filter) - it only hides the Activate button
  (`invisible="state != 'uninstalled'"` in base/views/ir_module_views.xml). So the
  row is removed rather than flagged, with flagging kept as the fallback for
  anything that can't be removed.

Known limitation
----------------
Running "Update Apps List" inside the client re-creates the rows, because that
re-scans the addons path. This is enforcement at the database level, not true
filesystem isolation; the only way to make it airtight is to give each plan its own
addons directory (which needs one template container per plan). Re-asserting on
provisioning and on every plan-module change keeps it correct in practice.
"""
import logging
import os

_logger = logging.getLogger(__name__)

#: States in which a module row may safely be removed/flagged. Anything else is live.
REMOVABLE_STATES = ('uninstalled', 'uninstallable')


def list_custom_modules(common_addons_path):
    """
    Technical names of the modules physically present in the shared custom-addons
    directory (the one mounted into every container as /mnt/extra-addons).

    Returns an empty set if the path isn't readable - callers must treat that as
    "don't know, change nothing" rather than "no custom modules exist", or an
    unreadable mount would look like a licence to hide everything.
    """
    try:
        entries = os.listdir(common_addons_path)
    except OSError as e:
        _logger.error("Could not list custom addons dir %s: %r", common_addons_path, e)
        return set()

    modules = set()
    for name in entries:
        manifest = os.path.join(common_addons_path, name, '__manifest__.py')
        if os.path.isfile(manifest):
            modules.add(name)
    return modules


def enforce_module_visibility(client, entitled_modules, common_addons_path):
    """
    Hide from `client`'s Apps every custom module not in `entitled_modules`.

    `client` is a connected erppeek client for the target database.
    `entitled_modules` is the plan's module list (technical names); the caller is
    responsible for including anything structurally required, e.g.
    saas_kit_auto_login.

    Returns a dict summary; never raises - visibility tightening must not be able
    to fail a client/template creation that has otherwise succeeded.
    """
    summary = {'hidden': [], 'skipped_installed': [], 'flagged': [], 'errors': []}

    custom_modules = list_custom_modules(common_addons_path)
    if not custom_modules:
        _logger.warning("No custom modules discovered at %s - leaving module visibility "
                        "untouched", common_addons_path)
        return summary

    entitled = {m for m in (entitled_modules or []) if m}
    candidates = sorted(custom_modules - entitled)
    if not candidates:
        _logger.info("Every custom module is entitled for this database, nothing to hide")
        return summary

    try:
        rows = client.execute('ir.module.module', 'search_read',
                              [('name', 'in', candidates)], ['name', 'state'])
    except Exception as e:
        _logger.error("Could not read module list while enforcing visibility: %r", e)
        summary['errors'].append(str(e))
        return summary

    to_remove = []
    for row in rows:
        if row['state'] in REMOVABLE_STATES:
            to_remove.append(row['id'])
            summary['hidden'].append(row['name'])
        else:
            # Installed - almost always a dependency pulled in by an entitled module.
            summary['skipped_installed'].append(row['name'])

    if summary['skipped_installed']:
        _logger.info("Leaving these installed non-plan custom modules alone (most likely "
                     "dependencies of entitled modules): %r", summary['skipped_installed'])

    if not to_remove:
        return summary

    try:
        client.execute('ir.module.module', 'unlink', to_remove)
        _logger.info("Hid %s non-entitled custom modules from Apps: %r",
                     len(summary['hidden']), summary['hidden'])
    except Exception as e:
        # Couldn't remove them - fall back to flagging them uninstallable, which at
        # least removes the Activate button so they can't be installed.
        _logger.warning("Could not remove non-entitled module rows (%r) - falling back to "
                        "marking them uninstallable", e)
        summary['errors'].append(str(e))
        try:
            client.execute('ir.module.module', 'write', to_remove, {'state': 'uninstallable'})
            summary['flagged'] = summary.pop('hidden')
            summary['hidden'] = []
        except Exception as e2:
            _logger.error("Could not mark non-entitled modules uninstallable either: %r", e2)
            summary['errors'].append(str(e2))

    return summary
