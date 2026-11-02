# Remote Workspace Implementation Plan

> **For agentic workers:** Use subagent-driven-development. Implementers do not spawn subagents. Main controller owns review and integration.

**Goal:** Connect, pin and inspect a remote task without memorizing commands or paths, through the same API for people and agents.
**Architecture:** additive connection-trust/workload models, shared bounded SSH/SFTP service, Vue workspace, stdlib CLI.
**Tech Stack:** existing Python3.11/FastAPI/SQLAlchemy/Paramiko and Vue3/Vite; no new runtime dependency without concrete need.
**Spec:** [design-remote-workspace.md](design-remote-workspace.md)

## Global Constraints

- Work only on feat/remote-workspace in /Users/meilingluo/Developer/Projects/PanelLab/worktrees/reliability. Preserve previous branch and main. Small commits, stage owned files only. No push/merge/deploy or real-server/tailnet operations.
- Use only isolated fixtures. Never read personal SSH config/keys, never use host SSH agent implicitly. Password and explicit Tailscale none authentication only. Host keys must be explicitly pinned before credentials or remote work.
- All endpoints enforce ownership; production shared-machine device discovery requires operator. No new arbitrary remote execution endpoint. Workload mutations modify metadata only. UI and CLI use same API.
- Remote reads bounded: SSH connect/auth/channel deadline10s, collector20s/output1MiB, browse500entries, text/log64KiB, download16MiB. No symlink/special-file traversal in workspace file reads. Return clear errors, no credential/raw exception leaks.
- Existing tables unchanged; additive models only. No crash/production readiness overclaim. Missing evidence is unknown; running process is not successful task; reported run records labeled reported.
- Python tests use /Users/meilingluo/Developer/Projects/PanelLab/.venv311/bin/python. Focused RED→GREEN then one relevant full suite. Root owns docs and integration script. Do not edit root-owned docs.

## Task 1: trusted shared remote connection and bounded observation

**Files:** new backend/models/remote_connection.py, backend/schemas/remote.py, backend/services/remote_connection.py, backend/services/remote_probe.py, backend/routers/remote.py, backend/tests/test_remote_connection.py, backend/tests/test_remote_probe.py; modify backend/models/__init__.py, backend/main.py, backend/routers/terminal.py, frontend/src/composables/useTerminalStore.js minimally for ticket handshake, tests affected by terminal protocol. May add focused services modules to keep files readable.

**Interfaces:** `/api/servers/{id}/connection/probe` POST returns host,port,key_type,fingerprint,token,expires_at; token purpose remote-trust TTL300 bound owner/id/host/port/username/key and auth mode (password default or tailscale explicit request). POST `/connection/trust` {token} saves pin and auth mode after rechecking current server target, returns fingerprint,auth_mode. GET `/connection` returns configured/pinned summary no secrets. POST `/connection/check` authenticates and returns ok,home,observed_at. Unknown/changed key returns409 detail {code,message}; auth/connect failure503 sanitized. Probe does not authenticate. Shared service exposes `open_remote(db,user,server_id)` context manager yielding connected Paramiko SSHClient, plus safe SFTP helpers. Document exact helpers in report.

`GET /api/servers/{id}/snapshot`: observed_at, system {cpu,memory,network,gpu}, processes [{pid,identity,command,cwd?,cpu?,memory_bytes?}], capabilities/warnings. Linux Python3 fixed probe script, JSON output, no psutil requirement; record process identity boot_id:start_ticks. Bounded process list200, truncate command to512. CPU fields may be unavailable if no interval sample; never invented. GET `/api/servers/{id}/files?path=` lists normalized cwd, parent, entries[{name,path,type,size,mtime}],truncated, default SSH home, no file contents. Refuse symlinks for navigation; list them as symlink disabled.

`POST /api/servers/{id}/terminal-ticket` {cwd?:absolute path} returns ticket,expires_at,TTL60. Existing WS accepts ticket only, consumed once; validate/recheck ownership and trusted connection, ticket not reusable; resolve optional directory safely using sameSFTP before shell start, cd via shlex.quote, no newline/NUL. Update terminal store to obtain ticket with bearer auth before WS; no longJWT inquery. Existing unpinned servers show cleartrustrequired error. Single-process ticket store explicit limitation, use bounded lifetime cleanup.

Optional `GET /api/remote/tailscale` operator-protected production reads fixed `tailscale status --json` with5s timeout,512KiB cap; returns available,reason,devices[{name,dns_name,addresses,online}], bounded200; no login/policy writes or SSH auto-trust. Handle absentbinary/nonzero/malformed JSON. Do not add command configuration from client.

- [ ] RED tests: unowned/notfound probes; no password sent before pin; invalid/expired/changedendpoint trusttoken; changedhostkey prevents auth; safe filenames/path reads; collector bounded timeout/output and noGPU; PID identity parsing; terminal ticketcrossuser/replay/expiry; tailscale unavailable. Use fakes for network boundary and real fixture text for parsers, no source-string assertions.
- [ ] Implement shared connection and fixed probe with structured exception mapping; never AutoAddPolicy. Keep protocol schema precise for next tasks.
- [ ] GREEN focused tests, then backend suite once. Small commits: connectiontrust, observation/files, ticketintegration. Report exact JSON/examples/helpers and limitations.

```python
# Representative acceptance; flesh fixtures to execute behavior.
assert unowned_client.post(f'/api/servers/{sid}/connection/probe').status_code == 404
assert observed_process['identity'] != reused_pid_process['identity']
assert secret_password not in error_response.text
```

## Task 2: durable workloads, bounded files/logs, reported runs and CLI

**Files:** new backend/models/workload.py, backend/schemas/workloads.py, backend/services/workloads.py, backend/routers/workloads.py, backend/tests/test_workloads.py, scripts/panellab.py, backend/tests/test_workspace_cli.py; modify backend/models/__init__.py and backend/main.py. Consume Task1 helpers and exact interfaces in task1report.

**Interfaces:** `/api/workloads` GET {items}, POST; `/api/workloads/{id}` GET/PATCH/DELETE. Fields name<=100,kind training|website|scheduled,server_id,root_path absolute,log_path optionalabsolute withinroot,pid+process_identity paired optional,website_url optionalhttp(s) no userinfo,schedule_note<=200,notes<=2000. Owner fromJWT, crossuser404, unknownextra forbid. Validate remote paths/selectedprocess on creation/update that changes binding; changed PID identity rejected409. Metadata updates unrelated tobinding do not requireliveconnection. CRUD no remote mutations. Deletion cascades own reportedruns viaORM; serverdeletion withworkloads must refuse409 instead ofdangling data (modify routers/servers.py for thisdependency only).

`GET /{id}/observation` returns workload_id,observed_at,process_status running|exited_or_missing|unbound|unknown,process?,system,log {path,available,modified_at,size},warnings; unreachable errors503 and no fakefreshdata. PIDidentity mismatch=exited_or_missing +warning. `GET /{id}/files?path=` confinedtoroot; `GET /{id}/text?path=` bounded64KiB tail default, response path,text,truncated,size,modified_at; `GET /{id}/download?path=` <=16MiB attachment (safeRFC5987filename), require regularfile, no symlink ancestors. ResolvewithSFTP normalize/lstat and check actualresolvedroot containment; filechangingduringread bounded andreported, no unbounded streaming. Paths traversal/outside403, missing404, oversized413. Logtail uses same API withworkloadlogpath. Frontend choosers useTask1 serverfiles forinitialroot/logselection.

For binding/observation, do not infer absence from capped200processdiscovery. Extend fixed collector/helper with optional validated positivepid targetedlookup and optional bounded query (max128chars) for processpicker; extend server snapshot queryaccordingly. Targetlookup differentiates present/missing/unavailable, and incomplete/deniedcollection yields unknown, neverexited. Use shellquotedJSONarguments to fixedscript, no userprovidedcode. Boundproc enumeration/scan andoutputs underexistingdeadlines. Task2 maymodify services/remote_probe.py,routers/remote.py,tests/test_remote_probe.py for this integration. Addtests boundPIDbeyonddiscoverycap andunavailable/permissioncase. Expose /proc state; matchingidentity withZ/X means exited_or_missing, T/t meansunknown withwarning, nothealthy-running. Coverthese fixturestates. Report exactnewfields/APIforTask3.

`GET /{id}/runs?before_id=&limit=` cursor limit1..50 returnsitems. POST /runs fields started_at timezoneaware,finished_at optional,status running|succeeded|failed,exit_code optional,summary<=2000,artifact_path optionalwithinroot; source reported and reported_by user ID setserver-side. Reject contradictory times/status; never infer successfromexitcode. These records are user/agentreports, not scheduler execution; no croninstallation.

CLI `python scripts/panellab.py --help`: stdliburllib+argparse; --url defaulthttp://127.0.0.1:8000; bearer from PANEL_TOKEN or --token-file (no tokencommandlineflag); no logsecrets. Commands servers, tasks list/show/create/update/remove, observe ID, files ID [PATH], logs ID, download ID PATH --output LOCAL, runs ID, report ID. Creation/update/report accepts --json path or '-'stdin; JSON outputsuccess and errors, nonzeroexit onerror; download defaultrefuse overwrite, remove requires --yes; all mutations hit sameAPI (no SSHinCLI). HTTP nonloopback refused unless explicit --allow-insecure; httpsnormal. Redact sensitive errorcontent? bounded safeerrorJSON only; no automatic retries mutations. Documenthelp examples inreport for UI.

- [ ] RED meaningful tests: ownerisolation; bindingchecks; pathescapes/symlink/specialfiles/spacequotes; boundedtail/download; lostconnection; PIDreuse; reportedrunstatus/times/pagination; serverdeleteguard; CLI fakeHTTPserver verifies authorization,URLs,JSONerrors,nonzero,downloadnooverwrite,tokenabsent and insecureURLrejection.
- [ ] Implement cohesive modules andCLI; methods ownership-first before network.
- [ ] GREEN focused thenbackendfullsuite once. Commit workloads,files/runs,CLI separately. Report exactcontracts, outputexamples andtestevidence.

```python
assert other_client.get(f'/api/workloads/{task_id}/observation').status_code == 404
assert client.get(f'/api/workloads/{task_id}/text', params={'path':'/outside/key'}).status_code == 403
assert reported['source'] == 'reported'
```

## Task 3: task-first workspace UI

**Files:** new frontend/src/views/dashboard/Workspaces.vue; frontend/src/components/workspaces/* focused components; frontend/src/composables/useWorkspaceApi.js; frontend/tests/workspaces.test.js; modify frontend/src/router/index.js, frontend/src/views/DashboardLayout.vue, frontend/src/views/Login.vue redirect default only, frontend/src/views/dashboard/Terminal.vue and frontend/src/composables/useTerminalStore.js for optionalcwd/serverlink (preserveTask1tickets), backend/routers/terminal.py for explicit readiness frame only. Consume task1/2reports exactAPI.

Entry /workspaces is mainroute afterlogin andsidebarfirst. Reuse existing serverCRUD foraddserver namehostportuserpassword, trust flow shows target/fingerprint and explicitconfirmation with text reminding externalverification; optionalTailscale candidates populatehost andchooseauthmode, noautotrust. Serverselection shows connectionstatus/retry withclearerrors. No secrets inURL/logs/storageexceptexistingauthJWT.

Taskcards/filterbyserver/kind; no fabricatedstatus. Create/edit form:name,kind,server, browse-root selector (homeinitial), chooseprocess ornone, chooselogfile optional,URL/schedulenote optional. Filepicker supportsbreadcrumb/up, spaces/Chinese, disables symlinks; no required path typing. Taskdetail tabs overview/log/files/reportedruns; observation freshness, CPU/memory/network/GPU whenavailable, processidentity/status; logs64KiB text andmanualrefresh plusoptionalpollingbounded nooverlap, entries openfolder/text/download. Action opens existing terminal at selectedserver/currentdirectory, no automaticcommands beyondsafe server-sidecd. Copy ID/CLIexample; errors do notdiscardloadeddata; clearly stale label. Reportedruns clearly labeled, simple addreport form optional use sameAPI. Delete onlyunregister metadata confirmation explicitly states remote process/filesunchanged.

Resolve Task1 minor: WSopen alone is not SSHready. Backend sends explicit ready controlframe onlyafterauthentication/shell/cwdsetup; store staysconnecting untilthatframe, handleserror/close withoutsuccessflash, subsequentterminaltext remainsraw. Addfocused storetest (existingterminal-ticket.test.js) foropen-vs-ready andfailedsetup; do notparsearbitraryremoteoutput asreadiness.

Network UI shows cumulative counters as cumulative, and may derive clearly labeled approximate B/s only from two successful samples for the same server/interface within30s and nonnegative counters; reset onserverchange/failure/counterreset. Addbehavior calculationtest, neverpretend totalbytes is rate.

Use APIservice centralizedBearer/errorJSON parsing; abort/sequenceguards forswitches, no stale responses apply toanother task/server, disableduplicate submits. Responsive390CSSpx, semanticlabels, keyboard. Keep filesfocused, no giant allinonecomponent. No screenshots hardcoded, noHTMLinsertion oflogs.

- [ ] RED Vitest actualcomponentinteractions: connecttrustrequiresclick; creationpicker pathchoice; processbinding; taskselection stale response; logfailure preservesdata/stale; downloadrequest; reportedlabel; terminaltarget/cwd; deleteconfirmation.
- [ ] Implement component tree and defaultnavigation usingreal APIs.
- [ ] npm test andnpmrunbuild, meaningfultests notsnapshots. Twoormoresmallcommits. Report APIassumptions andtestevidence; root handlesrealbrowservalidation.

```javascript
// Representative behavior, not source inspection.
await wrapper.get('[data-testid="open-root-picker"]').trigger('click')
// Select fixture directory; assert create payload uses selected absolute path.
expect(createRequest.root_path).toBe('/home/lab/experiments/run A')
```

## Task 4: independent end-to-end verification and delivery (controller)

- [ ] Run isolated local SSH/SFTP server with knownhostkey and fixtureprocess/files; exercise trust,mismatch,discovery,pinning,logtail,download,CLIshareddata; preserve originalserver state.
- [ ] Browser actual create/selecttask,log/files/download andnarrowviewport; screenshot. Backendandfrontendfinalchecks afterlastcodechange.
- [ ] Independentwholebranchreview; onefixwave ifneeded; updatedREADME/docsindex/manual anddateddeliveryreport distinguish implemented/verified/deferred. KeepTailscalerealnetworkuntested explicit.
- [ ] Clean gitstatus, document localbranch relationship and unchangedmain. Stop onlyselfcreatedtestservices. Keep worktree/reviewevidence; no unapprovedremotechanges.
