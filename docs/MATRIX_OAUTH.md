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

Both login methods accept a server name such as `example.com` or an absolute URL
such as `https://example.com`. Bare server names use HTTPS, then Matrix discovery
resolves delegated client endpoints. OAuth requires HTTPS; explicit HTTP remains
available for password login.

The optional username accepts a localpart such as `relay` or a full Matrix ID such
as `@relay:example.com`. A full ID restricts the exact approved account; a localpart
checks the approved account's name without assuming the API host is its Matrix
server name. Without a username, the account selected during browser approval is
used. If the homeserver is omitted, MMRelay prompts for a server name or URL.

Open the displayed verification address in a browser, enter the displayed code,
and approve the MMRelay session. The browser may run on a different machine from
the relay, including for SSH and Docker deployments. No callback port is opened.
Only approve a code from a login you initiated; treat it as a temporary secret.

Keep the login command running while completing browser approval. MMRelay polls
until approval, denial, or the login deadline. Pending responses with HTTP 400
or 403 keep the command waiting at the server's polling interval. Credentials are saved
after the approved account and device have been verified. Wait for the command
to report successful authentication and the saved credentials path before
starting the relay.

If the command has exited, start a fresh OAuth login and use its new code.
Approving a code from an exited process cannot create the local credentials file.

For Docker, run the command through the deployment's existing interactive
`docker compose run --rm mmrelay ...` workflow, using the same persistent home
mount and service account. Do not create a separate disposable credentials volume.

The session receives a distinct device ID. MMRelay verifies the account and device
through whoami before saving credentials. When E2EE is configured, login initializes
the persistent encryption store and attempts the same own-device cross-signing
used at relay startup. It creates signing keys for an account without an existing
identity, or reuses the local signing sidecar to sign the MMRelay device. It does
not verify other users or their devices. Success is checked against the
homeserver's master-to-self-signing-to-device signature chain.

Browser login approval establishes authentication, not encryption trust. Other
clients may need to trust the account's master key even after device signing
succeeds. See the [E2EE guide](E2EE.md).

### Recovering a missing signing sidecar

Ordinary login and startup preserve an existing server identity when its private
signing keys are missing locally. Logging out and creating another session does
not recover those keys. Prefer restoring the complete E2EE store and sidecar from
a backup.

To explicitly replace a lost identity, stop the relay and run:

```bash
mmrelay auth login --oauth --reset-cross-signing
```

An existing OAuth session is reused with the same device ID and encryption store;
do not log out first. With saved OAuth credentials, `auth login --reset-cross-signing`
also selects this passwordless recovery path. If the server requires approval,
the command displays its account-management URL. Approve the cross-signing reset
in a browser and keep the command running. MMRelay retries the same signing keys
at five-second intervals within a bounded two-minute operation. Stable m.oauth
and the org.matrix.cross_signing_reset challenge are supported on the session's
HTTPS issuer origin; password challenges do not trigger a password fallback.

Replacing an identity changes the account's master and self-signing keys. Other
clients may require account identity verification again. The command signs only
MMRelay's device and preserves credentials and encryption data when signing fails
or approval is cancelled. Run `auth login --oauth` with an existing session to
retry signing without requesting identity replacement.

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
fails. Password-session logout also retains the encryption store and signing
sidecar. It uses the saved access token without requiring a password. An explicit
`--password` requests optional password verification; a bare flag prompts securely.
Both logout paths retain credentials when server revocation fails or times out.
Password logout removes a credential whose server reports `M_UNKNOWN_TOKEN` because
that session is already invalid. Logout affects the saved relay session, not every
session on the account. A later login creates a device and reuses retained account
signing keys; retaining keys does not keep the revoked session authenticated.

## Work before general availability

- Add authorization-code login with PKCE for servers without device authorization.
- Add secret-storage recovery for existing encrypted accounts whose signing keys
  belong to another client; own-device signing and explicit browser-approved
  identity reset do not recover those private keys.
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
