/** @odoo-module **/

import { router } from "@web/core/browser/router";
import { patch } from "@web/core/utils/patch";

const ODOO_PREFIX = "/odoo";
const CLEAN_BACKEND_ROOT = "/app";

function removeOdooPrefix(url) {
    if (url === ODOO_PREFIX) {
        return CLEAN_BACKEND_ROOT;
    }
    if (url.startsWith(`${ODOO_PREFIX}/`)) {
        return url.slice(ODOO_PREFIX.length);
    }
    if (url.startsWith(`${ODOO_PREFIX}?`)) {
        return `${CLEAN_BACKEND_ROOT}${url.slice(ODOO_PREFIX.length)}`;
    }
    return url;
}

// Must stay in sync with `technical_prefixes` in models/ir_http.py. These are
// namespaces Odoo owns outright; prefixing them with /odoo produces a URL that
// resolves to nothing. The two lists previously disagreed - the server excluded
// /websocket, /mail, /report, /my, /api, /jsonrpc and /xmlrpc while this one
// only knew about /web and /scoped_app - so urlToState() would happily rewrite
// e.g. /my/orders into /odoo/my/orders.
const NATIVE_TECHNICAL_PREFIXES = [
    "/web",
    "/websocket",
    "/report",
    "/my",
    "/mail",
    "/api",
    "/jsonrpc",
    "/xmlrpc",
    "/scoped_app",
    "/website",
    ODOO_PREFIX,
];

function isNativeTechnicalPath(pathname) {
    return NATIVE_TECHNICAL_PREFIXES.some(
        (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`)
    );
}

function cleanPathToNative(pathname) {
    if (pathname === CLEAN_BACKEND_ROOT) {
        return ODOO_PREFIX;
    }
    if (!isNativeTechnicalPath(pathname)) {
        return `${ODOO_PREFIX}${pathname.startsWith("/") ? pathname : `/${pathname}`}`;
    }
    return pathname;
}

patch(router, {
    // Odoo remains responsible for serializing all action/menu/record state.
    // We only change the visible prefix.
    stateToUrl(state) {
        return removeOdooPrefix(super.stateToUrl(state));
    },

    // Popstate/back-forward can contain a clean URL. Parse a clone with the
    // canonical Odoo prefix while leaving the browser's visible URL untouched.
    urlToState(urlObj) {
        const url = new URL(urlObj.href);
        url.pathname = cleanPathToNative(url.pathname || "/");
        return super.urlToState(url);
    },
});

/**
 * Hard-refresh strategy (v19.0.1.0.3)
 * -----------------------------------
 * A direct clean request is server-redirected to /odoo/... so Odoo's core
 * router bootstraps from its native URL.  By the time this addon module runs,
 * that native state is already valid.  We therefore ONLY clean the address
 * bar with history.replaceState; we never replace/reparse Odoo's router state.
 * This avoids the white-screen race from the older implementation.
 */
if (
    window.location.pathname === ODOO_PREFIX ||
    window.location.pathname.startsWith(`${ODOO_PREFIX}/`)
) {
    const cleanPath = removeOdooPrefix(window.location.pathname);
    const cleanUrl = `${cleanPath}${window.location.search}${window.location.hash}`;
    window.history.replaceState(window.history.state, "", cleanUrl);
}

// Protect against literal /odoo hrefs left by native/third-party templates.
document.addEventListener(
    "click",
    (ev) => {
        const anchor = ev.target?.closest?.("a[href]");
        if (!anchor) {
            return;
        }
        const href = anchor.getAttribute("href") || "";
        if (href === ODOO_PREFIX || href.startsWith(`${ODOO_PREFIX}/`)) {
            anchor.setAttribute("href", removeOdooPrefix(href));
        }
    },
    true
);
