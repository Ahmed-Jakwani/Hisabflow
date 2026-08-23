import os,time,sys
import random, string
import json
import subprocess
# import imp,re,shutil
import argparse
import logging
import functools

from collections import defaultdict
import socket

from contextlib import closing
#from . import pg_query
from . import module_visibility
_logger = logging.getLogger(__name__)

#operation = "create" #clone/create


try:
    import docker
except ImportError as e:
    _logger.info("Docker Library not installed!!")
    
try:
    import erppeek
except ImportError as e:
    _logger.error("erppeek library not installed!!")
    

def check_error(func):
    functools.wraps(func)
    def wrapper(*args,**argc):
        try:
            return func(*args,**argc)
        except Exception as e:
            _logger.error("Error %s occurred at %s"%(str(e),func.__name__))
            return False
    return wrapper


def connect_db(url , database , user_name , passwd , flag = True):
    count = 0
    client = ""
    while count < 5: # Let me try 5 times not more
        try:
            _logger.info("Attempt %d %s."%(count,flag))
            if flag:
                client = erppeek.Client(server=str(url)) #Connect without specifying DB for creation of new.
            else:
                client = erppeek.Client(server=str(url),db = database, user = user_name,password = passwd) # connect specifically as new DB has to cloned. Need db's admin credentials
            break
        except Exception as e:
            _logger.info("Could not Connect. Error %s"%str(e))
            count += 1
            time.sleep(4)
    if count == 5:
        _logger.info("Maximum attempt made but couldn't connect")
        return False # Tried enough times, still couldn't connect.
    _logger.info("Connection built!! %s"%client)
    return client

@check_error
def cloning(client,database_name,admin_passwd):
    try:
        count = 0
        for each in range(5):
            res = client.clone_database(admin_passwd,database_name) #cloning DB using admin password 
            count += 1
            _logger.info("%s Attempt to clone!! %s"%(database_name,res))
            if res:
                break
        if count > 4 and not res:
            _logger.info("DB couldn't be cloned %s"%database_name)
            return False
    except Exception as e:
        _logger.error("%s cloned!!"%database_name)
        _logger.error("Error %s"%str(e))
        return False
    return True

def cloning_db(source_db,new_db):
   pg_host = 'localhost'
   pg_port = '9432'
   pg_user = 'odoo'
   pg_password = 'odoo'
   pg_database = "postgres"
   query = '''SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '%s';
       CREATE DATABASE %s WITH TEMPLATE %s OWNER %s;
       '''%(source_db,new_db,source_db,pg_user)
   with pgX:
       result = pgX.selectQuery(query)
   return result

@check_error
def create_new(client,database_name,user,passwd,admin_passwd): #send Odoo admin password. Using default (Admin) for now
    client.create_database(admin_passwd,database_name,user_password=passwd, login=user) #creating a new database, mentioning new db name, user name and password  
    _logger.info("%s created!!"%database_name)
    return True


#: `ir.module.module.state` values that mean "this module's code is active in the DB".
INSTALLED_STATES = ('installed', 'to upgrade')


def module_states(client, modules):
    """
    Return {module_name: state} for `modules`, straight out of ir_module_module.

    Uses search_read rather than erppeek's `client.read(model, domain, 'state')`
    convenience: with a single field name that returns a FLAT LIST OF VALUES
    (e.g. ['uninstalled']), not a list of dicts, which makes it impossible to tell
    which value belongs to which module. search_read is explicit and unambiguous.

    Names absent from the result have no ir_module_module row at all, which means
    Odoo has never seen the module - normally because its files aren't in the
    container's addons path (common-addons_v*).
    """
    rows = client.execute('ir.module.module', 'search_read',
                          [('name', 'in', list(modules))], ['name', 'state'])
    return {row['name']: row['state'] for row in rows}


def _module_states_with_retry(client, modules, attempts=6, delay=5):
    """
    module_states(), retried - installing a module reloads the target's registry,
    which routinely kills the in-flight XML-RPC call and can make the next one or
    two fail too while Odoo comes back up. Returns None if it never succeeded.
    """
    for attempt in range(attempts):
        try:
            return module_states(client, modules)
        except Exception as e:
            _logger.warning("Could not read module states (attempt %s/%s): %r",
                            attempt + 1, attempts, e)
            time.sleep(delay)
    return None


def install_modules(client, modules=None):
    """
    Install `modules` into the connected database and report honestly which ones
    did NOT end up installed.

    Returns (all_ok, modules_missed).

    Two things this deliberately does differently from the original:

    1. It calls `button_immediate_install` instead of erppeek's `client.install()`.
       erppeek presses `button_install` - which only *flags* the module as
       'to install' - and then relies on `base.module.upgrade.upgrade_module()` to
       actually apply it. Both of those reload the target registry, which frequently
       tears down the XML-RPC connection mid-call, so the caller sees a "connection
       error" for an install that may well have succeeded. `button_immediate_install`
       is the same entry point the Apps UI uses and needs no second step.

    2. It decides success by RE-READING ir_module_module afterwards, not by the
       absence of an exception. "No exception" never proved anything here: a module
       whose files are missing from common-addons_v* has no ir_module_module row,
       and a registry-reload disconnect looks like a failure even on success. The
       database's own state is the only trustworthy answer.
    """
    modules = list(modules or [])
    if not modules:
        return (True, [])

    # Re-scan the addons path first. A module whose files were only just copied into
    # common-addons_v* has no ir_module_module row yet, and every install attempt
    # against it would fail as "not found" until update_list() runs.
    try:
        client.model('ir.module.module').update_list()
    except Exception as e:
        _logger.warning("ir.module.module.update_list() failed, continuing anyway: %r", e)

    known = {}
    try:
        known = module_states(client, modules)
    except Exception as e:
        _logger.warning("Could not pre-read module states: %r", e)

    for name in modules:
        if known and name not in known:
            _logger.error("Module %s has no ir_module_module row in this database - its files "
                          "are almost certainly missing from the container's addons path "
                          "(common-addons_v*). Nothing in Odoo can install it from here.", name)

    for name in modules:
        if known.get(name) in INSTALLED_STATES:
            _logger.info("Module %s already installed, skipping", name)
            continue
        try:
            ids = client.execute('ir.module.module', 'search', [('name', '=', name)])
            if not ids:
                continue  # already logged above
            client.execute('ir.module.module', 'button_immediate_install', ids)
            _logger.info("Requested immediate install of module %s", name)
        except Exception as e:
            # Very likely the registry reload dropping the connection. Don't decide
            # anything here - the verification pass below is authoritative.
            _logger.warning("button_immediate_install(%s) raised %r - will verify actual state",
                            name, e)
        time.sleep(1)

    final = _module_states_with_retry(client, modules)
    if final is None:
        # Genuinely couldn't verify. Report the modules as missed rather than
        # claiming success - silently-wrong bookkeeping is what caused the original
        # "everything says installed but nothing is" state.
        _logger.error("Could not verify module install state for %r - reporting as missed", modules)
        return (False, modules)

    modules_missed = [m for m in modules if final.get(m) not in INSTALLED_STATES]
    for m in modules:
        _logger.info("Module %s final state: %r", m, final.get(m))
    if modules_missed:
        _logger.error("Modules NOT installed after install pass: %r", modules_missed)
    return (not modules_missed, modules_missed)


def verify_module_installed(client, module):
    """Back-compat single-module helper. True/False, or None if unreadable."""
    try:
        return module_states(client, [module]).get(module) in INSTALLED_STATES
    except Exception as e:
        _logger.warning("Could not verify install state of module %s: %r", module, e)
        return None


def create_saas_client(operation = None, odoo_url=None, odoo_username = None, odoo_password = None, base_db=None, database_name=None,modules_list=[],admin_passwd = "admin", common_addons_path=None, entitled_modules=None):

    response = {'modules_installation' : False, "modules_missed" : modules_list}
    if operation not in ['clone','create','install']:
        response['message'] = "Invalid Operation"
        return response
    _logger.info("Trying to connect DB")
    client = connect_db(odoo_url,base_db, odoo_username, odoo_password, flag = (True if operation != "clone" else False)) # base db for cloningelse it doesn't matter
    if not client:
        return response
    _logger.info("Connection Made %s"%client)
#     
    if operation == 'clone':
        _logger.info("Lets CLone DB!!")
        response['db_cloned'] = cloning(client, database_name, admin_passwd)  #admin_password  and database to be cloned into
        _logger.info("Lets CLone DB!! %r",response['db_cloned'])
    elif operation == 'create':
        response['db_create'] = create_new(client, database_name, odoo_username, odoo_password,admin_passwd) # new database name along with credentials

    if len(modules_list) and operation == 'install':
       client = connect_db(odoo_url,database_name, odoo_username, odoo_password, flag = False)
       if not client:
           response['modules_installation'] = False
           return response
       response['modules_installation'],response['modules_missed'] = install_modules(client, modules_list)
       # Tighten which OTHER custom modules this database can even see, now that the
       # entitled ones (and whatever they pulled in as dependencies) are installed.
       if common_addons_path:
           response['visibility'] = module_visibility.enforce_module_visibility(
               client, entitled_modules or modules_list, common_addons_path)
    return response
