# Server command log — session 2026-08-23 (audit)

| # | Command | Why |
|---|---------|-----|
| 1 | ssh root@... 'hostname' | Confirm SSH key auth works |
| 2 | docker ps -a / docker images | Container + image inventory |
| 3 | git log/status in /opt/odoo19/custom-addons; ls common-addons_v19, Odoo-SAAS-Data | Server clone drift + deployed addons |
| 4 | git diff (py files, saas.conf masked) | See the hand-edits made directly on server |
| 5 | ls/cat nginx sites-enabled, conf.d, docker_vhosts, client-ports.conf; nginx -t | Nginx routing + wildcard map audit |
| 6 | grep include /etc/nginx/nginx.conf; cat wildcard-clients vhost | Determine vhost precedence |
| 7 | certbot certificates (apt + venv) | SSL cert/expiry audit |
| 8 | cat docker_vhosts/{demo,mhperfumers}.conf | Confirm per-client vhosts are HTTP-only |
| 9 | psql -l | Database inventory |
| 10 | sed/grep manager odoo.conf (masked); grep odoo-portal nginx | Identify manager DB (dbfilter ^test$) + main-site TLS |
| 11 | psql: saas_plan/saas_contract/saas_client/saas_module/relations/status | Read actual saas_kit records |
| 12 | check_modules.sh (psql ir_module_module per DB) | Verify whether plan modules are really installed |
| 13 | stat mtimes of edited files; docker inspect StartedAt | Is the hand-patched code actually loaded? |
| 14 | cat hf_basic_b2b_theme + saas_kit_auto_login manifests | Explain "extra" modules (transitive deps) |
| 15 | docker exec template_cont cat odoo.conf (masked) + sha256 fingerprints | Test "secret mismatch" theory for auto-login |
| 16 | test_autologin.py (4 targets) | End-to-end auto-login test: clients OK, templates fail |
| 17 | getent hosts; openssl s_client -servername | Diagnose template-host TLS failure |
| 18 | test_tls_underscore.py | PROVE underscore breaks wildcard cert match |
| 19 | test_tmpl_route.py | Isolate template auto-login from TLS (fails over plain HTTP too) |
| 20 | docker exec template_cont tail odoo-server.log | Find "Rejected invalid/expired token" + cron KeyError |
| 21 | fingerprint_secrets.sh | Prove saas.conf secrets == container admin_passwd (ruled out mismatch) |
| 22 | cat deployed saas_kit_auto_login/tools.py | Confirm deployed verifier == local |
| 23 | verify_inside.sh | verify_token() SUCCEEDS in-container -> narrows to running worker's config |
| 24 | docker inspect Cmd/Env; verify exact rejected token host-side | Confirm ODOO_RC + token genuinely valid |
| 25 | docker restart odoo19_template_cont; retest | PROVE the in-memory admin_passwd hash mutation is the cause |
| 26 | cat nginx-deploy-dispatch.sh / nginx-client-map-update.sh | Learn allowed remote ops; found the map regex rejects underscores |
| 27 | probe_erppeek.py (read-only, in odoo19 container) | Establish real erppeek API shapes before coding against them |
| 28 | cp client-ports.conf + docker_vhosts/*.conf to /root backups | Safety net before nginx changes |
| 29 | rm docker_vhosts/{demo,mhperfumers}.conf; nginx-client-map-update.sh db19-templates 8819 | SSL FIX: stop the exact-match :80 vhosts shadowing the wildcard 301; register hyphenated template host |
| 30 | curl HTTP/HTTPS for all 4 hosts | VERIFY SSL fix: all now 301->https with valid certs |
| 31 | cp template odoo.conf backup; append dbfilter = ^template_ | Confine template container to template DBs only |
| 32 | docker restart odoo19_template_cont; test_tmpl_route.py; curl ?db=test | VERIFY dbfilter: templates still work, manager DB now unreachable |
| 33 | docker logs --since 3m | grep test | VERIFY manager-DB cron hijacking stopped (0 hits) |
| 34 | psql DELETE FROM saas_module_status WHERE plan_id IS NULL AND client_id IS NULL | Clean 131 orphan bookkeeping rows (blocked module deletion) |
| 35 | mkdir /root/stage; scp candidate lib files; docker cp into odoo19:/tmp/saaslib | Stage NEW code to test it against a real DB before deploying |
| 36 | docker exec odoo19 python3 /tmp/drive_install.py template_..._tid_15 <modules> | TEST new install_modules + visibility enforcement on plan 15's empty template |
| 37 | docker restart template_cont; psql ir_module_module for tid_15 | VERIFY 3 plan modules installed, 9 non-entitled custom modules removed, deps preserved |
| 38 | test_autologin.py via https://db19-templates... (both templates) | VERIFY template auto-login end-to-end over real HTTPS |
| 39 | prove_hash_mutation.py (restart, login, db.list_countries, login again) | PROVE root cause: one master-password verification breaks auto-login |
| 40 | git fetch/log in server clone | Confirm the pushed commits arrived |
| 41 | cp saas.conf + odoo_saas_kit/odoo.conf to /root backups; sha256 | Protect live credentials before touching git |
| 42 | git checkout -- (3 hand-edited .py files) | Discard server hand-edits now superseded by the committed fix |
| 43 | sha256 compare live vs origin/main vs HEAD saas.conf | PROVED the hand-edit was just ce99f72 applied early - no credential divergence |
| 44 | git checkout -- saas.conf; git pull --ff-only | Deploy the fixes cleanly; verified saas.conf hash unchanged after |
| 45 | cp -a saas_kit_auto_login -> common-addons_v19; chmod -R a+rX; rm __pycache__ | Deploy the auto-login fix (this dir is not a git clone) |
| 46 | docker restart odoo19 odoo19_template_cont | Load the new odoo_saas_kit + addon code |
| 47 | prove_hash_mutation.py | VERIFY auto-login now SURVIVES a master-password verification (was FAILS) |
| 48 | docker restart both clients; prove_any.py x2 | VERIFY client auto-login durable |
| 49 | odoo shell: button_immediate_upgrade(odoo_saas_kit) | Apply the ondelete=cascade schema change |
| 50 | psql information_schema FK query | VERIFY client_id + plan_id are now CASCADE |
| 51 | reconcile_now.py (first attempt) | Reconcile templates/clients - exposed the credential problem |
| 52 | probe_creds.py | DIAGNOSE: template 14 + both clients only accept the PRE-rotation password; clients' login is the customer email |
| 53 | add_legacy_passwd.py | Record container_passwd_legacy in saas.conf (value never printed) |
| 54 | reconcile_now2.py | Reconcile all templates + clients: installed missing plan modules, hid non-entitled, rewrote bookkeeping from reality |
| 55 | docker restart template_cont; psql ir_module_module sweep | VERIFY entitled installed everywhere, non-entitled absent |
| 56 | e2e_test.py (odoo shell) | E2E part 1: new Product -> Plan -> Create DB Template through real product code |
| 57 | psql checks on template_audit_e2e_plan_tid_17 | VERIFY 3 entitled installed, 5 non-entitled have NO rows, 61 modules total |
| 58 | prove_any.py on new template | VERIFY auto-login durable on a freshly created template |
| 59 | e2e_client.py (odoo shell) | E2E part 2: Contract -> create_saas_client() = container + DB + nginx + modules |
| 60 | docker ps / ls docker_vhosts / cat client-ports.conf / psql | VERIFY container up, NO shadowing vhost written, map entry added, modules correct |
| 61 | curl http+https audite2e | VERIFY new client 301->HTTPS with a valid cert |
| 62 | e2e_addmodule.py (odoo shell) | E2E part 3: Add Module wizard - contract synced, but install failed |
| 63 | retry_install.py with --log-level=info | DIAGNOSE: update_list() -> IndexError in Odoo's call_kw (erppeek sends no ids arg) |
| 64 | docker cp fixed saas_client_db; python3 install test | VERIFY update_list fix restores a hidden module's row and installs it |
| 65 | connect_admin test against audite2e | VERIFY DB-derived login + candidate passwords work; module installed into client |
| 66 | reconcile_now2.py (final) | Normalise all 3 plans, 3 templates, 3 clients |
| 67 | docker restart template + audite2e; psql saas_module_status dump | VERIFY bookkeeping matches reality, 0 orphans |
| 68 | prove_any.py x6 (all templates + all clients) | FINAL: auto-login durable everywhere |
