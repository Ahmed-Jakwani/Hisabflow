# -*- coding: utf-8 -*-
"""
Maintains /etc/nginx/conf.d/client-ports.conf, the `map $host $client_port`
that backs the wildcard-clients.hisabflow.tech catch-all nginx vhost. That
single vhost (server_name ~^(?<sub>.+)\.hisabflow\.tech$;) already terminates
TLS for every *.hisabflow.tech subdomain using the shared wildcard cert, so a
new client only needs one line added here (no per-client nginx vhost file or
cert of its own) to be reachable over HTTPS.

This file lives purely on the nginx host, not inside the odoo19 container, so
it has to be read/written over SFTP (same reasoning as
nginx_vhost.execute_on_host_via_ssh in saas_localhost.py).
"""
import logging

import paramiko

_logger = logging.getLogger(__name__)

MAP_PATH = "/etc/nginx/conf.d/client-ports.conf"


def update_client_port_map(ssh_conf, hostname, port, map_path=MAP_PATH):
    if not (ssh_conf.get("host") and ssh_conf.get("user") and ssh_conf.get("key")):
        _logger.error("Nginx SSH settings not configured (nginx_ssh_host/user/key in saas.conf)")
        return False

    hostname = str.lower(hostname)
    new_line = "    %s    %s;" % (hostname, port)

    try:
        ssh_obj = paramiko.SSHClient()
        ssh_obj.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh_obj.connect(hostname=ssh_conf["host"], port=ssh_conf.get("port", 22), username=ssh_conf["user"], key_filename=ssh_conf["key"], timeout=15)

        sftp = ssh_obj.open_sftp()
        with sftp.open(map_path) as f:
            lines = f.read().decode().splitlines()

        out_lines = []
        inserted = False
        for line in lines:
            stripped = line.strip()
            if stripped.split() and stripped.split()[0] == hostname:
                continue  # drop the old entry for this host; re-added below
            if stripped.startswith("default") and not inserted:
                out_lines.append(new_line)
                inserted = True
            out_lines.append(line)
        if not inserted:
            for i in range(len(out_lines) - 1, -1, -1):
                if out_lines[i].strip() == "}":
                    out_lines.insert(i, new_line)
                    inserted = True
                    break

        with sftp.open(map_path, "w") as f:
            f.write(("\n".join(out_lines) + "\n").encode())
        sftp.close()

        stdin, stdout, stderr = ssh_obj.exec_command("nginx -t && nginx -s reload")
        exit_status = stdout.channel.recv_exit_status()
        if exit_status != 0:
            _logger.error("nginx -t/-s reload failed after client-ports.conf update: %r", stderr.read())
        ssh_obj.close()
        return exit_status == 0
    except Exception as e:
        _logger.error("update_client_port_map failed: %r", e)
        return False
