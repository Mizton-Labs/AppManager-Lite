import type { ApiUser, Application, Team } from "../types";

/** Build an {@link Application} for tests; override only what a case cares about. */
export function makeApp(overrides: Partial<Application> = {}): Application {
  return {
    id: 1,
    name: "Hunt Workbench",
    description: "Run hunting queries.",
    url: "https://example.com/hunt",
    url_type: "url",
    icon_url: "",
    teams: ["Threat Hunting"],
    is_active: true,
    approval_status: "approved",
    sort_order: 0,
    created_by: null,
    publisher_team: "",
    ...overrides,
  };
}

/** Build an {@link ApiUser} for tests; override only what a case cares about. */
export function makeUser(overrides: Partial<ApiUser> = {}): ApiUser {
  return {
    id: 1,
    username: "user",
    role: "user",
    is_active: true,
    must_change_password: false,
    self_service: false,
    apps_server: "",
    apps_server_ip: "",
    teams: [],
    ...overrides,
  };
}

/** Build a {@link Team} for tests; override only what a case cares about. */
export function makeTeam(overrides: Partial<Team> = {}): Team {
  return {
    id: 1,
    name: "Red Team",
    sort_order: 0,
    icon: "",
    ...overrides,
  };
}
