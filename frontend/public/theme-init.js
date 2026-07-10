(() => {
  // Prepaint default. The real per-user/admin-default theme is applied once the
  // session loads (see theme.tsx); this only avoids an unstyled first paint.
  document.documentElement.dataset.theme = "dark-modern";
})();
