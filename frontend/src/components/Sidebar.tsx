import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import type { Application, Team } from "../types";
import { teamSlug } from "../teams";
import { TeamIcon } from "./TeamIcon";
import { resolveIconSrc } from "../lib/links";
import {
  BookIcon,
  FrameIcon,
  GridIcon,
  HomeIcon,
  InfoIcon,
  ListIcon,
  ServerIcon,
  SlidersIcon,
  UserIcon,
} from "./icons";

/**
 * Primary navigation. Sections mirror the product spec: Main, Teams (filtered to
 * the teams the account may see, each shown as a button-card with its own
 * icon), Embedded apps (in-portal iframes), and Config. When `collapsed`, labels
 * and section headings are hidden by CSS, leaving icon-only navigation. App
 * Manager is available to every signed-in user; Settings and the admin tools
 * (App Statistics, Audit log) are administrator-only.
 */
export function Sidebar(props: {
  teams: readonly Team[];
  embeddedApps: readonly Application[];
  collapsed: boolean;
  isAdmin: boolean;
}) {
  const { teams, embeddedApps, collapsed, isAdmin } = props;
  return (
    <nav
      className={collapsed ? "sidebar collapsed" : "sidebar"}
      aria-label="Primary"
    >
      <Section title="Main">
        <SideLink to="/" end icon={<HomeIcon />} label="Home" collapsed={collapsed} />
      </Section>

      <Section title="Teams">
        {teams.length === 0 ? (
          <p className="sidebar-empty">No teams assigned</p>
        ) : (
          teams.map((team) => (
            <SideLink
              key={team.id}
              to={`/teams/${teamSlug(team.name)}`}
              icon={<TeamIcon icon={team.icon} size={18} />}
              label={team.name}
              collapsed={collapsed}
              card
            />
          ))
        )}
      </Section>

      <Section title="Embedded apps">
        {embeddedApps.length === 0 ? (
          <p className="sidebar-empty">No embedded apps</p>
        ) : (
          <div className="sidebar-scroll">
            {embeddedApps.map((app) => {
              const icon = resolveIconSrc(app.icon_url);
              return (
                <SideLink
                  key={app.id}
                  to={`/embedded/${app.id}`}
                  icon={
                    icon ? (
                      <img
                        className="embedded-nav-icon"
                        src={icon}
                        alt=""
                        width={18}
                        height={18}
                      />
                    ) : (
                      <FrameIcon />
                    )
                  }
                  label={app.name}
                  collapsed={collapsed}
                />
              );
            })}
          </div>
        )}
      </Section>

      <Section title="Config">
        <SideLink
          to="/account"
          icon={<UserIcon />}
          label="Account"
          collapsed={collapsed}
        />
        <SideLink
          to="/app-manager"
          icon={<GridIcon />}
          label="App Manager"
          collapsed={collapsed}
        />
        <SideLink
          to="/servers"
          icon={<ServerIcon />}
          label="Servers"
          collapsed={collapsed}
        />
        <SideLink
          to="/user-guide"
          icon={<BookIcon />}
          label="User Guide"
          collapsed={collapsed}
        />
        {isAdmin && (
          <SideLink
            to="/settings"
            icon={<SlidersIcon />}
            label="Settings"
            collapsed={collapsed}
          />
        )}
        {isAdmin && (
          <SideLink to="/app-statistics" icon={<ListIcon />} label="App Statistics" collapsed={collapsed} />
        )}
        {isAdmin && (
          <SideLink
            to="/audit"
            icon={<ListIcon />}
            label="Audit log"
            collapsed={collapsed}
          />
        )}
        <SideLink
          to="/about"
          icon={<InfoIcon />}
          label="About"
          collapsed={collapsed}
        />
      </Section>
    </nav>
  );
}

function Section(props: { title: string; children: ReactNode }) {
  return (
    <div className="sidebar-section">
      <p className="sidebar-heading">{props.title}</p>
      <div className="sidebar-links">{props.children}</div>
    </div>
  );
}

function SideLink(props: {
  to: string;
  icon: ReactNode;
  label: string;
  collapsed: boolean;
  end?: boolean;
  card?: boolean;
}) {
  return (
    <NavLink
      to={props.to}
      end={props.end}
      aria-label={props.label}
      title={props.collapsed ? props.label : undefined}
      className={({ isActive }) =>
        ["nav-item", props.card ? "team-card" : "", isActive ? "active" : ""]
          .filter(Boolean)
          .join(" ")
      }
    >
      <span className="nav-icon">{props.icon}</span>
      <span className="nav-label">{props.label}</span>
    </NavLink>
  );
}
