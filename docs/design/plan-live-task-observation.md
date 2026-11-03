# Live task observation implementation plan

Spec: [design-live-task-observation.md](design-live-task-observation.md). Base5e7fe0c; branchfeat/live-task-observation; reuse /Users/meilingluo/Developer/Projects/PanelLab/worktrees/reliability. User authorizes autonomous scoped iteration; root owns review/docs/integration. No implementer subagents.

## Global Constraints

- Preserve main,feat/remote-workspace,personalassets and rootdocs. Small owned-file commits only; controller pushes newbranch afterreview, no merge/deploy/realserver/tailnet operations.
- Retain explicit hostkey trust,ownership-first checks,bounded SSH20scollector/1MiBoutput,64KiBtext/16MiBdownload, no arbitrary execution,newdependencies,DBschema or remote writes.
- Evidence missing is unknown, serverresources nottaskresources, processrunning notsuccess. Preserve same-ID binding invalidation, delayedresponse/terminal/download isolation.
- Python /Users/meilingluo/Developer/Projects/PanelLab/.venv311/bin/python. MeaningfulRED→GREEN; relevant fullsuite once aftercode stable; reviewerdoesnotrerun samechecks. Root owns browser/integration.

## Task 1: additive sampling evidence and observation context

Files backend/services/remote_probe.py,backend/services/workloads.py,backend/tests/test_remote_probe.py,backend/tests/test_workloads.py. Addfocusedhelper/testfileonlyifneeded. Do noteditrootdocs/scripts/frontend.

Collector outputs top-level sampling={boot_id:str|null,monotonic_seconds:float|null}; propagate through parse_snapshot and workloadobservation. Capture remote monotonic time nearCPU/networkreads, not backend receipt timestamp; never extra remote sleep. CPU remainspercent:null with existingload_average, addticks dict user,nice,system,idle,iowait,irq,softirq,steal integers fromfirst8 /proc/stat CPUfields; ignoreguest duplicates. Validate values safe0..2**53-1 (boolnotnumber),finite time>=0, boundedbootid. Unavailable/malformednewfields degrade to null/unknown withwarningswithout losingvalidprocess/memory; supportoldfixturepayloadmissingnewfields. IsolateCPUload/counters/memoryreadfailure asneeded. Networkaddifindex from bounded sysclassnet interfaceindex; missing/invalidnull, nointerface pathtraversal fromnames. Preservebounds/nofakepercent/rate.

Observationadds context={workload:{id,server_id,root_path,log_path,pid,process_identity},server:{id,host,port,username}}, gatheredfromsameownedDBobjects usedforread,withoutsecrets. Nonownersstill404beforeSSH. ExistingAPI/CLIremaincompatible; observationreturns system+sampling+context. Tests realfixedcollectorscriptwithprocfixtures CPUfieldorder/guestignored,memoryavailableifCPUbad,ifindex,monotonic/boot,malformedsample; parserbackwardcompatibility/nullsafevalidation; workloadcontext ownership/credentialabsence andsameIDmodifiedbindings. Do notbroadenlegacyprocessparsercleanupunnecessarily.

- [ ] RED then implement bounded additivefields/context.
- [ ] Focusedtests thenfullbackendonce; smallcommits; report exactJSON/newfixtures/testresults in task-1-report.md.

## Task 2: live observation state machine and truthful interval metrics

Files focused newfrontend composables/tests, ObservationPanel.vue,TaskDetail.vue,Workspaces.vue ifrefresh-eventneeded, workspaces.css targetedstyle. Existing tests fixturesmayneednewcontext. Do nottouchbackend/rootdocs/dependencies.

ReadTask1reportexactJSON. AddpureCPU/network derivation using sampling bootid+remote monotonicseconds, first8ticks (excludeidle+iowaitfrombusy), networkname+ifindexidentity, safeintegers/nonnegative/finite, dt0.5..120inclusive; CPUanyfieldregressionorunsafeaggregatedeltainvalidCPU; networkRX/TXregressioninvalidinterface; new/recreatedinterfacemustprime. No usableboot/sample meansunknown. Firstsample/reboot/failure/pause/hidden/targetchange/gapinvalid→baseline/unknown; preservecumulativevalues asclearlyseparate. CPUlabelintervalpercentage,networkapproximateB/sandinterval; nohostsumdoublecount acrossinterfaces. No charts/history/newdependencies.

Taskoverviewonly followcheckbox defaultOFF; initialoneobservation unchanged. Userinteger2..60seconds default5; invalidshowmessage+pause(no clamp). Onependingrequestsharedmanual/auto; schedule nextonlyaftersettled; failedrequest retainsoldobservation/timestamp+staleerror, resetsratebaseline, delays min(60,interval*2**consecutiveFailures)seconds; successresetsfailures. Manualrefreshcanretryimmediatelywhenidleandvisible. Pause,hidden,nonoverviewtab,unmount cancelpending/cleartimers/invalidate generation; returnactivevisiblewithfollowON samplesimmediately. Do notautopollfilepreview/log/runs. Provideactiveprop fromTaskDetail(v-showobservationotherwisekeepsmounted). Existingwrongbindingguardsremain. Usablefollowcontrols keyboard/semanticlabels/mobilewrap; explainstale/paused/pendingbaseline honestly. Do notpersistcredential/personaldata oraddpreferencepersistence.

Contextcheck every observationagainstcurrenttask+server (normalizedidentityfields; name/notesexcluded). Missing/mismatchingcontext rejectsresponse asfreshdata, marksolddata stale, resetbaseline andauto-blockuntilbindingmetadatarefreshed; showrefresh-task-info action wiredthroughTaskDetail toWorkspaces load(). Do notmaskerror onnewrequeststartasfresh. Age-basedstale threshold max(30seconds,2*configuredinterval); paused/blocked/error states remainexplicit and ratesreset, so a valid60secondfollowintervaldoesnotlookunexpectedlyexpiredat30seconds. Refreshunchangedbinding afterfalse/oldserverresponse mustallowexplicitretry withoutunboundedautoloop; definehonestblockedstate andcover. Currentlistmetadataisnotlivepush.

Tests actualcomponents+faketimers/deferredpromises: firstsampleunknown secondvalidrates;serverboot/interfaces/reset/clockgap/malformedcounts; nooverlap;manualpending;hide/tab/pause/unmountclearandlateignore;backoff/recovery;intervalbounds;same-IDcontextmismatch/notfreshdata,refreshaction;preserveunchangedbindingstate. Adjustexistingfixturestruthfully,nosourcestringtests. RED→GREEN, fullnpmtest/buildonce stable;2smallcommits ifseparable. Reportfiles/tests/limits in task-2-report.md.

- [ ] RED behavior tests and implementation.
- [ ] Focusedtests,fullfrontend/build and report; independenttaskreview.

## Task 3: controller integration and delivery

- [ ] RealLinuxSSH/MySQLfixture sampleshaveboot/monotonic/ticks/ifindex/context; existingintegration stillpasses.
- [ ] Browser follow twicevalidrates,pause/resume, sameIDCLIrebindmismatchnotice+refreshrecovery; screenshot,narrowlayout.
- [ ] Updatecurrentguide/docsindex/newdatedreport withoutrewritingoldhistoricalevidence; independentwholebranchreview plusonefixwaveifneeded.
- [ ] Stoponlyselfcreatedservices; cleangit,smallcommits,pushnewbranchandverifyremoteSHA/mainunchanged.
