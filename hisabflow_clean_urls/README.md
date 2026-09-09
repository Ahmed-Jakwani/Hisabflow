# HisabFlow Clean Backend URLs — 19.0.1.0.3

Website-safe clean backend URLs for Odoo 19.

## Refresh-safe behavior

A hard request to a validated clean backend path such as `/sales`, `/inventory`
or `/action-585` is first redirected to Odoo's canonical `/odoo/...` route so
Odoo's core router initializes normally. After the webclient boots, the addon
uses `history.replaceState()` to remove `/odoo` from the visible address bar
without a second reload.

The addon does **not** overwrite Odoo's initial router state after startup.

## Website safety

Odoo Website fallback executes first. Existing Website/portal/controller routes
remain authoritative, including `/contactus`, `/shop`, `/blog`, `/my`, `/web/*`,
reports, APIs, websocket and future Website Builder pages.
