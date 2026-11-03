# Personal fork update — 2026-11-03

## Source and preservation

- Fork: `filou01/bticino_intercom`.
- Original branch: `personal/fix-token-reauth-cleanup` at
  `48733518f0da94335ba94b6b562e8ebd441a9237`.
- Upstream: `k-the-hidden-hero/bticino_intercom:main` at
  `e7fafaa37283a494b3c777f119eb0284192313c2` (version 2.0.4-rc1).
- Verified divergence: 143 upstream-only commits, three fork-only commits.
- New branch: `personal/fix-token-reauth-cleanup-upstream-20261103`, created
  directly from `upstream/main`. The original branch is not rebased, reset,
  merged into, or force-pushed.

## Decisions for the three personal commits

| Commit | Original function | Upstream finding | Decision |
| --- | --- | --- | --- |
| `b0d2e3f` — clear stale tokens on reauth | Share the storage version; remove stale tokens during setup and successful reauth; propagate coordinator auth failures; use a pybticino feature branch; add tests. | Token persistence and the reauth form exist, but successful reauth still reloads stale storage. Status/event requests still swallow AuthError and topology auth errors become UpdateFailed. | Preserve token cleanup and HA reauth propagation through a shared helper; keep existing platform/topology logic; adapt tests to current fixtures. Replace the feature dependency with upstream's existing `pybticino>=1.9.0`. |
| `e548c50` — clear tokens on coordinator auth failure | Remove persisted tokens before HA reauth; recognize API token rejection during topology, status and event polling. | These corrections are still missing. | Preserve cleanup at all three stages. Recognize HTTP 401 or an explicit invalid-access-token error using ApiError's structured fields. A generic 403 may mean a permission error and must not automatically erase tokens or trigger password login. |
| `4873351` — retry auth with stored credentials | Try full password authentication before asking the user; retry a coordinator update once. | `pybticino` 1.9.0 already falls back from refresh failure to password authentication inside `get_access_token()`. It does not handle an API rejecting a locally unexpired token. | Keep one explicit password login and operation retry for API-rejected tokens. On AuthError, clear storage and ask HA for reauth, without repeating the library's already attempted password login. Preserve upstream's bounded transient-error counter and partial/empty-topology handling. |

The last personal commit also has a setup fallthrough defect: after recovering
an invalid-token ApiError, it still unconditionally raises ConfigEntryNotReady.
The new helper returns the recovered operation result instead. A setup-level
regression test covers this path.

The library dependency decision was checked against both repositories:
`filou01/pybticino:feature/module-configs-dnd` is an ancestor of
`k-the-hidden-hero/pybticino:main`. Published pybticino 1.9.0 provides the
module-config, DND and professional-studio methods used by this upstream
integration. The old Git pin adds no missing functionality.

## Reauth, WebSocket and cleanup behavior

- Setup, polling, WebSocket connect and hourly resubscribe share token-error
  handling. Successful password authentication continues to persist tokens
  through the existing token callback.
- Recovery is bounded to one password login and one operation retry. Rejection
  of a newly obtained token clears it again and requests HA reauth.
- `WebsocketClient.connect()` and subscription methods wrap underlying library
  errors. Typed `__cause__` chains are inspected, so wrapped AuthError and token
  ApiError receive the same treatment. Generic transport errors retain upstream
  reconnect behavior; no broad message-based exception guessing is used.
- On terminal WebSocket authentication failure, the manager requests reauth and
  exits through its existing finally block, cancelling/awaiting its listener and
  disconnecting the client.
- Successful reauth validates the new password, updates entry data, removes
  old stored tokens and then reloads. Rejected passwords leave entry data and
  stored tokens intact.
- Ordinary unload retains persisted tokens for restart; permanent removal
  retains upstream token/history deletion. No competing cleanup manager added.
- Retained upstream fixes include `8ff0427` (backoff/stable sessions/jitter),
  `61e91e4` (consume listener exceptions without orphaned shield futures),
  `fd6fba2` (unload callback returns None), `7b34947` (wait for LOADED), and
  `5fa9fb9` (protect entities against empty/partial topology). The old stale-WS
  silence watchdog is not reintroduced.
- Pass `config_entry=entry` explicitly to DataUpdateCoordinator. Current HA
  reports reliance on its implicit ContextVar when the coordinator is created
  outside setup, as the existing isolated coordinator tests do.

## Validation

Final results: **264 passed**, including 25 new regression cases. Ruff lint and
format checks pass (48 files); Bandit reports no issues; `git diff --check`
passes. The complete test run used:

```text
../test-env/Scripts/python.exe ../run_pytest_windows.py tests/ -q --tb=short --basetemp=../pytest-temp-verified --junitxml=../../outputs/test-results.xml
```

Tests use
Python 3.14.10, Home Assistant 2026.10.4,
pytest-homeassistant-custom-component 0.13.375, pybticino 1.9.0 and Ruff 0.16.7.

The host is Windows. The normal pytest invocation cannot import HA's Unix-only
`fcntl` and `resource` modules. A separate launcher outside the repository
supplies empty import placeholders (their Unix operations remain unavailable)
and allows only asyncio's internal loopback socketpair construction through
pytest-socket. Application network access remains blocked by the test plugin.
No production or dependency code is patched for this workaround. These runs
exercise integration logic, not Unix daemon locking/resource limits, and do not
replace a Linux CI run or a test against real BTicino hardware/cloud credentials.

The explicit `--basetemp` keeps pytest's temporary files inside the writable
workspace. An earlier verification attempt could not access the host's default
temporary directory; the final complete run above has no errors or skips.

One remaining library limitation is that pybticino also labels some token-endpoint
network failures as AuthError. This integration follows that classification;
ordinary ApiError server failures do not prompt reauth.

The added regression suite covers recovery, repeated rejection, failed password
login, permission/server errors, all polling stages, setup continuation, reauth
ordering, real pybticino exception wrapping, and WebSocket connect/resubscribe
reauth with listener cleanup.
