# HisabFlow Basic B2B Theme — Odoo 19

This release keeps the HisabFlow desktop application sidebar and homepage
search while preventing that custom search from appearing inside Odoo apps.

## Included

- HisabFlow navy/teal application sidebar using installed Odoo app icons
- Homepage-only `Search anything` control with Ctrl+K support
- No duplicate custom search inside Sales, Purchases, Inventory or other apps
- Native, selectable Odoo company switcher
- Native activities, notifications, breadcrumbs and user menu
- Live company-aware Overview dashboard and date filters
- Clickable KPI cards, business snapshot rows and date-filtered recent records
- Working Overview Quick Actions for Sales, Purchases, Inventory, Accounting,
  Manufacturing and Point of Sale, using explicit record-window actions
- Independent dashboard and sidebar scrolling
- Responsive fallback to Odoo's native app navigation on smaller screens

## Upgrade

Replace the existing `hf_basic_b2b_theme` directory with this folder and
upgrade the module:

```bash
./odoo-bin -d YOUR_DATABASE -u hf_basic_b2b_theme --stop-after-init
```

Restart Odoo, clear generated assets when required, then hard-refresh the
browser with `Ctrl + Shift + R`.

## v19.0.1.5.0

- Added a **HisabFlow Menu** checkbox on Settings > Users > Access Rights.
  The HisabFlow root menu/dashboard is visible only to users with this option.
- Extended the backend teal/green brand override to native Odoo primary accents,
  including smart buttons, breadcrumbs, search facets, active filters, tabs,
  pagination, checkboxes/radios and selection states.
- Dashboard KPI figures now expose the exact linked-record count, count queries
  have a safe fallback, recent rows respect the selected period, and the default
  dashboard period is year-to-date.
- Corrected Accounting Bank Balance to sum liquidity-side journal items only,
  avoiding the zero caused by summing both sides of balanced bank entries.
