# 服务器 Agent 首次试用修正：连接复用、页面返回、邮件设置

2026-11-11. Branch `fix/agent-connection-reuse`, base `35d9da3` (main after PR #3 was merged).

## Background (from user trial)

After the user installed the Agent on Aliyun, they reported three problems:

1. **False SSH failure.** The Agent page reported "SSH connection or authentication failed" while the terminal on the same server worked fine.
   - The backend log shows paramiko failing with `Error reading SSH protocol banner` / `EOFError`: the server closed the connection before sending its banner.
   - This is how sshd behaves when it throttles too many new connections (MaxStartups or PerSourcePenalties).
   - The page opens a new SSH login for each request, several at the same time. The status, job list, alert settings and each job's last result and next run are all separate requests.
   - `handshake()` catches every exception, including ones raised while the connection is in use, and reports all of them as a connection or authentication failure, in English.
2. **Every return to the page reloads everything.** Leaving the page discards its data, and each request then costs a new SSH login, roughly 1 s against Aliyun.
3. **The email settings are confusing.** The user doesn't know what an SMTP host or port is. They asked for provider presets, or for just an address plus an app password.

## Goals

- Every SSH request to a server goes over an already authenticated, reused connection. At most one new login is in progress per server at any time. A refused connection is retried once after a short wait.
- Connection errors are reported in Chinese and in specific terms. Only a failure while connecting counts as a connection failure.
- Returning to the Agent page shows the last data immediately and refreshes in the background. One page refresh sends as few SSH requests as possible.
- The email form is filled in with an email address and its authorization code / app password. QQ, 163, 126, yeah.net, Gmail and 阿里邮箱 are filled in automatically. Other providers are entered under 高级设置.

## Non-goals

- Changing host-key pinning, trust or authentication logic, or the terminal's own long-lived connection.
- A connection pool shared across processes or kept across restarts.
- OAuth email login.

## Task A: Reusing SSH connections (backend)

Files: `backend/services/remote_connection.py` (main change); any call sites that need to adapt (`agent_client.py`, `remote_probe.py`, `remote_files.py`, `remote_monitor.py`, `workloads.py`, `routers/remote.py`, `routers/servers.py`, and `routers/terminal.py` only if required); tests.

1. **Separate the connect phase from the use phase.**
   - `handshake()` only maps exceptions raised while connecting, during the banner, during key exchange or during authentication. Exceptions inside the `with` body propagate unchanged.
   - Paramiko or socket errors raised while a connection is in use are mapped where they occur, as each call site already does, and must never be labelled `connection_failed`.
2. **Specific Chinese messages for connect-phase errors.** The codes stay the same except where a new code is listed here.
   - Banner read failure, EOF or connection reset: `connection_refused_by_server`, 503, `服务器暂时拒绝了新的 SSH 连接（可能连接过于频繁），请稍后重试`.
   - TCP refused: `connection_failed`, `无法连接服务器的 SSH 端口`.
   - Timeout: `connection_failed`, `连接服务器超时`.
   - DNS failure: `connection_failed`, `无法解析服务器地址`.
   - Authentication failure: `authentication_failed`, `SSH 认证失败`.
   - Any other error: `connection_failed`, `SSH 连接失败`.
   - Error details such as passwords, keys or raw exception text never go back to the client. The server-side log records only the exception type.
3. **Connection pool.**
   - Key: `(owner_id, server_id)`.
   - The cached value is an authenticated paramiko `Transport`, plus a fingerprint over: host, port, username, the pinned key_type and key_data, auth_mode, and a hash of the current credential (sha256 of the encrypted password, or the stored private key's version/ciphertext).
   - `open_remote()` first verifies the pin through `require_pin`, exactly as today. It reuses a pooled transport only if the transport `is_active()` and its fingerprint matches the current one. Otherwise it closes the old transport and connects again.
   - Each server has a lock, so only one new login per server is in progress at a time. Callers that arrive meanwhile wait and then reuse the result.
   - Idle eviction after 120 s; maximum lifetime 600 s.
   - Server edits, trust or pin changes, private key saves and server deletion call `invalidate(server_id)`.
   - On `connection_refused_by_server`, wait 1.5 s and retry the connection once.
   - Thread safety: requests run in FastAPI worker threads, so the pool needs a lock. Each request opens its own channel or SFTP session on the shared transport and closes it when done.
4. **`remote_deadline` must no longer close the whole transport.**
   - On timeout, close only the channels and SFTP sessions belonging to that request, and mark the transport unusable so it is dropped from the pool. Other requests that are already running on it must not be cut off.
   - If a channel-level timeout is not enough to interrupt a paramiko event wait, dropping the transport is still allowed, but it must be removed from the pool so the next request reconnects.
   - The `remote_timeout` behaviour and code stay the same.
5. **Leave the terminal's long-lived PTY connection alone.** It keeps its own dedicated connection, unless reusing the pool is simpler and safe; explain whichever choice is made.
6. **Tests:**
   - Multiple requests reuse one transport.
   - Concurrent requests log in only once.
   - A changed credential, target or pin leads to a new login.
   - Idle eviction works.
   - A banner EOF is retried once, and a second failure gives `connection_refused_by_server` with the Chinese message.
   - A paramiko or OSError inside the body is no longer reported as `connection_failed`.
   - A timeout does not affect the other channel running at the same time.
   - Server edit, key save and trust all invalidate the pooled connection.
   - The existing 245 tests stay green.

## Task B: Instant page return and fewer requests (Agent + frontend)

1. **Agent (`agent/panellab_agent/protocol.py`):** each job in the `jobs.list` result gains two fields; existing fields are unchanged.
   - `last_run`: `{run_id, status, trigger, started_at, ended_at}`, or null.
   - `next_run`: `{utc, local}` for the next run time, or null when the job is paused or has no schedule.

   The protocol version stays 1, since the fields are only added. The Agent version goes up to `0.1.1`. Add tests.
2. **Frontend:** when `jobs.list` already carries `last_run` and `next_run`, don't send the per-job preview and `runs?limit=1` requests. Against an older Agent, keep the current fallback behaviour.
3. **Frontend cache:** a module-level cache keyed by server id (stored in memory only, never in localStorage) keeps the last `status`, `jobs` and `alerts`.
   - When the page returns, render the cached state immediately and refresh in the background, with a small 「更新中…」 indicator.
   - When the cache is stale or the background refresh fails, keep the old data visible and show the error.
   - Clear the cache on logout and when switching user.
4. **A refresh fetches the status and the job list in parallel** and loads the alert settings only when 告警设置 is opened.
5. After merging, the user upgrades the Agent with the page's 升级 / 重新安装 Agent button. Before the upgrade the page keeps working through the fallback.

## Task C: Email settings with provider presets (frontend)

1. Default view: 邮箱地址, 授权码 / 应用专用密码, 收件人 (defaults to the email address, more than one allowed), and 发送测试邮件.
2. Recognise the domain and fill in host, port, security, username and sender automatically:

   | Domain | Host | Port | Security |
   | --- | --- | --- | --- |
   | qq.com, foxmail.com | smtp.qq.com | 465 | ssl |
   | 163.com | smtp.163.com | 465 | ssl |
   | 126.com | smtp.126.com | 465 | ssl |
   | yeah.net | smtp.yeah.net | 465 | ssl |
   | gmail.com, googlemail.com | smtp.gmail.com | 465 | ssl |
   | aliyun.com | smtp.aliyun.com | 465 | ssl |

   For each provider, show one line on where to get the code, e.g. QQ: 「QQ 邮箱网页版 → 设置 → 账号与安全 → 开启 SMTP 服务并生成授权码」; Gmail: 「需先开启两步验证，再在 Google 账号 → 安全 → 应用专用密码 中生成」.
3. For an unknown domain, expand 高级设置 automatically and ask the user to fill in the provider's SMTP settings. 高级设置 contains the current raw fields and is collapsed by default.
4. Show a hint that 阿里云服务器默认封禁 25 端口，请使用 465.
5. The existing three-way password handling (keep / change / delete) and the R14 validation stay unchanged. When existing settings are loaded and they match a preset, show the simple view; otherwise show 高级设置 expanded.
6. Tests: recognising each domain fills in the settings correctly; an unknown domain expands 高级设置; the recipient defaults to the sender; the submitted shape is identical to what `alerts.configure` accepts today.

## Verification

- agent, backend, frontend and build all pass.
- `python3 scripts/verify_agent_lab.py --panel` passes.
- Add a lab check that fires 10 requests at once at an sshd with `MaxStartups 2:100:2`: all succeed and only one login happens. If the fixture can't easily be configured this way, use a unit test instead and say so in the record.
- Ask the user to upgrade the Agent on Aliyun and check that switching pages is smooth and the false error is gone.
