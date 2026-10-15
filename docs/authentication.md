# Authentication and API operations

New passwords use Argon2id through argon2-cffi 25.1: 64 MiB, three passes,
four lanes and a random salt. Hashing runs off the async event loop with at
most two password operations per process. Successful login upgrades legacy
PBKDF2 hashes and older Argon2 parameters without changing the user's password.
Unknown usernames still perform a normal password verification. Malformed or
unreasonably expensive stored parameters fail closed.

New accounts require a 15–128-character passphrase; existing accounts keep their
original login compatibility. Spaces and Unicode are supported, with no silent
truncation. The API returns generic invalid-credential errors.

## Sessions and admission

The existing opaque bearer-token API is preserved. Tokens contain 256 bits of
randomness and only their SHA-256 hashes are stored. Login and registration now
also return `expires_at`. Defaults are seven days absolute lifetime, one day
without an authenticated API request, and five active sessions per user.
Activity timestamps are written at most once per minute. Workspace polling
counts as API activity; this is not a measure of keyboard or pointer activity.

`POST /api/auth/logout` revokes the current token and returns 204, including
when it was already revoked. `POST /api/auth/logout-all` revokes the current
user's sessions. User-row locks serialize session creation, the session cap and
logout-all. The frontend waits for server revocation before clearing its saved
token; a temporary failure remains visible and can be retried.

Login and registration share PostgreSQL-backed fixed-window admission limits:
30 attempts per source IP and 10 per source IP + normalized username in 60
seconds. This limits work before expensive hashing and remains effective across
workers. PostgreSQL's clock determines windows and remaining wait time after row
lock acquisition. Responses use HTTP 429 and `Retry-After`. Counters persist after failed
password checks and expired buckets are cleaned in bounded batches. Stored keys
are hashes; this is data minimization, not anonymous analytics. The app does not
parse forwarding headers. Configure trusted proxies in the ASGI deployment;
otherwise clients behind a proxy may share its source-IP bucket.

Environment settings are documented in `backend/.env.example`:

| Setting | Default |
| --- | --- |
| `AUTH_SESSION_TTL_SECONDS` | 604800 |
| `AUTH_SESSION_IDLE_SECONDS` | 86400 |
| `AUTH_MAX_SESSIONS` | 5 |
| `AUTH_RATE_WINDOW_SECONDS` | 60 |
| `AUTH_RATE_IP_LIMIT` | 30 |
| `AUTH_RATE_ACCOUNT_LIMIT` | 10 |
| `DATABASE_CONNECT_TIMEOUT_SECONDS` | 5 |
| `DATABASE_COMMAND_TIMEOUT_SECONDS` | 15 |
| `HEALTH_TIMEOUT_SECONDS` | 2 |

## API boundaries and recovery

When `AGENT_ACCESS_TOKEN` is configured, the project gate also protects login,
registration and account/session routes. Only auth status/check and operational
health probes are exempt. Gate failures have code `access_token_required` so
clients can show the appropriate entry screen. Authentication responses use
`Cache-Control: no-store`; responses also include `X-Content-Type-Options: nosniff`.

Default CORS permits loopback origins, plus explicit `CORS_ORIGINS`. Configure
the actual HTTPS frontend origin for remote hosting. Existing explicit config
overrides remain effective, including any previously broad regex; review that
setting when upgrading a deployment.

`GET /api/health` checks process liveness. `GET /api/health/ready` checks database
connectivity and the required Alembic revision within two seconds by default.
An outdated schema or unreachable database returns 503. Connection acquisition
and database commands have separate timeouts. Database failures return a generic
retryable 503 without SQL or credentials. Readiness does not prove write access;
the integration tests exercise real registration, login and ownership writes.

Frontend authentication requests have a 15-second deadline. HTTP 401 clears the
expired workspace and aborts outstanding session requests. Network and 503
failures preserve saved login and offer Retry connection. Long chat streams,
uploads and document reindexing do not inherit the auth-request deadline.

## Migration and validation

Stop older application processes, back up the database and run `alembic upgrade
head` before starting the new code. Migration `0010_auth_sessions` gives each
existing session `created_at + 7 days` expiry and `created_at` as its activity
timestamp. Old sessions may therefore require login again; the migration never
extends their original lifetime. Password hashes change only after successful
verification. Schema downgrade drops revocation/expiry data and is not an
authentication rollback strategy; restore an appropriate backup or invalidate
all sessions if reverting the application.

The acceptance suite covers legacy rehash, malformed parameters, event-loop
responsiveness, real PostgreSQL writes, expiration, revocation, ownership,
concurrent session caps, concurrent admission limits, project gates, CORS and
readiness deadlines. A disposable database validates fresh upgrade and
0009 → 0010 migration with both recent and expired legacy sessions. Browser
acceptance covers registration, old-account login, logout, expired-session UI,
and stopping/restarting the backend followed by retry without reauthentication.

Design references checked on 2026-10-15:

- [OWASP password storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)
- [argon2-cffi verification and rehash](https://argon2-cffi.readthedocs.io/en/stable/api.html)
- [OWASP session management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
- [Starlette CORS middleware](https://www.starlette.io/middleware/)
