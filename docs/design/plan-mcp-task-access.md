# MCP task access implementation plan

> For agentic workers: use subagent-driven-development; sequential owned-file tasks, no implementer subagents.

Goal: a configured unfamiliar MCP client discovers and reads the same task context as humans.
Architecture: optional stdio adapter over existing authenticated HTTP API; official Python SDK, separate environment; no backend/GUI behavior change.
Spec: [design-mcp-task-access.md](design-mcp-task-access.md). Basec7f76b7, branchfeat/mcp-task-access.

## Global Constraints

- Read personal AGENTS. Keep main and earlier branches/assets, no real servers/Agent config changes, no remote writes, credentials in outputs or extra ports. Controller owns rootdocs/roadmap/integration fixture.
- Python>=3.10, optional SDKmcp==2.3.0 in scripts/requirements-mcp.txt; do not add SDK to backendrequirements. MCP env /Users/meilingluo/Developer/Projects/PanelLab/.venv-mcp/bin/python, backendenv /Users/meilingluo/Developer/Projects/PanelLab/.venv311/bin/python.
- Preserve CLI standardlibrary behavior, API ownership/SSH/path/size constraints. Tests meaningful RED→GREEN; no repeat whole suites after unchangedcode. Reports in current plan SDDdirectory, small ownedfilecommits.

## Task 1: readonly stdio tools and protocol tests

Files create scripts/panellab_mcp.py, scripts/panellab_mcp_server.py, scripts/requirements-mcp.txt, scripts/tests/test_mcp_server.py (helperfixturefileifneeded). Do not edit existingCLI/backend/frontend/rootdocs/integration script.

Consumes panellab.API(url,token).request(method,path,body=None,query=None,binary=False), base_url(value,allow_insecure), token_from(argnamespace), ClientError.error. Inspect actualAPIpayloads and installedSDK2.3.0 source instead of guessing olderSDK methods.
Produces build_server(api) with official MCPServer (or suitableSDKlowlevel) and main(argv=None) stdio entry; main supports --url defaultloopback8000, --token-file/PANEL_TOKEN, --allow-insecure and --help. Separate parser/config fromSDKimport so task2 can extend without requiringSDK forhelp. Report exact interfaces forTask2.

Tools exactlylist_servers,list_tasks,get_task,observe_task,read_task_log,list_task_files,read_task_text,list_reported_runs. All readOnly/destructivefalse annotations; descriptions/parameter field guidance and serverinstructions per spec. list_servers allowlist id/name/host/port/username. list_tasks query max128/namecaseinsensitivesubstring, kind enumoptional, limit20 in1..100, offset0>=0; outputs items,total,has_more. APIlistrootitems; getbyID rootobject. IDs strictpositiveintegers, paths nonempty<=4096, runs limit20 1..50 cursorpositive. list_task_files optionalpath; read_task_text requiredpath; read_task_log tasklogbinding orlog_unbound. Allcalls GETonly. Preserve structuredAPIevidence. No commands/auth/HTTPpath toolinputs.

MapClientError to explicit MCP toolerrors with safe structurederror ifsupported, fixed recoveryhints, no body/token/rawexception leakage; invalidupstreamschema sanitized. Unexpectederrors generic. API bounded30s/1MiB; remote reads64KiB unchanged. Ensure synchronousHTTP doesn't block stdio protocol thread (SDKthreadhandleroranyio offload); no new backgroundpolls.

- [x] Write tests before implementation: SDK discover exact8 tool schemas and descriptions; tools with loopbackHTTP fixture captureonlyGET/rightpaths/args, listfilterpagination; get/observe preservecontext/time; logunbound and paths/nonowners; malformedresponses/401/404/409/connectionfail/oversize showisError notsuccess; no credential/errorbody leak; strictIDs/bools/invalidpaths/pagebounds rejectedbeforeHTTP; remoteinstructiontext returnedasdata.
- [x] Implement tools/entry, genuine stdio subprocess testfromnonrepocwd, cleanstdout handshake/toolcall and failurestartup stderr, no SDKCLIdependency.
- [x] Run MCP tests in isolatedMCPenv, existingCLItests inbackendenv (no fullfrontendneeded). Commit2smallstepsifseparable; report task-1-report.md exacttoolfields/output/SDKinterfaces/RED/GREEN/limitations. Reviewerrootdispatch.

## Task 2: onboarding configuration and CI

Files modify scripts/panellab_mcp.py, create scripts/tests/test_mcp_config.py, docs/user/mcp-access.md; targeted .github/workflows/ci.yml MCPjob. Do not edit servertools/rootdocs/otherCLI/backend/frontend.
Consumes Task1entry parser/main/build_server; readtask1report first. Implement --print-config and --check distinctmutuallyexclusive modes. --print-config requires tokenfile, resolves currentPython/script/tokenfile absolutepaths, returns mcpServers.panellab command/args JSON forstdio using --url andoptional--allow-insecure. Doesnotreadtokenfile,emitsecret/env,writeclientconfig orcontactAPI; --help/configworkwithoutSDK. RejectinvalidURLs/argumentswithredactedstderr; preserve existingserve.
--check validatesconfiguredAPI withGETservers, returns onlyok/server_count; errors fixedsafeJSON orstderr/nonzero, no serverdetailsorcredentials, noSDKneeded. SDKmissingservemode reportsinstallcommandandnonzero. Unicode/spacepaths andnonrepocwd supported.
Guide describes isolatedenvinstall requirements, preexistingAPI/auth/tokenfileconfiguration and expiry; configisgeneric clienthostmaydiffer; actualread-onlytoolset isnotrestrictedBearercredential, do notclaimdelegation; noautoMCPclientinstall. Include exactcommands and simple discovery→list→observe→logs workflow, unbound/unknown/reported boundaries; tooltable. Noactualtokenfixturesinexample.
CI add independentMCPjob Python3.11 installscriptsrequirements +pytest run scripts/tests, keeping existingbackend/frontendjobsunchanged andmain/PRtriggersunchanged.
- [x] RED subprocess tests configurationrequiresfile/noSDK/noHTTP/no secrets (evenenv set), absolutepaths/cwd/URLvalidation; checksuccess/authfail/unknownfailure; servecompatibility.
- [x] Implement onboarding/guide/CI and run combinedMCPtests once plusCLIregression asaffected; reporttask-2-report.md, smallcommit(s), independenttaskreview.

## Task 3: controller integration and handoff

- [x] Extend scripts/verify_workspace_lab.py with optional --mcp-python absoluteinterpreter; afterfixturetests invoke tracked scripts/verify_mcp_lab.py (rootowned) usingSDKclientstdio todiscover/read samefixturetask, processidentity/context, logs/files/text/runs; bootstrap token through environment, private disposable tokenfile for generatedconfig, no credentialoutput. Existingwithoutoptionworks. ActualLinux/MySQLfixturePASS.
- [x] Root currentREADME/docsindex/guide links/newdatedreport, roadmapstatus; evidence fromtaskreports andindependentreviews plusrealprotocol, noLLM/nativehostclaim.
- [x] Finalwholebranchreview once; oneconsolidatedfixwave/scopedreviewifneeded. Stopownservices, preserveassets. Cleangit/smallcommits/pushnewbranch, verifyremoteSHA/main/previousbranchunchanged.
