# AppManager Lite

A self-hosted landing portal for your teams. It presents a single, modern home
page with quick access to each team's applications, plus built-in
authentication, self-service application management with administrator approval,
and user management.

The portal is mountable at the site root or behind a reverse proxy on a path
prefix (for example `/home`), and ships with a single lifecycle script that
bootstraps its own dependencies.

## Status

| Area | State |
|---|---|
| Authentication + session management | Implemented |
| User management (admin) | Implemented |
| Team management (admin): create, reorder, icon | Implemented |
| Portal shell: header, collapsible sidebar, routing | Implemented |
| Team sections + per-team application cards | Implemented |
| Application management (self-service + admin approval) | Implemented |
| Local-alias links for reverse-proxied applications | Implemented |
| HTTP security headers (CSP, HSTS, frame/COOP) | Implemented |

## Architecture

- **Backend:** FastAPI (Python) with a SQLite datastore. Serves a JSON API
  under `/api` and hosts the built frontend.
- **Frontend:** React + TypeScript built with Vite. A single build runs under
  any deployment prefix; the backend injects a matching `<base href>` at serve
  time.
- **Auth:** Argon2id password hashing, server-side sessions, and an
  HttpOnly + SameSite=Strict + Secure session cookie. State-changing requests
  require an `X-CSRF-Token` header. Optional SSO supports OpenID Connect/OAuth2
  providers and SAML 2.0 identity providers. Roles (`admin`, `user`) and team
  membership are enforced server-side, as are application ownership, team scope,
  and the approval workflow.

```text
AppManager-Lite/
|-- appmanager-lite           # lifecycle script (start/stop/status/restart)
|-- backend/                   # FastAPI app, SQLite, auth, routers, tests
|   `-- app/
|-- frontend/                  # React + TS (Vite) single-page app
|   |-- public/app-logo.svg    # fallback logo (branding is admin-configurable)
|   `-- src/
|-- scripts/                   # check.sh, test.sh, security-check.sh
|-- project-manifest.json      # machine-readable project structure/API/data model
|-- NOTICE / THIRD-PARTY-NOTICES.md
`-- README.md
```

A machine-readable [`project-manifest.json`](project-manifest.json) (validated by
[`project-manifest.schema.json`](project-manifest.schema.json)) describes the
components, tech stack, commands, configuration, HTTP API, and data model for
tooling and agents. Keep it in sync when structure, the API, configuration, or
the schema changes.

## Quick start

Prerequisites (minimal requirements): [uv](https://docs.astral.sh/uv/) (backend
Python environment), Node.js with npm (frontend), and Python 3.11+.

You can install uv and Node.js/npm with the bundled installer (**root/sudo
required**); the portal itself runs as a normal user:

```bash
sudo ./appmanager-lite install   # installs uv + Node.js/npm; root/sudo required
```

Run it with `sudo` from your normal account: uv is installed via its official
installer **into your user's home** (`~/.local/bin`, which must be on your
`PATH`), while Node.js/npm are installed system-wide via the OS package manager
(apt, dnf/yum, pacman, zypper, or apk). Already-present tools are skipped. If you
prefer, install the prerequisites manually instead.

The lifecycle script uses uv to provision the backend environment, installs
frontend dependencies, builds the frontend, and starts the server — all as a
normal user (no root):

```bash
./appmanager-lite start                 # http://127.0.0.1:8000
./appmanager-lite start --bind 127.0.0.1:8137
./appmanager-lite start --dev           # foreground, auto-reload (Ctrl-C)
./appmanager-lite status
./appmanager-lite stop
./appmanager-lite restart --bind 0.0.0.0:8000 --base-prefix /home
```

Useful start flags:

- `--bind IP:PORT` — address to listen on (default `127.0.0.1:8000`).
- `--base-prefix /path` — optional subpath mount; usually unnecessary (the app
  works behind a proxy via relative paths) and only needed for strict
  reverse-proxy setups that require the app to emit its public subpath.
- `--dev` — run in the foreground with auto-reload; Ctrl-C to stop.
- `--rebuild` — force a frontend build even when it looks up to date.
- `--reinstall` — force dependency reinstallation.

On every `start`/`restart` the script keeps things current automatically: it
runs `uv sync` for the backend (uv is the only Python dependency manager),
reinstalls frontend dependencies when the lockfile changed, and **rebuilds the
frontend whenever its sources, config, or the current commit changed** since the
last build (use `--rebuild` only to force a build). It also **warns when the
checked-out code advanced** since the last start — the database migrates
automatically on start (additive, idempotent), but any destructive schema or
dependency change may need manual follow-up.

### First-run administrator

On first start (with auth enabled), an `admin` account is created and its
generated password is written to `data/first-run-admin-credentials.txt`
(mode `0600`, git-ignored). The administrator must change this password at first
sign-in. To reset it later:

```bash
./appmanager-lite reset-admin-password
```

The reset also forces a password change on the admin's next sign-in.

### First-login setup wizard

The very first time an administrator signs in to a freshly provisioned
deployment (immediately after changing the initial password), they are taken
once to **Settings → General Settings** to set the initial branding
(application name and logo). Saving the **Application Basic Information** card
marks the deployment as configured; subsequent sign-ins land on the Home page as
usual. The "configured" state is stored server-side in the single settings row.

### SSO authentication

Local username/password authentication is enabled by default. You can also enable
OIDC/OAuth2, SAML 2.0, or both. SSO provider secrets are read from environment
variables at startup; they are not stored in SQLite or exposed to the frontend.
If a user signs in through SSO, AppManager Lite does not force that session
through the local password-reset screen, even when the local account has a
pending password change. The flag remains in place and is still enforced if the
same user signs in with a local password.

Callback URLs to register with your identity provider are based on the public app
origin plus the optional `APP_BASE_PREFIX`:

- OIDC/OAuth2 redirect URI: `https://<host><APP_BASE_PREFIX>/api/auth/oidc/callback`
- SAML ACS URL: `https://<host><APP_BASE_PREFIX>/api/auth/saml/acs`
- SAML SP metadata URL: `https://<host><APP_BASE_PREFIX>/api/auth/saml/metadata`

Examples without a prefix:

- `https://apps.example.com/api/auth/oidc/callback`
- `https://apps.example.com/api/auth/saml/acs`
- `https://apps.example.com/api/auth/saml/metadata`

Examples with `APP_BASE_PREFIX=/home`:

- `https://apps.example.com/home/api/auth/oidc/callback`
- `https://apps.example.com/home/api/auth/saml/acs`
- `https://apps.example.com/home/api/auth/saml/metadata`

Authentication mode and shared SSO settings:

- `APP_AUTH_MODE` — selects which sign-in methods are available:
  - `local` shows and accepts only username/password sign-in.
  - `sso` shows and accepts only configured SSO providers.
  - `both` shows and accepts both local and SSO sign-in. This is the default.
- `APP_SSO_LOCAL_LOGIN_ENABLED` — legacy fallback used only when `APP_AUTH_MODE`
  is unset. Prefer `APP_AUTH_MODE=local`, `APP_AUTH_MODE=sso`, or
  `APP_AUTH_MODE=both` for new deployments. Keep local login available for at
  least one break-glass admin account unless your IdP availability is guaranteed.
- `APP_SSO_AUTO_PROVISION` — create local users on first successful SSO sign-in
  (`true` by default). If disabled, an administrator must create matching local
  users first.
- `APP_SSO_DEFAULT_ROLE` — role for auto-provisioned users, `user` by default.
- `APP_SSO_EMAIL_DOMAIN_ALLOWLIST` — optional comma-separated domain allowlist,
  for example `example.com,example.org`.

OIDC/OAuth2 settings:

- `APP_OIDC_ENABLED=1`
- `APP_OIDC_PROVIDER` — `google`, `microsoft`, `okta`, `auth0`, `keycloak`, or
  `generic`. Google and Microsoft have built-in endpoint presets; other OIDC
  providers use issuer discovery or explicit endpoint overrides.
- `APP_OIDC_LABEL` — optional login button label.
- `APP_OIDC_CLIENT_ID` — client/application ID from the provider.
- `APP_OIDC_CLIENT_SECRET` — client secret. For Microsoft Entra ID, this is the
  application/SPN client secret value.
- `APP_OIDC_ISSUER` — issuer URL, required for generic discovery and most
  non-preset providers.
- `APP_OIDC_SCOPES` — defaults to `openid email profile`.
- `APP_MICROSOFT_TENANT` — Microsoft tenant ID/domain, or `common` by default.
- `APP_OIDC_AUTHORIZATION_ENDPOINT`, `APP_OIDC_TOKEN_ENDPOINT`,
  `APP_OIDC_USERINFO_ENDPOINT`, and `APP_OIDC_JWKS_URI` — optional explicit
  endpoint overrides for providers that do not publish standard discovery.

Provider notes:

- Google: create an OAuth client for a web application, add the OIDC callback URL
  as an authorized redirect URI, set `APP_OIDC_PROVIDER=google`, and provide the
  client ID/secret.
- Microsoft Entra ID: create an app registration, add a web redirect URI using
  the OIDC callback URL, create a client secret, set `APP_OIDC_PROVIDER=microsoft`,
  `APP_MICROSOFT_TENANT=<tenant-id-or-domain>`, and provide the client ID/secret.
- Okta/Auth0/Keycloak/generic OIDC: configure an application that allows the OIDC
  callback URL, then set the issuer URL and client ID/secret. If discovery is not
  available, provide the explicit endpoint override variables.

Microsoft Entra example:

```bash
export APP_AUTH_MODE=both
export APP_OIDC_ENABLED=1
export APP_OIDC_PROVIDER=microsoft
export APP_OIDC_LABEL="Sign in with Microsoft"
export APP_OIDC_CLIENT_ID="<application-client-id>"
export APP_OIDC_CLIENT_SECRET="<client-secret-value>"
export APP_MICROSOFT_TENANT="<tenant-id-or-domain>"
export APP_SSO_AUTO_PROVISION=0
```

If AppManager Lite runs behind nginx or another TLS-terminating reverse proxy,
make sure uvicorn trusts that proxy's forwarded headers; otherwise callback URLs
may be generated as `http://...` and rejected by the identity provider. Uvicorn
uses `FORWARDED_ALLOW_IPS` for this. Set it to the proxy IP or CIDR that forwards
to the backend, for example:

```bash
export FORWARDED_ALLOW_IPS="172.16.10.2"
```

The redirect URI registered in Microsoft Entra must exactly match the runtime URL
generated by the app, for example:

```text
https://apps.example.com/api/auth/oidc/callback
```

SAML 2.0 settings:

- `APP_SAML_ENABLED=1`
- `APP_SAML_LABEL` — optional login button label.
- `APP_SAML_SP_ENTITY_ID` — optional service-provider entity ID. Defaults to the
  SAML metadata URL.
- `APP_SAML_IDP_ENTITY_ID` — IdP entity ID.
- `APP_SAML_IDP_SSO_URL` — IdP single sign-on URL.
- `APP_SAML_IDP_X509_CERT` — IdP signing certificate, PEM body without the
  surrounding `BEGIN/END CERTIFICATE` lines or as accepted by your deployment
  environment.
- `APP_SAML_EMAIL_ATTRIBUTE` — assertion attribute used as the local username;
  defaults to `email`. If absent, the SAML NameID is used.

For SAML, import the AppManager Lite SP metadata URL into the IdP when supported,
or manually configure the ACS URL, SP entity ID, and NameID/email attribute. SAML
assertions must be signed; unsigned assertions are rejected.

## Application management

A clean install starts with **no applications** — the Home and team sections are
empty until an administrator or user adds applications from the Application
Manager.

Every signed-in user reaches **Settings** from the sidebar and can
submit and edit applications for the teams they belong to. Administrators see the
same area with every application (and its creator), a tab for user management, a
**Teams** tab for managing teams, and a **General Settings** tab for branding
(application name and logo) and reverse-proxy configuration.

When an administrator creates a user, the username must be an **email address**;
it is the user's sign-in name. Each user also gets a derived **user ID** — the
email's local part, lowercased, with dots and underscores replaced by dashes
(restricted to letters, digits, and dashes; e.g. `john.doe@example.com` →
`john-doe`) — shown beneath the email in User Management and on the Account
page. Because the user ID names per-user resources, creating a user whose
derived ID collides with an existing user's is rejected.

Every user account carries its own **Ed25519 SSH keypair**, generated at user
creation (existing accounts are backfilled automatically on startup). From the
**Account** page a user can view their public key, download the private or
public key, and regenerate the keypair after an explicit confirmation
(regeneration and private-key downloads are audited — metadata only, never key
material; key material is served only to the owning, signed-in user). The
downloaded key files are named `id_ed25519_appmanager_<app>` (and `.pub`),
where `<app>` is the configured **Application name** lowercased with
non-alphanumerics turned into dashes (blank app name → `id_ed25519_appmanager`),
so users can identify the key in their `~/.ssh` directory; the generated SSH
config's `IdentityFile` lines reference the same filename. (Renaming the app
between downloading the key and the config means re-downloading so the two
agree.)
Regenerating also **rotates the key on the user's servers**: the old public
key is removed from `authorized_keys` (root and every `/home` user) and the
new one installed, using each server's admin SSH key; a per-server
verification summary (updated / skipped / failed, with reasons) is shown and
appended to each server's log. When a jump server is configured, the key is
rotated there too, on the account for the configured account model (the shared
jumper account in shared mode, otherwise the user's own account).

Each bundle template can carry an optional **description** (set in the bundle
add/edit card). The Account page's **Bundle Downloads** card downloads a
**zip** ready to unzip into `~/.ssh`: the personal SSH config, the user's
private key (filename with no extension) and public key, and a
`connect_server_<name>.sh` helper script per server so the user can connect
without typing the full `ssh` command. Each script is **self-contained and runs
in place from the unzipped directory**: it `cd`s into its own folder and invokes
`ssh` with the private key sitting beside it (`-i ./<key>`), connecting straight
to the server. When a jump server is configured the bastion hop is reached with
an explicit `ProxyCommand` that carries the **same** bundled key (also resolved
relative to the script's directory), so the whole chain works without touching
`~/.ssh`. The bundled `config` is provided as a ready-to-use `~/.ssh` artifact
for users who prefer to drop the files into their home directory and use the
config manually (its `Host` blocks reference `~/.ssh/<key>`). Because the zip
contains the private key the download is audited and never cached. The
downloaded filename carries a UTC timestamp suffix
(`<bundle>-YYYYMMDD-HHMMSS.zip`) so repeated downloads never collide in a
browser's downloads folder; only the zip's own filename is suffixed, not the
files inside it. Templates with
field mappings are rendered from account details;
mapping sources include the username, the derived **user ID**, apps server
host/IP, role, and **per-template variables** (`server_<slug>_name`,
`server_<slug>_ip`, `server_<slug>_user`, where `<slug>` is the server
template's name slug, e.g. `server_debian-coder_ip`). Each template variable
resolves to the user's **first** server created from that template (the server
user resolves to the template main user or the derived ID); templates with no
server for that user, and mappings whose template was later deleted, render as
empty. Because the variable is keyed by the template's **name slug**, renaming
a server template (or having two templates whose names slugify to the same
value) can leave a previously-saved mapping resolving empty; re-select the
template variable after a rename. The predefined, read-only **"SSH Config Default"** is a
**built-in** template that renders a full SSH config dynamically: a `Host *`
keepalive stanza, a `Host jumpserver` block (with the configured port) when the
jump server is enabled, and one `Host` block per server with `ProxyJump
jumpserver` when the jump server is enabled. Built-in templates can be
**cloned** into editable copies and **disabled** (hidden from downloads) but
not edited or deleted. Administrators can also set the user's **apps server**
(host/IP) — the host where that user runs their applications, used as the
upstream for that user's reverse-proxy aliases. When one or more server
templates are marked as **Apps servers**, user creation offers those templates'
names in an **apps-server dropdown** (with a **Custom** option for a manual
host/IP); an apps server is otherwise **optional** — an account can be created
without one (it can still view applications, but a custom apps server is
required to create them). The value chosen at user creation is only a
**reference** (the template's name) — it resolves to the actual host of a
live, provisioned apps-server server automatically when an alias is pushed
(preferring the server cloned from the referenced template, else the user's
first apps-server server), so it stays correct even though no server exists
yet at account-creation time. Each application carries its
**own port** (see below), so there is no per-user port. A normal user only sets
the port on an application; the upstream host comes from their apps server.
Application management's alias-upstream **apps-server dropdown** lists the
application owner's own **provisioned apps-server servers by name** (plus a
Custom free-text option) instead of template names; picking one stores that
server's resolvable hostname/IP as the alias's upstream host. Administrators
editing another user's application see that user's apps-server servers, and
the dropdown reloads when they change the application's owner. If the owner
has no apps-server servers yet, only Custom is offered.
Administrators have no per-user apps server, so when an administrator creates an
application they can set **both** its apps host and port on the application
itself.

- **Approval workflow.** Submissions have one of three states: `pending`,
  `approved`, or `rejected`. Only `approved` applications appear on the Home and
  team pages. Administrators approve or reject from the management list while a
  submission is `pending`; once an application is `approved` it can no longer be
  rejected — only disabled or deleted. A `rejected` application is retained
  (greyed out) rather than deleted. When a non-self-service owner substantively
  edits an approved application it returns to `pending`.
- **Proxy push workflow.** Approved alias applications push their nginx config
  automatically when an administrator or self-service user changes proxy-relevant
  settings, including enable/disable. Disabling keeps the app's marked nginx
  block in place but comments its directives; enabling writes the active block
  again. Non-self-service owners can request alias or enable/disable changes;
  administrators see those pending changes and push them by approving or by using
  the admin-only **Push** action.
- **Alias-change approval.** Changing the **alias** of an approved application is
  a special case: when a non-self-service owner edits the alias, the application
  keeps serving its **current** alias and configuration while the new alias is
  held as a pending change. An administrator approving the change applies the new
  alias to the live URL and pushes it to the reverse proxy. Administrators and
  self-service users apply alias changes immediately (no staging).
- **Logos.** When creating an application you may upload a small logo (PNG, WebP,
  or JPEG). The image is resized in the browser to a 64-pixel square and stored
  inline as a capped (64 KB) raster data URI (PNG or WebP — a JPEG is converted
  on upload); you may instead paste an absolute image URL. If no logo is
  provided, one is chosen from a small bundled default catalogue: three variants
  for each of a fixed set of known team slugs, and a neutral `generic` set used
  for every other team and for team-less apps. The logo appears small on each
  application card.
- **Self-service.** Each account has a `self_service` flag (administrator
  managed, off by default). Self-service users — and all administrators —
  publish applications immediately, bypassing approval. The flag is shown on the
  Account page and toggled from User Management.
- **Team scope.** Applications are team-scoped, and any signed-in user may share
  an application with **any** team (the team picker lists all teams). A
  non-administrator must scope a submission to at least one team. Sharing broadly
  is still gated by approval: a non-administrator's submission stays `pending`
  until an administrator approves it, so it only appears on another team's page
  once approved (changing the teams of an approved application re-queues it).
- **Link types.** The target type is chosen with radio buttons. A **local alias**
  (the default for new applications) is a bare relative path stored verbatim and
  resolved against the deployment base at render time, so an external reverse
  proxy can map it to an internal service; the create form shows the server URL
  prefix greyed-out before the input so the full resulting URL is visible. An
  alias may contain only **letters, digits, underscores, and dashes** and is at
  most **30 characters** (the requirement is shown next to the field, and a
  leading slash is stripped). A **full URL** is validated as `http`/`https`. Alias links are
  not subject to `http`/`https` validation.
- **Alias authentication.** Local aliases require an AppManager session by
  default. The create/edit form includes a per-app toggle to exclude an alias
  from AppManager authentication when the upstream app has its own auth or is
  safe to expose. Disabling auth removes the alias block's
  `auth_request /api/auth/proxy-check;` and
  `error_page 401 = @appmanager_login;` directives and shows a warning. Admins
  and self-service owners apply this immediately; non-self-service owners stage
  the change for admin approval.
- **Alias upstream.** Each alias application has its own upstream target, shown
  only for alias apps: protocol (`http` by default), server host/IP (prefilled
  from the user's configured apps host/IP when available), mandatory port, and
  optional suffix/path. A read-only preview shows the upstream URL that nginx
  will proxy to. The reverse-proxy settings host is only the SSH target used to
  push config — never the alias upstream. If an approved alias is missing its
  upstream host or port, the push is skipped. When editing an alias, AppManager
  attempts to read the current marker-managed nginx block and prefill the form
  from the deployed alias/upstream/auth settings; if that read fails, stored app
  settings remain editable as a fallback.
- **Owned app shortcuts.** The Home page shows **My Applications** before shared
  applications. Owned app cards include an **Edit** link that opens the app's
  configuration in Application Manager.
- **Team selection.** The team picker offers a **Select all / Clear all** toggle
  in addition to the individual checkboxes.

There are no built-in teams: a clean install starts with **no teams**, and an
administrator creates them under **Settings → Teams** (see below).

## Teams

Teams are administrator-managed from **Settings → Teams** (admin only). Each team
has:

- **Name** — shown on its sidebar button and used to generate its URL slug
  (`/teams/<slug>`). Names allow letters, digits, spaces, `&`, and `-`, are at
  most 40 characters, and must be unique (a name that would collapse to an
  existing team's slug is rejected).
- **Position** — the order in the left sidebar. Reorder teams by **dragging**
  them in the list (accessible **move up / down** buttons do the same); the new
  order is applied to every member's sidebar.
- **Icon** — an optional small icon shown on the team's sidebar button. Pick one
  from a bundled catalogue with a **generic-IT** set (server, database, network,
  cloud, security, dashboard, development, support, storage, containers,
  automation, team) and a **cybersecurity** set (defensive security, offensive
  security, vulnerability, threat intel, DevOps, and security engineering —
  three variants each), or upload a small raster image (PNG, WebP, or JPEG,
  resized and stored as a capped raster like the application logo). When no icon
  is chosen, a neutral default is used.

Renaming a team keeps all existing user and application memberships (they are
stored by the team's stable id). Deleting a team removes it from every user and
application that referenced it.

The team list (names, order, and icons) drives the sidebar, the team pages, and
the team pickers in User Management and the Application Manager; it is readable
by any signed-in user, while creating, editing, reordering, and deleting teams
require an administrator.

## Reverse proxy (nginx)

When **Reverse Proxy Configuration** is set in Settings → General Settings, an
approved **local-alias** application is published to a remote nginx server
automatically. This runs both when an administrator approves a pending
application and when an application is auto-approved on creation
(administrators / self-service users).

Configure in General Settings:

- **NGINX Server Host/IP** — the host the backend connects to over SSH.
- **SSH user** — optional login user; when set the backend connects as
  `user@host`, otherwise as `host` (using the SSH config's default user).
- **NGINX conf file path** — the config file (inside the 443 `server { … }`
  block) where aliases are added.
- **Local SSH key path** — path to a **private key file on this server** used for
  key-based SSH. The key contents are never stored in the database or shown in
  the UI; only the path is kept.
- **AppManager backend host/IP and port reachable from nginx** — the address nginx
  uses for `auth_request` checks back to AppManager. The UI suggests the current
  browser hostname and port `8000`, but the administrator must confirm values that
  are reachable from the nginx host/container network.
- **Alias template** — the nginx `location` block (collapsed by default). The
  placeholders `APPS_SERVER`, `APPS_PORT` and `ALIAS` are substituted on push.
  `APPS_PORT` is the application's own port. `APPS_SERVER` is the application's
  own server (set explicitly via its apps-server dropdown/Custom field, if
  any), otherwise the **owning user's** apps server reference resolved to a
  live, provisioned apps-server server's host at push time (preferring the
  server cloned from the account's referenced template, else the user's first
  apps-server server, else the literal reference as a last resort). The
  **NGINX Server Host/IP** above is only the SSH target used to push the
  config — it is never used as `APPS_SERVER`.

### Protecting direct alias links

Aliases are served by nginx, not by the FastAPI process. To require AppManager
authentication even when a user opens `/some-alias/` directly, nginx must use
`auth_request` to ask AppManager whether the browser has a valid `app_session`
cookie. The default alias template includes the required per-alias directives for
new installs:

```nginx
auth_request /api/auth/proxy-check;
error_page 401 = @appmanager_login;
```

When Reverse Proxy Configuration is saved with nginx SSH settings and the
AppManager backend host/port, AppManager checks the nginx config for a marked
shared auth block and pushes it if it is missing. The injected block is:

```nginx
# >>> appmanager-lite-proxy-auth >>>
location = /api/auth/proxy-check {
    proxy_pass http://<appmanager-backend-host>:<port>/api/auth/proxy-check;
    proxy_set_header Host $host;
    proxy_set_header Cookie $http_cookie;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_pass_request_body off;
    proxy_set_header Content-Length "";
}

location @appmanager_login {
    return 302 /?next=$request_uri;
}
# <<< appmanager-lite-proxy-auth <<<
```

The setup result and log are shown immediately in General Settings. If the marked
block already exists, AppManager leaves it in place and reports that protected
alias auth is already configured. If reload or validation fails, AppManager
restores the backup and shows the nginx error transcript.

AppManager and alias paths should be served from the same domain so the browser
sends the AppManager session cookie to direct alias requests. If TLS terminates
at nginx, configure uvicorn to trust forwarded headers from that proxy, for
example `FORWARDED_ALLOW_IPS=<nginx-ip>`.

Existing deployments keep the alias template stored in the database. To migrate
an existing deployment, save the new AppManager backend host/port in General
Settings so AppManager can push the shared nginx auth block, update the alias
template so each alias location contains the two `auth_request` lines, then
re-push approved alias applications from the Application Manager.

On approval or push the backend (using the system `ssh`/`scp`, key-based,
non-interactive, with timeouts) verifies SSH access, that the conf file exists,
that nginx is running, and that the file is writable; then it backs the file up
(`<conf>-<epoch>-<alias>`), renders the template inside a unique per-application
marker (`# >>> appmanager-lite-app:<id> >>>` … `# <<< appmanager-lite-app:<id> <<<`), removes any
older block with that marker, injects the new block before the file's last `}`,
reloads nginx with `docker exec nginx nginx -s reload` (this version assumes nginx
runs in a docker container named `nginx`), and verifies the reload. Disabled apps
keep their marked block in the remote config, but the rendered nginx directives
are commented out. **If any step fails the original file is restored from the
backup and the nginx error is captured.** The timestamped transcript is stored on
the application and copied to the audit log (no secrets). On the expanded
application card administrators see the push status, a **View push log** button,
and an admin-only **Push** button for approved alias apps. After a push runs
(create, approve, or manual Push) a transient **proxy: …** notice also appears
next to the application name in the manager; it is shown once and clears when you
navigate away (it is independent of the persistent indicator on the expanded
card).

**Deleting an application** removes its alias block from the nginx config: the
backend finds the block by its `appmanager-lite-app:<id>` marker, excises it (backup →
remove → reload → verify, reverting on failure), and audits the result as
`nginx_remove`. Aliases pushed before markers existed have no marker and are
reported as skipped on removal.

## Server provisioning (LXC/VM)

Administrators configure server provisioning under **Settings → Server
Provisioning** (admin only). Each Settings section groups its cards into
**sub-tabs**; Server Provisioning has **Provider**, **Policy**, **Jump
Server**, and **Server Templates**.

- **LXC/VM provider** — currently Proxmox (API-token authentication). Configure
  the Proxmox URL (`PROTO://IP:PORT`), token name, and API key; the key is
  stored **write-only** (never returned by the API, shown in the UI, logged, or
  audited — the UI shows only whether a key is set). A **template name filter**
  and a **templates-only** toggle control which VMs/containers the provider
  offers, and a **Verify TLS certificate** toggle (on by default, with a
  warning when disabled) supports self-signed lab instances. Saving runs a
  connection test; the result is shown with a **View connection log** toggle,
  and after a successful test a verification dropdown lists the matching
  templates read live from Proxmox.
- **Provisioning policy** — enable/disable self-service server provisioning
  (self-service users and administrators; normal users cannot request their own
  servers), the maximum number of servers per user, and the per-user resource
  caps (defaults: 12 CPUs, 24 GB memory, 200 GB disk). Any self-service user
  may modify their own non-admin-managed server's resources within these caps
  — there is no separate opt-in toggle for this. Administrator-made resource
  changes do not count against these limits.
- **Server templates** — register the Proxmox templates (LXC or VM, by VM ID)
  offered to users when creating a server, each with an app-facing name and an
  **SSH key** chosen from the registry (see Remote Access) for later
  customization. Templates are assumed to be preconfigured (SSH keys,
  resources, user). A template can be flagged as an **Apps server** so its name
  is offered in the apps-server dropdowns used during user creation and
  application management (the name is used as the alias upstream host and must
  resolve on the reverse-proxy host). Each registered template has an **Edit**
  action that reopens all of its options (VM ID, name, kind, admin SSH key, main
  OS user, sudo, trusted access, apps-server flag) for modification.
- **Jump server** — optionally onboard users onto a bastion. When enabled with
  a host, **SSH port** (default 22), management user, and registry SSH key,
  creating a user provisions an OS account on the jump server with their public
  key installed, deleting a user removes that key, and regenerating a key
  rotates it there too — always on the account for the configured jump account
  model (the shared jumper account in **shared** mode, the user's own account
  in **per-user** mode), keyed to the owner for provenance. A **Sync users to
  jump server** action backfills
  existing users. All jump operations are best-effort (they never block user
  create, delete, or login) and audited. Changing the jump server's connection
  settings (host, user, port, or key) prompts a reminder to re-run **Sync users
  to jump server**, since existing accounts would otherwise no longer match.
  AppManager connects to the bastion as the **management user** (default
  `root`), which must be privileged enough to create accounts and write keys.
  The **jump account model** decides where users' keys live: **per-user**
  (default) gives each user their own account (named by their derived user ID),
  while **shared** installs every user's key into one shared account. In both
  models the accounts are **hardened** for jump-only use — created with a
  `nologin` shell and each key line prefixed with `restrict,port-forwarding`, so
  they can only be used as a `ProxyJump` hop (no shell, TTY, agent, or X11).
  Note that hardening does not restrict *where* a shared-account user may
  TCP-forward, so **`shared` mode is best for a single tenant or trusted cohort**
  — prefer `per_user` for multi-tenant setups.
  Switching the account model re-syncs every user, so the UI requires the
  administrator to acknowledge the re-sync first; if any user fails to sync the
  change is reverted automatically and the error is reported.
  By default the generated SSH config bundle addresses the bastion at the same
  management host/port. If the bastion is managed over one interface but users
  connect over another (e.g. private vs. public), turn off **Use jumpserver
  admin config in SSH config bundle** and supply a separate **bundle host** and
  **bundle port**; that address is only written into users' downloaded SSH
  config (AppManager never dials it), so changing it needs no re-sync when it
  points to the same jump server.

Every SSH public key AppManager installs on a remote host — on the jump server,
on a user's servers, and across the trusted-access mesh — is stamped with an
identifying comment on the `authorized_keys` line so its origin is clear:
`AppManager-managed:<user_id>` for a user's own key and
`AppManager-trusted:<user_id>` for keys shared between a user's servers. Installs
are idempotent by the key blob (any earlier copy of the same key is replaced, so
re-syncing never leaves duplicates), and key removal/rotation still match on the
blob, so the comment is purely informational.

When creating a user, administrators can also **auto-provision servers**: the
Create user card lists every server template with a toggle (all enabled by
default), and one server is created per selected template, named
`TEMPLATE_NAME-USERID`. Provisioning runs synchronously but is best-effort — a
missing provider, unknown template, or clone failure yields a per-template
result (shown with the temporary credentials) and never blocks user creation.
While the create request is in flight, a **progress card** appears at the top of
the Users tab (above the create card) listing the servers being provisioned.

Each server template also carries provisioning options (both sudo and trusted
access default on):

- **Main user** — when set, the user's SSH key is installed only for that OS
  account on their server (blank falls back to the user's derived ID). Because
  this is an admin-configured, single-value setting, that account defaults to
  a `/bin/bash` login shell: it is created with bash if the template image
  doesn't already have it, or its shell is normalized to bash if it differs
  (only when the bash binary is present on the server). This auto-create/
  normalize behavior only ever applies to this configured main user — never to
  extra OS usernames a caller lists explicitly when creating a server, so a
  self-service request can't be used to conjure arbitrary accounts.
- **Sudo access** — adds the main user to the server's sudo/wheel group.
- **Trusted SSH access** — establishes a full SSH mesh across the user's
  servers that share the same main user: each server generates its own keypair
  locally (private keys never leave the servers or touch AppManager) and every
  server's public key is installed on the others, so the user's servers can
  reach each other. **A main OS user is required** — trusted access needs a
  shared OS account to key, so a template with trusted access enabled but no
  main user forms no mesh (the template editor warns about this). The mesh is
  reconciled whenever a trusted server is created, a VM's IP is entered, and
  lazily when the server list is loaded if a server acquired its IP only after
  creation (so an LXC whose address arrived late is still joined). Every
  reachable group of the user's trusted servers sharing a main user is meshed,
  even across different templates. The mesh reaches each server as **root**
  using that server's template admin key, so the **template image must
  authorize the admin key for root** (`PermitRootLogin` + the key in root's
  `authorized_keys`); when it doesn't, the mesh records a clear, actionable
  error instead of silently doing nothing. Each per-server mesh outcome
  (established / skipped / failed, with the reason) is recorded on the server's
  provisioning log and in the audit trail (`server_mesh`). Transient
  "sshd not ready yet" conditions are retried briefly and otherwise re-tried on
  the next reconcile.

## Remote Access (SSH key registry)

Under **Settings → Remote Access**, administrators manage a registry of SSH
keys used across the app. A key is either a **reference to a key file path** on
the server, or a **pasted private key stored encrypted at rest** in the
database (AES via Fernet, using a master key from `APP_MASTER_KEY` or an
auto-generated `data/master.key`). Pasted keys are write-only — never returned,
logged, or echoed in errors; the registry shows only the name, kind, and (for
stored keys) the public key and fingerprint. Every place that needs an SSH key
(reverse proxy, server templates, jump server) selects a registered key by name
from a dropdown. A key that is still in use cannot be deleted, but it can be
**edited** in place — rename it, change the file path, switch between path and
stored kinds, or paste a replacement private key (leaving the key blank keeps
the current one). Replacing a stored key re-encrypts it, recomputes its public
key and fingerprint, and refreshes any materialized copy. Per-user SSH
keypairs are also stored encrypted at rest with the same master key.

### User servers

Each user can have multiple servers, shown as small `NAME - IP` cards in their
User Management card and, for their own servers, in the top-level **Servers**
section. Administrators can add a server to any user; self-service users can
create their own (when self-service provisioning is enabled); normal users
cannot request servers. The Servers section is the single place a user creates
and manages their own servers (see [Servers section](#servers-section) below);
it replaced the former Account → My servers card.

Creating a server picks a registered template and a **name suffix**: the full
server name always carries a static prefix, so it is composed as
`<template-slug>-<owner-id>-<suffix>` (the same convention as auto-provisioned
servers). Server names are **globally unique** (case-insensitive) — if the
composed name is already taken the create is rejected and you are asked to
choose a different suffix. The create form shows the fixed prefix, a live
preview of the full name, and how many suffix characters remain (names are
capped at 63). AppManager then
clones it in Proxmox (full clone, next free VMID) and — for **LXC** — starts
the container and records its IP address. The address is read from **inside the
container** over SSH (the DHCP/network address the guest actually holds), and
adopted only when Proxmox independently attributes the same address to that
guest; otherwise the hypervisor-reported address is kept. A **VM** is cloned
only: the operator is guided to configure it in Proxmox and enter its IP
manually on the server card. An optional toggle installs the owner's SSH
public key on the new server for a comma-separated list of OS users (default:
the owner's user ID), connecting with the template's admin SSH key; only the
public key ever leaves this host. Every creation stores a timestamped,
secret-free transcript on the server record (**View log**, admin-only — the
provisioning log is hidden from non-admin users in both the UI and the API),
successes show a
green confirmation, and everything is audited. A record is marked `failed`
only when no guest was produced; if the clone succeeded but a later step
errored (IP discovery, key installation), the record stays `created` — with
the error in its log — because the guest exists, consumes capacity, and
counts against quotas.

When creating a server, the form shows small **resource-usage bars** just above
the template picker — the user's committed servers, CPUs, memory, and disk
against their per-user limits, coloured green/amber/red as a limit is
approached. Administrators (who are exempt from per-user quotas) see a short
"limits are not enforced" note instead.

Quotas apply to non-administrator creators: the per-user server count and the
summed CPU/memory/disk of all the user's non-failed servers are checked against
the provisioning policy (template resources are read from Proxmox before
cloning). All of a user's servers count toward these figures — including servers
created by an administrator and those auto-provisioned at account creation — so
the usage bars reflect the resources actually committed. Administrators
themselves remain exempt from per-user caps when they create or modify servers
(admin-initiated actions are never quota-gated). Resource changes are applied
via the Proxmox API: **LXC** servers support CPU, memory, and grow-only disk;
**VM** servers support CPU and memory only (VM disk resize is out of scope for
self-service edits, since it isn't safely automatable) and a **reboot is
advised** afterwards for the change to take effect. Any self-service user may
change resources on **their own** server — including servers an administrator
provisioned for them — within their remaining quota; no administrator opt-in is
required. (Servers an administrator sized stay exempt from the user's quota, but
that no longer prevents the owner from resizing them.)

Each server card in the **Servers** section always shows the server's assigned
CPU/memory/disk (or "Resources: not recorded" when the specs were never
captured, e.g. for reference servers). Specs missing from older records are
lazily read back from Proxmox and stored the next time the list loads. On an
eligible server (own, or any server for an admin; LXC or VM; not failed or
pending deletion), a **"Change resources"** button opens an editor bounded by
the per-user limits; LXC disk can only be grown, and VMs hide the disk field
entirely and show a reboot reminder after saving. A separate **Reboot** button
(with a confirm step) is available on any eligible LXC or VM server to its
self-service owner or an administrator.

Deleting a server is **deferred and reversible for 24 hours**. After an
explicit "this is permanent" confirmation, the server enters a **deletion
pending** state with a countdown and a **Cancel deletion** action; owners and
administrators can cancel any time during the grace window. Once the window
elapses, a sweep force-stops and **destroys the LXC/VM** in Proxmox (the guest
and its referenced disks; intentionally-retained detached volumes are left
alone) and removes the record. The sweep has no background scheduler — it runs
at startup and opportunistically on server-list requests, bounded so it never
holds up a request. If the automatic destroy fails, the server is removed from
the owner's list but kept in the **administrator's** list with a red error
notice and the full log; the administrator (who can check Proxmox directly) can
then **force-remove** the record even when the destroy could not be confirmed.

### Servers section

The **Servers** entry in the sidebar (below Account) is where a user creates and
manages their servers. A **Create server** card sits at the top (when the user
may provision), followed by servers grouped by owner. Administrators see and
manage every user's servers; a regular user sees and manages only their own.
Each server card shows its assigned resources next to four compact usage charts
— **CPU, memory, disk, and network** — drawn as small sparklines from Proxmox's
historical `rrddata`, with a **timeframe** selector (last hour, day, or week).
Charts are loaded per server as the list renders, so a long list stays
responsive and one unreachable server never blocks the rest; a server with no
running guest (or when the provider is unconfigured) simply shows a short "no
stats" note.

Each card also carries its management actions, subject to the same rules
enforced by the API: **Change resources** and **Reboot** on eligible servers
(the self-service owner within their quota, or an administrator on any server),
and **Delete** (self-service owner or administrator). Resource editing follows
the LXC/VM rules described under [User servers](#user-servers) (LXC
CPU/memory/grow-only disk; VM CPU/memory with a reboot advisory). A regular user
never sees action buttons on another user's servers.

## Home

The Home page shows two groups of applications:

- **Available shared applications** — everything visible to the account by team
  scope (created by administrators or other users), excluding the account's own
  apps.
- **My Applications** — the apps the account itself published.

Each card shows a small team-scope badge for the team(s) the application belongs
to, so it is clear where a tool comes from.

## Themes

The portal includes four visual themes, chosen by each user from the compact
theme selector in the **Profile** card of the **Account** section:

- **Dark modern** — the default layered dark theme.
- **Light** — a clear light interface for bright environments.
- **Energy** — a neutral dark-grey theme with brighter borders and an amber accent.
- **Classic** — the original portal appearance.

A user's theme is **saved to their account** (per user, not per browser), so it
follows them across devices and does not affect other users who sign in on the
same browser. An administrator sets the deployment's **Default theme** in
**General Settings**; it is delivered with the session (even before sign-in, so
the login form uses it) and applied to users who have not chosen their own —
an explicit user choice always takes precedence, and signing out returns the
interface to the admin default.

## About

The **About** page (sidebar, after Settings) shows the application
name, a link to the source repository on GitHub, the build version with its
commit hash, the development team, and any administrator-configured
collaborators. The version, commit, and development-team list are injected at
build time from `package.json` and the git commit history; because the
lifecycle script rebuilds the frontend when the commit changes, a `start`/
`restart` after a new commit refreshes them automatically (or use `--rebuild`).

The **development team** is derived only from the repository's commit authors,
canonicalized through `.mailmap` to GitHub handles, and displayed once per
handle as a link to that profile (for example, `@jusafing`). This keeps multiple
Git names or emails for one contributor from producing duplicate entries.
**Collaborators** are a separate, internal list set by administrators in
**Settings → General Settings → About Collaborators** (a name textbox with Add,
each entry removable, then Save) and are shown as their own row beneath the
development team. They are stored server-side and update immediately on save.

## Audit log

Administrators get an **Audit log** in the sidebar (after Settings)
that records actions performed in the portal, grouped into three tabs:

- **Application Management** — application create/request, approve, reject,
  update, delete, alias-change request/approval, and reverse-proxy push/remove.
- **User activity** — sign-in (success and failure), sign-out, password changes,
  user create/update/delete/password-reset, and SSH-key regeneration and
  private-key downloads (metadata only; never key material).
- **System** — backend lifecycle (startup, shutdown, first-run administrator
  creation, authentication disabled), settings updates (branding and
  reverse-proxy), and team management (create, update, delete, reorder).

Events are stored in the `audit_log` table (created automatically on startup),
so they survive restarts; the view shows the most recent entries per category.
The text log (`logs/app.log`) is retained separately for operations. As with all
logging, secrets (passwords, hashes, session identifiers, CSRF tokens) are never
recorded in audit entries.

## Configuration

All settings are environment variables with the `APP_` prefix:

| Variable | Default | Purpose |
|---|---|---|
| `APP_BASE_DIR` | _(project root)_ | Base directory for resolving relative data, frontend, and log paths. |
| `APP_BASE_PREFIX` | _(empty)_ | Optional subpath prefix, e.g. `/home`; only needed for strict reverse-proxy setups (the app otherwise works behind a proxy via relative paths). |
| `APP_ENABLE_AUTH` | `1` | Enable authentication and access control. |
| `APP_DEV` | `0` | Dev mode (enables API docs; relaxes cookie security). |
| `APP_SECURE_COOKIES` | `1` (off in dev) | Set the `Secure` flag on the session cookie. |
| `APP_SESSION_TTL_SECONDS` | `43200` | Session lifetime (12 hours). |
| `APP_DATA_DIR` | `./data` | SQLite database and runtime data location. |
| `APP_DB_PATH` | `./data/app.db` | SQLite database file. |
| `APP_FRONTEND_DIST` | `./frontend/dist` | Built frontend to serve. |
| `APP_LOG_DIR` | `./logs` | Directory for the consolidated log file. |
| `APP_LOG_FILE` | `app.log` | Consolidated log file name. |
| `APP_LOG_LEVEL` | `INFO` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `APP_LOG_TO_FILE` | `1` | Write logs to the file (the console is always on). |

The startup script sets `APP_BASE_PREFIX` from `--base-prefix` for you.

## Logging

The application writes a single consolidated log to `logs/app.log` (override the
location with `APP_LOG_DIR` / `APP_LOG_FILE`). The same records are produced
however the server is launched — backgrounded by the lifecycle script, in the
foreground via `start --dev`, or under a bare `uvicorn` — because uvicorn's
access and error logs are routed through the application's handlers. The file
rotates at 5 MiB and keeps five backups. Logs also go to the console; in
background mode the script discards the process's own streams so lines are not
duplicated.

Sign-in success and failure, logout, password changes, user and application
create/update/delete, and CSRF/authorization failures are recorded. Secrets —
passwords, generated passwords, hashes, session identifiers, and CSRF tokens —
are never logged.

## Deployment behind a reverse proxy

In most setups no special configuration is needed: the frontend's API and asset
URLs are all relative, so the same build works at the site root or behind a
reverse proxy on any subpath. Bind the app to a private address and forward to
it from the proxy — that is usually enough.

`--base-prefix` is **optional**. It is only needed for a strict reverse-proxy
configuration that requires the app to know its public subpath so it can emit
correct absolute links (for example server-side redirects or generated links).
For a portal served at `https://<server>/home/` where the proxy forwards
`/home/...` to the app as `/...`:

```bash
./appmanager-lite start --bind 0.0.0.0:8000 --base-prefix /home
```

When set, the backend injects a matching `<base href>` so relative links resolve
under the prefix. Without it, the application still works behind the proxy
normally.

## Development

```bash
scripts/check.sh           # backend byte-compile + frontend type-check
scripts/test.sh            # backend pytest + frontend vitest
scripts/security-check.sh  # brand-genericization, ignore hygiene, dep audits
```

Backend-only commands (run inside `backend/`):

```bash
uv sync                                  # install/refresh deps from uv.lock
uv run pytest                            # backend tests
uv run uvicorn app.main:app --reload     # dev server with autoreload
```

Frontend-only commands (run inside `frontend/`):

```bash
npm run dev        # Vite dev server, proxies /api to 127.0.0.1:8000
npm run typecheck
npm test
npm run build
```

## Branding

The application name and logo are **configured by an administrator** in
**Settings → General Settings → Application Basic Information**. The name and
logo are stored in the deployment's settings and are delivered with every
session response, so the sign-in page, header, favicon, and About page all use
the deployment's own branding (even before authentication).

- **Name** — free text shown in the header, sign-in page, and About page.
- **Logo** — upload a PNG, WebP, or JPEG; it is resized in the browser and
  stored inline as a capped, raster-only data URI (PNG or WebP — a JPEG is
  converted on upload). Until a logo is configured, a neutral bundled fallback
  asset (`frontend/public/app-logo.svg`) is used.

The first-login setup wizard prompts a new deployment's administrator to set
these values before continuing.

## Attribution

Portions of the authentication and user-management design reuse patterns from an
Apache-2.0 licensed open-source project. See `NOTICE` and
`THIRD-PARTY-NOTICES.md` for details.

---

_Built in part with OpenCode and vibecoding, following simple but solid
development practices. Review and use with caution._
