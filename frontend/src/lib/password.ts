// Mirrors the server-side password policy (app/security.py) so the UI can give
// immediate feedback. The server remains the source of truth and re-validates.

export const MIN_PASSWORD_LENGTH = 12;

export function passwordIssues(password: string): string[] {
  const issues: string[] = [];
  if (password.length < MIN_PASSWORD_LENGTH) {
    issues.push(`Use at least ${MIN_PASSWORD_LENGTH} characters.`);
  }
  if (!/[a-z]/.test(password)) {
    issues.push("Add a lowercase letter.");
  }
  if (!/[A-Z]/.test(password)) {
    issues.push("Add an uppercase letter.");
  }
  if (!/[0-9]/.test(password)) {
    issues.push("Add a digit.");
  }
  return issues;
}

export function isPasswordValid(password: string): boolean {
  return passwordIssues(password).length === 0;
}
