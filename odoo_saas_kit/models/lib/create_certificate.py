import shlex
import subprocess
import os
import socket
import logging
import paramiko
_logger = logging.getLogger(__name__)

"""

We have to add this to nginx vhost files.
Or better to add this to one single file
and include it in various vhosts

location ^~ /.well-known/acme-challenge/ {
    default_type "text/plain";
}



certbot certonly --webroot -w /home/www/letsencrypt -d domain.com --dry-run
todo:
"""

def create_dir(webroot_path="/usr/share/nginx/html/"):
    """
    Directory is automatically created, when using certbot.
    Only use this if creating .well-known/acme-challenge
    manually is specifically required.
    """

    acme_challenge_dir_path = os.path.join(webroot_path, ".well-known/acme-challenge")
    if os.path.exists(acme_challenge_dir_path):
        _logger.info(acme_challenge_dir_path, "exists")
    else:
        os.makedirs(acme_challenge_dir_path)

    return acme_challenge_dir_path

def check_ips(custom_domain, subdomain):
    """
    ip_addr1: ip address of custom domain.
    ip_addr2: ip address of subdomain.
    """    
    _logger.info(locals())
    try:
        ip_addr1 = socket.gethostbyname(custom_domain)
        ip_addr2 = socket.gethostbyname(subdomain)
    except Exception as e:
        _logger.info("The Entered Domain(Sub) could not be resolved %r"%e)
        raise Exception("The Entered Domain(Sub) could not be resolved. Please ensure domain is mapped correctly!!")
    if ip_addr1 != ip_addr2:
        _logger.info("Domain %s not yet mapped. Please make the necessary DNS changes before proceeding!!"%custom_domain)
        raise Exception("Domain %s not yet mapped. Please make the necessary DNS changes before proceeding!!"%custom_domain)
    return True

def run_on_host_via_ssh(cmd, ssh_host, ssh_port, ssh_user, ssh_key):
    """
    nginx and certbot live on the host, not inside the odoo container, so
    this has to run over SSH rather than local subprocess (mirrors
    nginx_vhost.execute_on_host_via_ssh in saas_localhost.py).
    """
    ssh_obj = paramiko.SSHClient()
    ssh_obj.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh_obj.connect(hostname=ssh_host, port=ssh_port, username=ssh_user, key_filename=ssh_key, timeout=15)
    try:
        stdin, stdout, stderr = ssh_obj.exec_command(shlex.join(cmd))
        returncode = stdout.channel.recv_exit_status()
        out = stdout.read()
        err = stderr.read()
    finally:
        ssh_obj.close()
    return {
        "status": not bool(returncode),
        "stdout": out.decode(),
        "stderr": err.decode(),
    }

def generate_certificate(domain_name, client_email, webroot_path, dry_run, ssh_host=None, ssh_port=22, ssh_user=None, ssh_key=None):
    #path = create_dir()
    cmd = ["sudo", "certbot", "-n", "certonly", "--webroot", "-w", webroot_path]
    cmd.extend(["-d", domain_name])
    if dry_run:
        cmd.extend(["--agree-tos", "-m", client_email, "--dry-run"])
    else:
        cmd.extend(["--agree-tos", "-m", client_email])

    """
    Function returns:
    status: return code of the process, return code > 1 means ERR and < 1 means OK.
    stdout: stdout of process.
    stderr: stderr of process.
    """
    if ssh_host:
        try:
            return run_on_host_via_ssh(cmd, ssh_host, ssh_port, ssh_user, ssh_key)
        except Exception as e:
            _logger.error(e)
            return {"status": False, "stdout": "", "stderr": repr(e)}

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = proc.communicate()
        return {
            "status": not bool(proc.returncode),
            "stdout": out.decode(),
            "stderr": err.decode()
        }
    except subprocess.CalledProcessError as e:
        _logger.info(e)

    # if command is successful then certs will be available in /etc/letsencrypt/live/<domain>

if __name__ == "__main__":
    generate_certificate("domain.com", "abc@email.com", "/usr/share/nginx/html/", dry_run=True)
    #check_ips("google.com", "youtube.com")

