# -*- coding: utf-8 -*-

import logging


from odoo import models
from odoo.http import request
from odoo.addons.web.controllers.utils import get_action_triples

_logger = logging.getLogger(__name__)


# The public Website owns '/'.  '/app' is the clean equivalent of Odoo's
# otherwise-empty '/odoo' webclient root.
CLEAN_BACKEND_ROOT = "/app"

# Optional exact allow-list system parameter:
#   hisabflow.clean_urls.allowed_hosts
DEFAULT_ALLOWED_HOSTS = {
    "hisabflow.tech",
    "www.hisabflow.tech",
    "db19-templates.hisabflow.tech",
    "localhost",
    "127.0.0.1",
}


def _request_host():
    return (request.httprequest.host or "").split(":", 1)[0].strip().lower()


def _configured_allowed_hosts():
    raw = request.env["ir.config_parameter"].sudo().get_param(
        "hisabflow.clean_urls.allowed_hosts",
        "",
    )
    return {host.strip().lower() for host in raw.split(",") if host.strip()}


def _is_allowed_host():
    host = _request_host()
    configured_hosts = _configured_allowed_hosts()
    if configured_hosts:
        return host in configured_hosts
    return host in DEFAULT_ALLOWED_HOSTS or host.endswith(".hisabflow.tech")


def _is_clean_backend_path(path):
    """Return True only for paths Odoo itself understands as backend actions.

    Website/portal/technical namespaces are deliberately excluded.  Website's
    own fallback is always called before this validator, so real Website pages
    (including Contact Us, shop/blog pages and future Website Builder pages)
    retain priority over a backend path with the same slug.
    """
    if not path:
        return False

    normalized = "/" + path.strip("/")
    if normalized == CLEAN_BACKEND_ROOT:
        return True

    technical_prefixes = (
        "/web",
        "/websocket",
        "/report",
        "/my",
        "/mail",
        "/api",
        "/jsonrpc",
        "/xmlrpc",
        "/scoped_app",
    )

    def _is_under_namespace(prefix):
        return normalized == prefix or normalized.startswith(f"{prefix}/")

    if normalized == "/" or any(
        _is_under_namespace(prefix) for prefix in technical_prefixes
    ):
        return False

    # The exact /website may be the backend Website application.  Nested
    # /website/* routes belong to Website internals/frontend controllers.
    if normalized.startswith("/website/"):
        return False

    try:
        return bool(list(get_action_triples(request.env, normalized)))
    except (ValueError, TypeError, KeyError):
        return False
    except Exception:
        _logger.debug(
            "HisabFlow clean URL validation failed for %s",
            normalized,
            exc_info=True,
        )
        return False


def _native_backend_url(clean_path):
    """Build the canonical Odoo backend URL while preserving query params.

    The redirect is intentional.  Odoo 19 initializes its JS router *before*
    addon backend modules execute, and that core router only recognizes action
    path segments reliably when the initial document URL is /odoo/....

    Loading the native URL first guarantees the action exists on hard refresh.
    The companion JS removes /odoo from the address bar immediately after the
    webclient has booted, so normal users continue to see clean URLs.
    """
    if clean_path.rstrip("/") == CLEAN_BACKEND_ROOT:
        native_path = "/odoo"
    else:
        native_path = "/odoo" + clean_path

    query_string = request.httprequest.query_string.decode("utf-8", errors="ignore")
    if query_string:
        return f"{native_path}?{query_string}"
    return native_path


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _serve_fallback(cls):
        """Website first; only validated backend slugs are redirected to /odoo.

        Why redirect instead of directly rendering Home.web_client()?
        -------------------------------------------------------------
        Direct rendering at /sales or /action-585 gives Odoo's *server* the
        correct page, but Odoo 19's client router has already parsed that clean
        document URL before this addon's JS patch can run.  On a hard refresh
        the initial action is therefore empty and the user sees a white page.

        A short redirect to the native /odoo/... document lets Odoo bootstrap
        exactly as designed.  Once booted, clean_router.js strips /odoo from
        history without reloading.  Subsequent SPA navigation remains clean.
        """
        response = super()._serve_fallback()
        if response:
            return response

        if request.httprequest.method not in ("GET", "HEAD"):
            return response
        if not _is_allowed_host():
            return response

        clean_path = request.httprequest.path or "/"
        if not _is_clean_backend_path(clean_path):
            return response

        target = _native_backend_url(clean_path)
        _logger.debug("Redirecting HisabFlow clean backend URL %s -> %s", clean_path, target)
        return request.redirect(target, code=302)
