# -*- coding: utf-8 -*-
#################################################################################
#
#   Copyright (c) 2016-Present Webkul Software Pvt. Ltd. (<https://webkul.com/>)
#   See LICENSE file for full copyright and licensing details.
#   License URL : <https://store.webkul.com/license.html/>
# 
#################################################################################

import os
from odoo import api, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.addons.wk_backup_restore.models.lib.manage_backup_crons import LOG_FILE_PATH
from odoo.tools.config import config

import logging
_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'


    def check_database_backup_configuration(self):
        try:
            import paramiko
            from crontab import CronTab
            import argparse
            import psycopg2

            cron_status = self.check_cron_status()
            if not cron_status.get('installed'):
                raise Exception(f"\nCrontab is not installed on the server. Kindly install it.")
            
            if not cron_status.get('running'):
                raise Exception(f"\nCrontab is not running on the server. Kindly run it.")

            if not config.get('master_passwd', False):
                raise Exception(f"\nMaster password parameter(master_passwd) not set in Odoo conf file!!")

            # Creating the backup log file if doesn't exists
            if not os.path.exists(LOG_FILE_PATH):
                _logger.debug("========== Creating Backup Log File ==========")
                fp = open(LOG_FILE_PATH, 'x')
                fp.close()

            superuser_response = self.check_db_superuser() 
            if not superuser_response.get('superuser'):
                raise Exception(f"\nThe SUPERUSER role is not set for Odoo database user.")
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'type': 'success',
                    'message': _("Database Backup is successfully configured."),
                }
            }
            
        except ImportError as e:
            import_err = _(f"""\nThe following modules are not installed:\n{e.name}""")
            raise ValidationError(import_err)
        except PermissionError as e:
            raise ValidationError(f"\n{e.strerror} while creating logfile '{e.filename}'")
        except FileNotFoundError as e:
            raise ValidationError(f"\n{e.strerror} while creating logfile '{e.filename}'")
        except Exception as e:
            raise ValidationError(f"\n{str(e)}")
        
    
    @api.model
    def check_db_superuser(self):
        """Check if the PostgreSQL user has superuser privileges"""
        self.env.cr.execute("""
            SELECT rolname, rolsuper 
            FROM pg_roles 
            WHERE rolname = current_user
        """)
        res = self.env.cr.fetchone()

        username = res[0] if res else None
        is_superuser = bool(res[1]) if res else False

        _logger.debug(f"DB User Superuser Check -> username: {username}, superuser: {is_superuser}")

        return {
            "username": username,
            "superuser": is_superuser
        }
    

    # @api.model
    # def check_cron_status(self):
    #     """Check if cron is installed and running"""
    #     import shutil
    #     import subprocess
    #     result = {
    #         "installed": False,
    #         "running": False,
    #         "binary": None,
    #     }

    #     # Check if cron is installed
    #     cron_path = shutil.which("cron") or shutil.which("crond")
    #     if cron_path:
    #         result["installed"] = True
    #         result["binary"] = cron_path

    #         # Check if cron service is running
    #         try:
    #             proc = subprocess.run(
    #                 ["service", "cron", "status"],
    #                 stdout=subprocess.PIPE,
    #                 stderr=subprocess.PIPE,
    #                 text=True,
    #                 timeout=5
    #             )
    #             output = (proc.stdout + proc.stderr).lower()
    #             if "running" in output and "not running" not in output:
    #                 result["running"] = True
    #         except Exception as e:
    #             _logger.warning("Cron status check failed: %s", e)

    #     _logger.info("Cron status: %s", result)
    #     return result


    @api.model
    def check_cron_status(self):
        """Check if cron is installed and running (Docker & non-Docker safe)"""
        import shutil
        import subprocess
        result = {
            "installed": False,
            "running": False,
            "method": None
        }

        # Check if cron binary exists
        cron_bin = (
            shutil.which("cron") or
            shutil.which("crond")
        )

        if not cron_bin:
            _logger.warning("Cron binary not found")
            return result

        result["installed"] = True
        result["method"] = "binary"

        # systemd check
        if shutil.which("systemctl"):
            try:
                proc = subprocess.run(
                    ["systemctl", "is-active", "cron"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=5
                )
                if proc.stdout.strip() == "active":
                    result["running"] = True
                    result["method"] = "systemctl"
                    return result
            except Exception as e:
                _logger.debug("systemctl check failed: %s", e)

        # service check
        if shutil.which("service"):
            try:
                proc = subprocess.run(
                    ["service", "cron", "status"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=5
                )
                output = (proc.stdout + proc.stderr).lower()
                if "running" in output and "not running" not in output:
                    result["running"] = True
                    result["method"] = "service"
                    return result
            except Exception as e:
                _logger.debug("service check failed: %s", e)

        _logger.info("Cron status result: %s", result)
        return result
