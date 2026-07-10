import { getAppName } from "../branding";

/**
 * In-app User Guide (issue_025). A friendly, task-oriented walkthrough for
 * everyday users, with clearly labeled sections for administrators. Content is
 * hand-authored JSX (no markdown/mermaid dependency); the diagrams are simple
 * inline SVG flows drawn with currentColor so they follow the active theme.
 */
export function UserGuideView() {
  const app = getAppName();
  return (
    <div className="stack wide user-guide">
      <header className="view-head">
        <h1>User guide</h1>
        <p className="muted">
          How to use {app} — from signing in and launching apps to managing your
          servers. Administrator-only tasks are marked below.
        </p>
      </header>

      <nav className="card guide-toc" aria-label="User guide contents">
        <strong>On this page</strong>
        <ul>
          <li><a href="#guide-start">Getting started</a></li>
          <li><a href="#guide-apps">Launching applications</a></li>
          <li><a href="#guide-account">Your account &amp; SSH access</a></li>
          <li><a href="#guide-servers">Your servers</a></li>
          <li><a href="#guide-admin">For administrators</a></li>
        </ul>
      </nav>

      <section className="card guide-section" id="guide-start">
        <h2>Getting started</h2>
        <p>
          Sign in with the username (your email address) and password an
          administrator gave you. On first sign-in you'll be asked to set a new
          password.
        </p>
        <ul className="guide-steps">
          <li>
            <strong>Home</strong> shows the applications available to you,
            grouped into the ones you own and the ones shared with your teams.
          </li>
          <li>
            The <strong>sidebar</strong> on the left navigates between Home, your
            teams, Account, Servers, this User guide, and (for admins) Settings.
          </li>
          <li>
            Use the collapse control on the sidebar to give the page more room.
          </li>
        </ul>
      </section>

      <section className="card guide-section" id="guide-apps">
        <h2>Launching applications</h2>
        <p>
          Each application on the Home page is a card. Click a card to open the
          application in a new tab. Applications come in two kinds:
        </p>
        <ul className="guide-steps">
          <li>
            <strong>Link</strong> apps open an external URL directly.
          </li>
          <li>
            <strong>Alias</strong> apps are served through {app}'s reverse proxy
            at a friendly path on this site, forwarding to the app's server and
            port behind the scenes.
          </li>
        </ul>
        <GuideFigure
          caption="How an alias application reaches you"
          diagram={AliasFlow}
        />
      </section>

      <section className="card guide-section" id="guide-account">
        <h2>Your account &amp; SSH access</h2>
        <p>
          Open <strong>Account</strong> from the sidebar to manage your profile:
        </p>
        <ul className="guide-steps">
          <li>Change your password and pick your preferred colour theme.</li>
          <li>
            View your <strong>SSH key</strong> and download your{" "}
            <strong>connection bundle</strong> — a zip with a ready-to-use SSH
            config, your key, and a <code>connect_server_*.sh</code> helper per
            server. The scripts run in place from the unzipped folder.
          </li>
        </ul>
      </section>

      <section className="card guide-section" id="guide-servers">
        <h2>Your servers</h2>
        <p>
          The <strong>Servers</strong> section lists the servers assigned to you,
          each with live CPU, memory, disk, and network usage charts.
        </p>
        <ul className="guide-steps">
          <li>
            <strong>Add server</strong> (when enabled for your account) creates a
            new server from a template.
          </li>
          <li>
            <strong>Change resources</strong> adjusts CPU/memory (and disk for
            LXC) within the limits your administrator set.
          </li>
          <li>
            <strong>Reboot</strong> restarts a server; <strong>Delete</strong>{" "}
            removes it after a confirmation and a short grace period.
          </li>
          <li>
            Use the filter box to find a server by name, host, IP, or template.
          </li>
        </ul>
      </section>

      <section className="card guide-section" id="guide-admin">
        <h2>
          For administrators <span className="guide-admin-badge">admin</span>
        </h2>
        <p className="muted">
          These tasks are available to administrator accounts under{" "}
          <strong>Settings</strong> and the management sections.
        </p>

        <h3>User management</h3>
        <ul className="guide-steps">
          <li>
            <strong>Add user</strong> creates an account (username = email) and
            can auto-provision servers from templates.
          </li>
          <li>
            User cards are collapsed by default; click <strong>Expand</strong> to
            see a user's teams and servers, or <strong>Edit</strong> to change
            role, teams, apps server, and self-service.
          </li>
          <li>Filter the list by username, id, role, or team.</li>
        </ul>

        <h3>Application management &amp; the reverse proxy</h3>
        <p>
          Applications follow a simple lifecycle. After you change an approved
          alias's configuration, <strong>save it and then push it to the reverse
          proxy</strong> so the change goes live — a highlighted{" "}
          <strong>Push to reverse proxy</strong> button appears next to Save when
          a push is needed.
        </p>
        <GuideFigure
          caption="Application lifecycle"
          diagram={AppLifecycleFlow}
        />

        <h3>Servers &amp; provisioning policy</h3>
        <ul className="guide-steps">
          <li>
            Configure the LXC/VM <strong>provider</strong> (Proxmox), then select
            the <strong>realms</strong> and an optional <strong>pool prefix</strong>.
          </li>
          <li>
            Register <strong>server templates</strong> and set per-user{" "}
            <strong>quotas</strong>.
          </li>
          <li>
            With <strong>Add servers to User Pool</strong> enabled, every created
            server is added to its owner's Proxmox pool (created if missing).
          </li>
        </ul>
        <GuideFigure
          caption="What happens when a server is created"
          diagram={ProvisionFlow}
        />

        <h3>Branding &amp; settings</h3>
        <p>
          Set the application name and logo, the default theme, teams, and the
          jump-server configuration under <strong>Settings</strong>.
        </p>
      </section>
    </div>
  );
}

function GuideFigure(props: {
  caption: string;
  diagram: (label: string) => React.ReactNode;
}) {
  return (
    <figure className="guide-figure">
      {props.diagram(props.caption)}
      <figcaption className="muted">{props.caption}</figcaption>
    </figure>
  );
}

/** A boxed step with an optional trailing arrow, for the inline flow diagrams. */
function FlowStep(props: { x: number; label: string; sub?: string }) {
  return (
    <g transform={`translate(${props.x} 0)`}>
      <rect x="0" y="8" width="120" height="44" rx="8" />
      <text x="60" y={props.sub ? 30 : 34} textAnchor="middle" className="guide-flow-label">
        {props.label}
      </text>
      {props.sub && (
        <text x="60" y="42" textAnchor="middle" className="guide-flow-sub">
          {props.sub}
        </text>
      )}
    </g>
  );
}

function FlowArrow(props: { x: number }) {
  return (
    <path
      d={`M${props.x} 30 h22`}
      markerEnd="url(#guide-arrow)"
      className="guide-flow-arrow"
    />
  );
}

function FlowSvg(props: {
  width: number;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <svg
      className="guide-flow"
      viewBox={`0 0 ${props.width} 60`}
      role="img"
      aria-label={props.label}
      preserveAspectRatio="xMidYMid meet"
    >
      <defs>
        <marker
          id="guide-arrow"
          viewBox="0 0 10 10"
          refX="8"
          refY="5"
          markerWidth="6"
          markerHeight="6"
          orient="auto-start-reverse"
        >
          <path d="M0 0 L10 5 L0 10 z" />
        </marker>
      </defs>
      {props.children}
    </svg>
  );
}

function AliasFlow(label: string) {
  return (
    <FlowSvg width={560} label={label}>
      <FlowStep x={0} label="You" sub="browser" />
      <FlowArrow x={120} />
      <FlowStep x={142} label={getAppName()} sub="reverse proxy" />
      <FlowArrow x={262} />
      <FlowStep x={284} label="Apps server" sub="host:port" />
      <FlowArrow x={404} />
      <FlowStep x={426} label="App" />
    </FlowSvg>
  );
}

function AppLifecycleFlow(label: string) {
  return (
    <FlowSvg width={560} label={label}>
      <FlowStep x={0} label="Create" />
      <FlowArrow x={120} />
      <FlowStep x={142} label="Approve" sub="admin" />
      <FlowArrow x={262} />
      <FlowStep x={284} label="Push" sub="to proxy" />
      <FlowArrow x={404} />
      <FlowStep x={426} label="Live" />
    </FlowSvg>
  );
}

function ProvisionFlow(label: string) {
  return (
    <FlowSvg width={560} label={label}>
      <FlowStep x={0} label="Template" />
      <FlowArrow x={120} />
      <FlowStep x={142} label="Clone guest" sub="Proxmox" />
      <FlowArrow x={262} />
      <FlowStep x={284} label="Get IP" />
      <FlowArrow x={404} />
      <FlowStep x={426} label="Add to pool" sub="+ SSH mesh" />
    </FlowSvg>
  );
}
