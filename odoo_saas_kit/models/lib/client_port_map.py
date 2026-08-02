# -*- coding: utf-8 -*-
"""
Maintains /etc/nginx/conf.d/client-ports.conf, the `map $host $client_port`
that backs the wildcard-clients.hisabflow.tech catch-all nginx vhost. That
single vhost (server_name ~^(?<sub>.+)\.hisabflow\.tech$;) already terminates
TLS for every *.hisabflow.tech subdomain using the shared wildcard cert, so a
new client only needs one line added here (no per-client nginx vhost file or
cert of its own) to be reachable over HTTPS.

This file lives purely on the nginx host, not inside the odoo19 container.
The nginx_ssh_* key (saas.conf) is locked down via an SSH forced-command
(see /usr/local/sbin/nginx-deploy-dispatch.sh on the host) that only accepts
the literal nginx reload command or `update-map <hostname> <port>` -- it
cannot run arbitrary commands or open an SFTP session, so the actual file
edit has to happen host-side via that dispatcher, not by reading/writing the
file directly over this connection.
"""
import logging

import paramiko

_logger = logging.getLogger(__name__)


def update_client_port_map(ssh_conf, hostname, port):
    if not (ssh_conf.get("host") and ssh_conf.get("user") and ssh_conf.get("key")):
        _logger.error("Nginx SSH settings not configured (nginx_ssh_host/user/key in saas.conf)")
        return False

    hostname = str.lower(hostname)
    cmd = "update-map %s %s" % (hostname, port)

    try:
        ssh_obj = paramiko.SSHClient()
        ssh_obj.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh_obj.connect(hostname=ssh_conf["host"], port=ssh_conf.get("port", 22), username=ssh_conf["user"], key_filename=ssh_conf["key"], timeout=15)
        stdin, stdout, stderr = ssh_obj.exec_command(cmd)
        exit_status = stdout.channel.recv_exit_status()
        if exit_status != 0:
            _logger.error("client-ports.conf update failed: %r", stderr.read())
        ssh_obj.close()
        return exit_status == 0
    except Exception as e:
        _logger.error("update_client_port_map failed: %r", e)
        return False
