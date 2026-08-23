# -*- coding: utf-8 -*-
#################################################################################
#
#   Copyright (c) 2016-Present Webkul Software Pvt. Ltd. (<https://webkul.com/>)
#   See LICENSE file for full copyright and licensing details.
#   License URL : <https://store.webkul.com/license.html/>
#
#################################################################################
from urllib.parse import urlencode
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from . lib import containers, install_module
from .compat import get_module_resource, is_new_id
from . lib import query
from . lib import saas
from . lib import auto_login_token
from . lib import hostnames
from . lib import saas_client_db
from . lib import module_visibility
import logging
import time
import os
import docker
import base64
import re
from . lib import client
from .static_saas_kit import SAAS_ODOO_VERSION


_logger = logging.getLogger(__name__)

STATE = [
    ('draft', "Draft"),
    ('confirm', "Confirmed"),
    ('cancel', "Cancelled")
]

BILLING_CRITERIA = [
    ('fixed', "Fixed Rate"),
    ('per_user', 'Based on the No. of users')
]


class SaasPlans(models.Model):
    _name = "saas.plan"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = "id desc"
    _description = 'Class for managing SaaS subscription plans.'

    @api.depends('name')
    def _compute_db_template_name(self):
        """
            Compute the db_template name by using name and id of the records
        """
        
        for obj in self:
            if obj.name and not is_new_id(obj.id) and not obj.db_template:
                template_name = obj.name.lower().replace(" ", "_")
                obj.db_template = "{}_tid_{}".format(template_name, obj.id)

    def _default_saas_server(self):
        """
            Return the default saas server id
        """
        
        saas_servers = self.env['saas.server'].search([])
        if saas_servers:
            return saas_servers[0].id
        return False

    def _get_contract_count(self):
        """
            Set the total contract count associated with this plan
        """
        
        for obj in self:
            contracts = self.env['saas.contract'].search(
                [('plan_id', '=', obj.id)])
            obj.contract_count = len(contracts)

    def action_view_contracts(self):
        """
            Open the contract tree view.
            called from the button on saas plan
        """
        
        contracts = self.env['saas.contract'].search(
            [('plan_id', '=', self.id)])

        action = self.env.ref('odoo_saas_kit.saas_contract_action').read()[0]
        if len(contracts) > 1:
            action['domain'] = [('id', 'in', contracts.ids)]
        elif len(contracts) == 1:
            action['views'] = [(self.env.ref(
                'odoo_saas_kit.saas_contract_form_view').id, 'form')]
            action['res_id'] = contracts.ids[0]
        else:
            action = {'type': 'ir.actions.act_window_close'}
        return action

    @api.onchange('server_id')
    def server_id_change(self):
        """
            Method to set base url of saas plan from the domain name of linked saas server
        """
        
        for obj in self:
            obj.saas_base_url = obj.server_id.server_domain

    name = fields.Char(string='Plan Name', required=True, tracking = True)
    saas_base_url = fields.Char(string="SaaS Domain(Base URL)", required=True, tracking = True)
    image = fields.Binary(string='Image')
    summary = fields.Char(string="Plan Summary", tracking = True)
    db_dropped = fields.Boolean(string="DB deleted", default=False, tracking = True)

    expiration = fields.Integer(
        'Expiration (hours)',
        help='time to delete database. Use for demo', tracking = True)
    grace_period = fields.Integer(
        'Grace period (days)', help='initial days before expiration')
    product_template_ids = fields.One2many(
        comodel_name="product.template",
        string="Linked Products",
        inverse_name="saas_plan_id", tracking =True)
    use_specific_user_template = fields.Boolean(
        string="Use Specific User Template", help="""Select if you want to provide some specific permissions to your user for acessing its odoo instance which is going to be created by this plan.""", tracking = True)
    template_user_id = fields.Char(string="Database Template User ID", help="""Enter the user_id of User which you have created in the DB Template with some specific permissions or whose permission you want to grant to the user of odoo instances which is going to be created by this plan.""", tracking = True)
    saas_module_ids = fields.Many2many(
        comodel_name="saas.module",
        relation="saas_plan_module_relation",
        column1="plan_id",
        column2="module_id",
        string="Related Modules",
        tracking = True)
    description = fields.Text('Plan Description')
    recurring_interval = fields.Integer(
        default=1, string='Default Billing Cycle', tracking = True)
    recurring_rule_type = fields.Selection(
        [('daily', 'Day(s)'),
         ('weekly', 'Week(s)'),
         ('monthly', 'Month(s)'),
         ('monthlylastday', 'Month(s) last day'),
         ('yearly', 'Year(s)'),
         ],
        default='monthly',
        string='Recurrence',
        readonly=True
    )
    # total_cycles = fields.Integer(string="Number of Cycles", default=1)
    trial_period = fields.Integer(string="Complimentary(Free) days", default=0, tracking = True)
    # server_setup_type = fields.Selection(selection=SERVER_SETUP, string="Server Setup Type", default="SINGLE", required=True)
    is_multi_server = fields.Boolean(string="Deploy Client's on Remote Server", default=False,  tracking = True)
    server_id = fields.Many2one(
        comodel_name="saas.server",
        string="SaaS Server",
        default=_default_saas_server,
        domain=[('state', '=', 'confirm'), ('host_server', '=', 'self')], tracking = True)
    default_saas_servers_ids = fields.One2many(comodel_name="server.priority", inverse_name="saas_plan_id")
    db_template = fields.Char(
        compute='_compute_db_template_name', string="DB Template Name",default="", store=True, help="Enter a uniquie name to create a DB associated to this plan or leave it blank and let odoo to give it a unique name." , tracking = True)
    container_id = fields.Char(string="Instance ID")
    state = fields.Selection(
        selection=STATE, string="States", default="draft", tracking=True)
    contract_count = fields.Integer(
        string='Contract Count', compute='_get_contract_count', readonly=True)
    billing_criteria = fields.Selection(
        selection=BILLING_CRITERIA,
        string="Default Billing Criteria",
        required=True,
        default="fixed")
    per_user_pricing = fields.Boolean(string="User Based Pricing", help="Used to enable the per user costing of end user's instance", tracking = True)
    user_cost = fields.Float(help="PUPC(Per User Per Cycle cost)", tracking = True)
    min_users = fields.Integer(string="Min. No. of user", help="Minimum number of users whose cost client have to pay either created or not", default="1", tracking = True)
    max_users = fields.Integer(string="Max. No. of user", help="End user is not allowed to create user more than Maximum number of user limit. Enter -1 to allow user to create infinte number of user.", tracking = True, default="1")
    due_users_price = fields.Float(string="Due users price", default="1.0")
    user_product = fields.Many2one(comodel_name="product.product", string="Product for user calculation", help="Select a product for calculation costing user pricing.", domain="[('is_user_pricing', '=', True)]") 
    modules_status_ids = fields.One2many(comodel_name="saas.module.status", inverse_name="plan_id", string="Module installed/uninstalled status")
    is_all_installed = fields.Boolean(string="All Modules Installed", default=False)
    active = fields.Boolean(string="Active", default=True)
    
    def print_logs(self, log_type, message, line_no):
        if log_type=='info':
            _logger.info("Saas Plan PLAN{} : {} at Line {}".format(self.id, message, line_no))
        elif log_type=='warn':
            _logger.info("Warning in Saas Plan PLAN{} : {} at Line {}".format(self.id, message, line_no))
        elif log_type=='error':
            _logger.info("Error in Saas Plan PLAN{} : {} at Line {}".format(self.id, message, line_no))
    
    
    @api.constrains('default_saas_servers_ids')
    def _check_saas_server_priority(self):
        """
        Constraint to check that there should no be same server define in Default saas server more than one time.
        """
        
        if any(len(plan.default_saas_servers_ids) != len(plan.default_saas_servers_ids.mapped('server_id')) for plan in self):
            raise ValidationError(('You cannot define two Priorities lines for the same Server.'))
        for obj in self:
            if obj.is_multi_server and not len(obj.default_saas_servers_ids):
                raise UserError("Please select atleast one server in Default Saas servers")
            
            if obj.is_multi_server and len(obj.default_saas_servers_ids.mapped('priority')) != len(set(obj.default_saas_servers_ids.mapped('priority'))):
                raise UserError("Two servers cannot have same priority, Please udpate priority for remote servers.")

            for server_id in obj.default_saas_servers_ids.mapped('server_id'):
                for field in ('db_host', 'db_port', 'db_user', 'db_pass'):
                    if server_id.mapped(field) != obj.server_id.mapped(field):
                        raise UserError("Select only those Server whose database server is same as {} server".format(obj.server_id.name))
            return True

    
    @api.onchange('max_users')
    def check_max_user(self):
        """
            Validation for max users max users should not be less than min users
        """
        
        for obj in self:
            if obj.max_users != -1 and obj.max_users < obj.min_users:
                raise UserError("Max. No. of users must be greater than or Equal to Min. no. of users")
            else:
                obj.max_users = obj.max_users

    @api.onchange('min_users')
    def check_min_users(self):
        """
            Validation for min users
        """
        for obj in self:
            if obj.min_users < 1:
                raise UserError("Min. No. of users can't be less than 1")
            if obj.min_users > obj.max_users:
                raise UserError("Max. No. of users must be greater than or Equal to Min. no. of users")

    def reset_to_draft(self):
        """
            Method to change the state of the plan, to allow admin to edit the plans if there is contract is associated with plan.
            Called from Button over plan
        """
        
        for obj in self:
            contracts = self.env['saas.contract'].search([('plan_id', '=', obj.id)])
            if contracts:
                raise UserError("This plan has some contracts associated with it!")
            obj.state = 'draft'

    @api.model
    def select_server(self):
        """
        Select Server in case of Remote server setup type according to their priority and number of clients.
        """
        self.print_logs('info', 'Called select_server', '243')
        for obj in self:
            if len(obj.default_saas_servers_ids):
                priority_list = list(obj.default_saas_servers_ids)
                priority_list.sort(key = lambda priority_record: priority_record.priority) 
                
                for priority in priority_list:
                    if priority.server_id.max_clients > priority.server_id.total_clients:
                        server = priority.server_id
                        break
                else:
                    return (False, 'All server limits over. Please create a new server!')
                return (True, server)

            else:
                return (False, 'Please select atleast one server in Default Saas servers')
        self.print_logs('info', 'Return from select_server', '259')

    def login_to_db_template(self):
        """
            Auto-login as superuser into the selected template database, via the
            `saas_kit_auto_login` addon (must be installed on the template - see
            that addon's manifest). Falls back to a plain login page if the
            template predates that addon (e.g. templates created before this was
            added to create_db_template()'s module list).

            ``/saas/login`` was provided by the legacy ``wk_saas_tool`` addon,
            which was never ported to the Odoo 19 template image;
            `saas_kit_auto_login` replaces it.
        """

        for obj in self:
            template_host = hostnames.db_template_host(
                SAAS_ODOO_VERSION, obj.saas_base_url)
            try:
                secret = auto_login_token.read_secret(
                    get_module_resource('odoo_saas_kit'), "template_master")
                token = auto_login_token.build_token(secret, obj.db_template)
                # odoo19_template_cont hosts every plan's template with no dbfilter -
                # `db` must be passed as a query param so Odoo's HTTP layer dispatches
                # to the right database BEFORE routing; the db embedded in the signed
                # token itself only gets checked *after* dispatch, inside the controller.
                login_url = "https://{}/saas_kit/auto_login/{}?{}".format(
                    template_host, token, urlencode({'db': obj.db_template}))
            except Exception as e:
                self.print_logs('error', 'Could not build auto-login token: %r' % e, '279')
                login_url = "https://{}/web/login?{}".format(
                    template_host, urlencode({'db': obj.db_template}))
            return {
                'type': 'ir.actions.act_url',
                'url': login_url,
                'target': 'new',
            }

    def restart_db_template(self):
        """
            Method restart the Template Container.
            Called from the Restart button over Saas Plan.
        """
        
        for obj in self:
            host_server, db_server = obj.server_id.get_server_details()
            self.print_logs('info', 'calling empty containers script', '299')
            response_flag = containers.action(
                operation="restart",
                container_id=obj.container_id,
                host_server=host_server,
                db_server=db_server)
            if not response_flag:
                self.print_logs('error', 'Failed to start the container', '306')
                raise UserError("Operation Failed! Unknown Error!")

    def force_confirm(self):
        """
            Method to change the state of plan to Confirm, if the db_template linked to plan is exist.
            Called from the Skip this step Button.
        """
        
        for obj in self:
            response = None
            if not obj.container_id:
                _, db_server = obj.server_id.get_server_details()
                response = query.is_db_exist(obj.db_template, db_server=db_server)
                if not response.get('result'):
                    raise UserError("Please create DB Template First!")
            obj.state = 'confirm'


    def create_status_modules(self):
        for module in self.saas_module_ids:
            if module.id not in self.modules_status_ids.module_id.ids:
                module_created=self.env['saas.module.status'].create({
                    'technical_name' : module.technical_name,
                    'module_id' : module.id,
                    'plan_id' : self.id})
        return True


    def get_installable_modules(self):
        limit = self.server_id.module_installation_limit
        _logger.info("-=-= -= -= -1 12 2 =- limit %s--- "%self.modules_status_ids)
        installable_modules = self.modules_status_ids.filtered(lambda mod:mod.status == 'uninstalled')[:limit]
        _logger.info("-=-= -=1 -= -=121 21 21- limit %r--- "%installable_modules)
        if not installable_modules:
            self.is_all_installed=True
        return installable_modules


    def install_remaining_modules(self):
        installable_modules = self.get_installable_modules()

        modules = [module.technical_name for module in installable_modules]
        host_server, db_server = self.server_id.get_server_details()
        # NOTE: template admin users are always created with `container_user`/
        # `container_passwd` (see odoo_container.create_db()) and templates never
        # go through set_user_data() (that only touches real clients), so those
        # saas.conf values are the real, working login credentials here - unlike
        # query.get_credentials(), which reads the DB's *hashed* password and can
        # never authenticate over XML-RPC (that's what caused "Connection Failure").
        config_path = get_module_resource('odoo_saas_kit')
        try:
            login = auto_login_token.read_secret(config_path, "container_user")
            password = auto_login_token.read_secret(config_path, "container_passwd")
            cred_status = True
        except Exception as e:
            _logger.error("Could not read container credentials from saas.conf: %r", e)
            cred_status = False
        if cred_status:
            try:
                response = install_module.main(dict(
                    db_name=self.db_template,
                    modules=modules,
                    version='19.0',
                    config_path = get_module_resource('odoo_saas_kit'),
                    login=login,
                    password=password))
            except Exception as e:
                    _logger.info("--------MODULE-CREATION-CREATION-EXCEPTION-------%r", e)
                    raise UserError(e)
            else:
                if response:
                    if response.get('modules_installation', False):
                        self.state = 'confirm'
                        for module in installable_modules:
                            module.status="installed"
                            if not self.get_installable_modules():
                                self.is_all_installed=True
                        # The RPC install above only touches the template's database -
                        # its running container process won't show the new module's
                        # menus/assets until it reloads its registry. Restart it here so
                        # the module is actually visible without a manual step.
                        try:
                            self.restart_db_template()
                        except Exception as e:
                            _logger.error("Installed but could not restart template container for plan %s: %r", self.display_name, e)
                    else:
                        msg = response.get('msg', 'Connection Failure')
                        if msg:
                            raise UserError(msg)
                        else:
                            raise UserError("Unknown Error. Please try again later")
                else:
                    raise UserError("No Response. Please try again later")
        else:
            raise UserError("Details Not found !")

        

        


    def create_db_template(self):
        """
            Method to create the database template of the saas plan and confirm the state of the plan.
            Called from the Create Db Template button over saas plan.
        """
        
        for obj in self:
            if not obj.db_template:
                raise UserError("Please select the DB template name first.")
            if re.match("^template_",obj.db_template):
                raise UserError("Couldn't Create DB. Please try again with some other Template Name!")
            db_template_name = "template_{}".format(obj.db_template)
            config_path = get_module_resource('odoo_saas_kit')
            status_module = obj.create_status_modules()
            installable_modules = obj.get_installable_modules()
            modules = [module.technical_name for module in installable_modules]
            # 'wk_saas_tool' used to be force-installed here for its legacy /saas/login
            # route, but that addon was never ported to the 19.0 template image - forcing
            # its install silently failed per-module (see install_modules()) and left
            # is_all_installed/module bookkeeping out of sync.
            # 'saas_kit_auto_login' replaces it: a small addon exposing the auto-login
            # route used by login_to_client_instance()/login_to_db_template() above.
            # It must be copied into this Odoo version's common-addons folder on the
            # server for it to be installable here - see the addon's own manifest.
            modules.append('saas_kit_auto_login')
            try:
                host_server, db_server = obj.server_id.get_server_details()
                self.print_logs('info', 'calling create_db_template script', '398')
                response = saas.create_db_template(
                    db_template=db_template_name,
                    modules=modules,
                    config_path=config_path,
                    host_server=host_server,
                    db_server=db_server)
            except Exception as e:
                _logger.info("--------DB-TEMPLATE-CREATION-EXCEPTION-------%r", e)
                self.print_logs('error', e, '407')
                raise UserError(e)
            else:
                if response:
                    if response.get('status', False):
                        obj.db_template = db_template_name
                        obj.state = 'confirm'
                        obj.container_id = response.get('container_id', False)
                        # response['status']=True only means the DB itself got created -
                        # the RPC module-install step (a separate connection to the fresh
                        # DB) can fail independently (e.g. transient connect failure right
                        # after DB creation) without raising, so don't blindly trust every
                        # module as installed - this exact silent-lie pattern already bit
                        # om_account_accountant once (see SAAS_KIT_NOTES.md) and just bit
                        # saas_kit_auto_login on a fresh plan the same way.
                        result = response.get('result') or {}
                        if isinstance(result, dict):
                            modules_missed = result.get('modules_missed') or []
                        else:
                            # Not a result dict, so the install step never reported
                            # anything we can trust. Treat EVERY module as not installed
                            # rather than as installed: the old code defaulted this to []
                            # and so marked everything 'installed' off a bare string
                            # ("alreadyexists"), which is precisely how both live plan
                            # templates ended up with all-green bookkeeping and none of
                            # their modules actually present.
                            _logger.warning("create_db_template returned a non-dict result %r - "
                                            "treating all modules as not installed", result)
                            modules_missed = list(modules)
                        for module in installable_modules:
                            if module.technical_name not in modules_missed:
                                module.status = "installed"
                        if not obj.get_installable_modules():
                            obj.is_all_installed = True
                        if modules_missed:
                            obj.message_post(body="Warning: these modules could NOT be installed into the template database and were left uninstalled (install them manually if needed): {}".format(", ".join(modules_missed)))

                    else:
                        msg = response.get('msg', False)
                        if msg:
                            raise UserError(msg)
                        else:
                            raise UserError("Unknown Error. Please try again later with some different Template Name")
                else:
                    raise UserError("No Response. Please try again later with some different Template Name")

    def cancel_plan(self):
        for obj in self:
            contracts = self.env['saas.contract'].search([('plan_id', '=', obj.id),('state', '!=', 'cancel')])
            if contracts:
                raise UserError("Please Cancel the Linked Contract first before cancel the Plan.")
            elif obj.state=='confirm':
                raise UserError("Please reset the plan to draft before cancelling it.")
            else:
                obj.state = 'cancel'
                for res in obj.product_template_ids:
                    logging.info(f"============res==========={res}")
                    res.website_published = False
                    res.saas_plan_id = None

    

    def unlink(self):
        """
            Unlink of no contrac is associated with the plan

            Also tears down the plan's own db_template - a plan's template
            database (and its filestore) lives inside the one shared
            per-version template container, so unlike saas.client there is
            no container/data-dir of its own to remove, just the database
            and its filestore subfolder within that shared container.
        """

        for obj in self:
            if obj.contract_count:
                raise UserError("Error: You must delete the associated SaaS Contracts first!")

        module_path = get_module_resource('odoo_saas_kit')
        for obj in self:
            if not (obj.db_template and obj.server_id):
                continue
            try:
                host_server, db_server = obj.server_id.get_server_details()
            except Exception as e:
                obj.print_logs('error', 'Could not resolve server details during unlink: %r' % e, '494')
                continue

            try:
                query.drop_database(obj.db_template, db_server=db_server)
            except Exception as e:
                obj.print_logs('error', 'Could not drop template database %r during unlink: %r' % (obj.db_template, e), '494')

            if obj.container_id:
                try:
                    client.update_values(module_path)
                    if host_server.get('server_type') == 'self':
                        client.delete_template_filestore(obj.container_id, obj.db_template)
                    else:
                        ssh_obj = client.login_remote(host_server)
                        if ssh_obj:
                            client.delete_remote_template_filestore(obj.container_id, obj.db_template, ssh_obj)
                except Exception as e:
                    obj.print_logs('error', 'Could not delete template filestore for %r during unlink: %r' % (obj.db_template, e), '494')

        return super(SaasPlans, self).unlink()

    @api.model_create_multi
    def create(self, vals_list):
        """
            Added few validation
        """
        for vals in vals_list:
            if vals.get('recurring_interval', 0) <= 0:
                raise UserError("Default Billing Cycle can't be less than 1")
            if vals.get('is_multi_server', False) and not vals.get('default_saas_servers_ids', False):
                raise UserError("Select Atleast one Server in Default Saas Servers")
            if vals.get('trial_period', 0) < 0:
                raise UserError("Complimentary Free days can't be less than 0")

        res = super().create(vals_list)
        for obj in res:
            if obj.name and not obj.db_template:
                template_name = obj.name.lower().replace(" ", "_")
                obj.db_template = "{}_tid_{}".format(template_name, obj.id)
        return res

    def write(self, vals):
        """
            Added Few validations
        """
        
        initial_list = self.saas_module_ids.ids
        if vals.get('recurring_interval', False) and vals['recurring_interval'] <= 0:
            raise UserError("Default Billing Cycle can't be less than 1")
        if vals.get('trial_period', False) and vals['trial_period'] < 0:
            raise UserError("Complimentary Free days can't be less than 0")
        if vals.get('is_multi_server', False) and not vals.get('default_saas_servers_ids', False):
            raise UserError("Select Atleast one Server in Default Saas Servers")


        ##Fix for restriction on Installed Module unlinking from Saas Plan
        for obj in self:
            if vals.get('active')==False:
                if obj.state != 'cancel':
                    raise UserError("Please cancel the SaaS plan before archiving it.")
        if vals.get('saas_module_ids') and len(vals.get('saas_module_ids')[0])!=3:
            deleted_module_list= []
            for rec in vals.get('saas_module_ids'):
                deleted_module_list.append(rec[1])
            for rec in self.modules_status_ids:
                if rec.status =='installed' and rec.module_id.id  in deleted_module_list:
                    raise UserError("Can't Remoove the saas modules as they are already Installed")
        res = super(SaasPlans, self).write(vals)


        #  ================FIX===========================
        if vals.get('saas_module_ids'):
            self.sync_contract_modules()
            self.create_status_modules()
            for rec in self.modules_status_ids:
                module_status_unlink_list = []
                self.is_all_installed = True
                if rec.status =='uninstalled' and rec.module_id.id not in self.saas_module_ids.ids:
                    module_status_unlink_list.append(rec.id)
                if rec.status =='uninstalled' and rec.module_id.id in self.saas_module_ids.ids:
                    self.is_all_installed = False
                self.env['saas.module.status'].browse(module_status_unlink_list).unlink()
        return res

    def _admin_login_for(self, db_name):
        """
        The actual login of user id 2 in `db_name`, read straight from that database.

        Not `container_user`: set_user_data() renames user id 2 to the customer's own
        email on every real client, so container_user only ever matches a template (and
        only one provisioned since the last credential rotation).
        """
        host_server, db_server = self.server_id.get_server_details()
        try:
            response = query.get_credentials(db_name, host_server=host_server, db_server=db_server)
        except Exception as e:
            _logger.error("Could not read admin login for %s: %r", db_name, e)
            return None
        if not response.get('status') or not response.get('result'):
            _logger.error("Could not read admin login for %s: %r", db_name, response.get('message'))
            return None
        return response['result'][0][0]

    def _reconcile_one_db(self, odoo_url, db_name, entitled, common_addons_path):
        """
        Bring ONE database in line with `entitled`: install what's missing, hide the
        custom modules it isn't entitled to, and report what is actually installed.

        Returns a dict, or None if the database couldn't be reached at all.
        """
        config_path = get_module_resource('odoo_saas_kit')

        # The login has to come from the database itself, not from container_user:
        # set_user_data() renames user id 2 to the customer's own email on every real
        # client, so container_user is not a valid login there. And the password has
        # to be tried against every known container password, because rotating
        # container_passwd does not re-key already-provisioned databases.
        login = self._admin_login_for(db_name)
        if not login:
            return None
        rpc, _passwd = saas_client_db.connect_admin(odoo_url, db_name, login, config_path)
        if not rpc:
            _logger.error("Could not connect to %s at %s to reconcile modules", db_name, odoo_url)
            return None

        installed_ok, missed = saas_client_db.install_modules(rpc, entitled)
        visibility = module_visibility.enforce_module_visibility(rpc, entitled, common_addons_path)
        states = {}
        try:
            states = saas_client_db.module_states(rpc, entitled)
        except Exception as e:
            _logger.warning("Could not re-read module states for %s: %r", db_name, e)

        return {
            'db': db_name,
            'installed_ok': installed_ok,
            'missed': missed,
            'states': states,
            'visibility': visibility,
        }

    def reconcile_modules(self):
        """
        Maintenance action: make this plan's DB template and every one of its running
        clients match the plan's module list, and re-assert module entitlement on all
        of them.

        This exists because provisioning-time enforcement only helps things created
        *after* the fix. Templates and clients built earlier can be arbitrarily out of
        step - notably, both live templates were found with none of their plan's
        modules installed while every saas.module.status row claimed 'installed',
        because create_db_template()'s "already exists" path installed nothing and
        reported success anyway.

        For each target it: installs any entitled module that isn't installed, removes
        the non-entitled custom modules from its Apps, and then rewrites the
        saas.module.status bookkeeping from what the database ACTUALLY reports - so the
        bookkeeping stops being able to lie in the same direction twice.
        """
        self.ensure_one()
        config_path = get_module_resource('odoo_saas_kit')
        common_addons_path = auto_login_token.read_secret(
            config_path, "common_addons_v%s" % SAAS_ODOO_VERSION.split('.', 1)[0])

        # saas_kit_auto_login is structurally required (it serves the Login button's
        # route), so it is always entitled regardless of the plan's module list.
        entitled = [m.technical_name for m in self.saas_module_ids if m.technical_name]
        if 'saas_kit_auto_login' not in entitled:
            entitled.append('saas_kit_auto_login')

        results = []

        if self.db_template:
            template_port = install_module.get_port(
                config_path + "/models/lib/saas.conf", SAAS_ODOO_VERSION.split('.', 1)[0])
            res = self._reconcile_one_db(
                "http://localhost:%s" % template_port, self.db_template,
                entitled, common_addons_path)
            if res:
                res['kind'] = 'template'
                results.append(res)
                self._write_status_from_states(res['states'], plan_scope=True)
                try:
                    self.restart_db_template()
                except Exception as e:
                    _logger.error("Reconciled template %s but could not restart its "
                                  "container: %r", self.db_template, e)

        clients = self.env['saas.client'].search([
            ('saas_contract_id.plan_id', '=', self.id),
            ('state', '=', 'started'),
        ])
        for client in clients:
            res = self._reconcile_one_db(
                "http://localhost:%s" % client.containter_port, client.database_name,
                entitled, common_addons_path)
            if not res:
                continue
            res['kind'] = 'client'
            results.append(res)
            self._write_status_from_states(res['states'], client_id=client.id)
            try:
                client.restart_client()
            except Exception as e:
                _logger.error("Reconciled client %s but could not restart its "
                              "container: %r", client.database_name, e)

        body = ["<b>Module reconciliation</b>"]
        for r in results:
            hidden = r['visibility'].get('hidden') or r['visibility'].get('flagged') or []
            body.append(
                "%s <b>%s</b>: installed=%s, missed=%s, hidden from Apps=%s"
                % (r['kind'], r['db'],
                   sorted(k for k, v in r['states'].items() if v in ('installed', 'to upgrade')),
                   r['missed'] or 'none', len(hidden)))
        if not results:
            body.append("Nothing could be reached - see the server log.")
        self.message_post(body="<br/>".join(body))

    def _write_status_from_states(self, states, client_id=None, plan_scope=False):
        """
        Rewrite saas.module.status rows from what the target database actually
        reports, instead of from an assumption that the install worked.
        """
        domain = [('plan_id', '=', self.id)] if plan_scope else [('client_id', '=', client_id)]
        for row in self.env['saas.module.status'].search(domain):
            actual = states.get(row.technical_name)
            if actual is None:
                continue
            row.status = 'installed' if actual in ('installed', 'to upgrade') else 'uninstalled'

    def sync_contract_modules(self):
        """
        Push this plan's module list onto its live contracts.

        A contract takes a one-off SNAPSHOT of the plan's modules when it is created
        (`saas_module_ids = [(6, 0, plan.saas_module_ids.ids)]`, in both
        contract_creation_wizard and sale.py) and nothing ever refreshed it again -
        not editing the plan, and not even the "Add Module" wizard, which updated the
        plan and the per-client status rows but never the contract in between. So a
        contract's module list silently drifted away from its plan's, and since
        saas.client.attach_modules() builds its status rows from the CONTRACT, a
        module added to a plan would never reach a client created afterwards either.

        Cancelled contracts are left alone - their snapshot is a historical record.
        """
        for obj in self:
            contracts = self.env['saas.contract'].search([
                ('plan_id', '=', obj.id),
                ('state', 'not in', ('cancel',)),
            ])
            if contracts:
                contracts.write({'saas_module_ids': [(6, 0, obj.saas_module_ids.ids)]})
                _logger.info("Synced plan %s module list onto %s contract(s)",
                             obj.id, len(contracts))

    def drop_template(self):
        for obj in self:
            linked_plans=self.env['saas.plan'].search([('id','!=',obj.id),('db_template','=',obj.db_template)])
            try:
                if not len(linked_plans):
                    if obj.state == 'cancel':
                        host_server, db_server = obj.server_id.get_server_details()
                        self.print_logs('info', 'calling client script', '482')                    
                        response = client.main_plan(obj.db_template, host_server, get_module_resource('odoo_saas_kit'))
                        if not response['db_drop']:
                            raise UserError("ERROR: Couldn't Drop Database. Please Try Again Later.\n\nOperation\tStatus\n\nDrop database: \t{}\n".format(response['db_drop']))
                        else:
                            obj.db_dropped=True
                    else:
                        raise UserError("Please cancel the Plan first before drop the db.")
                else:
                    for plan in linked_plans:
                        if plan.state != 'cancel':
                            raise UserError("Cannot Drop Database: Active plan(s) are linked to this DB")
                    if obj.state != 'cancel':
                        raise UserError("Cannot Drop Database: Active plan(s) are linked to this DB")
                    self.print_logs('info', 'calling client script', '496')                    
                    response = client.main_plan(obj.db_template, host_server, get_module_resource('odoo_saas_kit'))
                    if not response['db_drop']:
                        raise UserError("ERROR: Couldn't Drop Database. Please Try Again Later.\n\nOperation\tStatus\n\nDrop database: \t{}\n".format(response['db_drop']))
                    else:
                        obj.db_dropped=True
                        for plan in linked_plans:
                            plan.db_dropped=True
            except Exception as e:
                raise UserError(e)
