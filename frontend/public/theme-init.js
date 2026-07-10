(() => {
  const key = "appmanager-lite.theme";
  const allowed = new Set(["dark-modern", "light", "energy", "classic"]);
  let theme = "dark-modern";
  try {
    const stored = window.localStorage.getItem(key);
    if (allowed.has(stored)) theme = stored;
  } catch {
    // Keep the default when browser storage is unavailable.
  }
  document.documentElement.dataset.theme = theme;
})();
