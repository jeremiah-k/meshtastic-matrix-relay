# Native Matrix OAuth login (experimental)

This implementation is draft work, not a release-ready replacement for every
Matrix login flow. It creates a dedicated MMRelay session through browser
approval. It does not import Element access tokens, require homeserver
administrator privileges, or collect a Matrix password.

## Server requirements

The homeserver must advertise native OAuth metadata, public-client registration,
device authorization, refresh tokens, and token revocation. Server policy can
disable device authorization or reject client registration. A server's product
name alone does not establish compatibility.

This draft requires HTTPS with publicly trusted certificates and OAuth endpoints
on the issuer's origin. Endpoint URLs cannot contain userinfo, query strings, or
fragments. Servers using other endpoint layouts need additional compatibility
work. Matrix client delegation is supported; the stable authentication metadata
endpoint is preferred, with an unstable endpoint fallback for earlier deployments.

## Create a session

Stop the relay before changing its authentication. Existing credentials are not
overwritten; in-place conversion from a password session is not implemented.
Use the same MMRelay home and configuration as the relay process:

```bash
mmrelay auth login --oauth --homeserver https://matrix.example.com --username @relay:example.com
```

The optional username must be a full Matrix ID. It restricts which approved
account is accepted. Without it, the account selected during browser approval is
used. If the homeserver is omitted, MMRelay prompts for its HTTPS URL.

Open the displayed verification address in a browser, enter the displayed code,
and approve the MMRelay session. The browser may run on a different machine from
the relay, including for SSH and Docker deployments. No callback port is opened.
Only approve a code from a login you initiated; treat it as a temporary secret.

For Docker, run the command through the deployment's existing interactive
`docker compose run --rm mmrelay ...` workflow, using the same persistent home
mount and service account. Do not create a separate disposable credentials volume.

The session receives a distinct device ID. MMRelay verifies the account and device
through whoami before saving credentials. Browser approval establishes login,
not encryption trust: **this draft does not complete OAuth-specific E2EE device
verification or reauthentication**. See the [E2EE guide](E2EE.md). An existing
cross-signing identity is not deliberately reset by OAuth login; accounts lacking
usable local signing keys still need a supported verification/recovery workflow.
Do not assume encrypted rooms work merely because login succeeds.

## Session lifecycle

Credentials contain both access and refresh tokens. MMRelay writes them with
atomic replacement, owner-only file permissions on POSIX systems, and a separate
cross-process lock. On Windows, inherited filesystem ACLs still need to restrict
access. Protect the complete MMRelay home and any backups; never post credentials
in issues or logs. The credential format is versioned independently of passwords.
Records must fit the same 64 KiB limit for writing and reading. Oversized records
are rejected before replacement, and their issued tokens are submitted for revocation.

Before each Matrix transport attempt, including SDK retries, MMRelay reloads the
record and renews tokens near expiry. A rotated refresh token is saved before
the access token is used. Restarting retains the same device and crypto store.
Removed or replaced credentials stop requests instead of switching accounts.
Keep a single active relay instance per session; competing authentication
operations fail rather than sharing refresh-token ownership.

Failed renewal does not trigger password login. Check connectivity and account
session status, then authenticate again if revocation requires it. The adapter
does not replay a failed Matrix request after a 401 response. A process crash or
storage failure after the server rotates a token but before local persistence can
require reauthentication; atomic file replacement cannot make the remote and
local updates one transaction.

Unreadable or corrupt saved credentials stop startup, including when a password
exists in configuration. Repair the file or deliberately remove it before
creating another session; automatic password login does not repair saved files.

Revoke the native session without a password:

```bash
mmrelay auth logout
```

Use `--yes` only when confirmation is intentionally unnecessary. Stop the relay
first. OAuth logout revokes the refresh token and removes credentials after the
server accepts revocation. Failed revocation retains credentials. Encryption keys
are retained: revoking a session is not an encryption identity reset. Account
settings provide a separate way to revoke an orphaned session if local storage
fails. Password-session logout keeps its existing behavior, including crypto-store
cleanup.

## Work before general availability

- Add authorization-code login with PKCE for servers without device authorization.
- Complete OAuth reauthentication for cross-signing and a usable verification or
  recovery workflow for existing encrypted accounts.
- Exercise real homeservers, including policy rejection, token expiry, restart,
  revocation, encrypted rooms, and headless deployments.
- Decide on automatic flow selection and authentication status diagnostics after
  live compatibility testing.
- Validate the Matrix transport adapter against supported SDK versions before
  changing the dependency pin.

The existing `mmrelay auth login` password workflow remains available for servers
that support it. Native OAuth failure never silently falls back to that workflow.
There is no Element token-import workflow in this feature.

## Protocol references

The implementation follows the [Matrix OAuth API](https://spec.matrix.org/v1.19/client-server-api/#oauth-20-api)
and [OAuth device authorization](https://www.rfc-editor.org/rfc/rfc8628).
The [MAS scope reference](https://element-hq.github.io/matrix-authentication-service/reference/scopes.html)
describes device-bound Matrix access. These references describe protocols, not
guarantees about a particular homeserver's enabled policies.
