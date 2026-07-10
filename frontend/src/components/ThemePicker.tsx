import { THEMES, useTheme } from "../theme";

export function ThemePicker(props: { compact?: boolean; hideLabel?: boolean }) {
  const { theme, setTheme } = useTheme();
  return (
    <label className={props.compact ? "theme-picker compact" : "theme-picker"}>
      {!props.hideLabel && <span>Theme</span>}
      <select
        aria-label="Theme"
        value={theme}
        onChange={(event) => setTheme(event.target.value as typeof theme)}
      >
        {THEMES.map((item) => (
          <option key={item.id} value={item.id}>
            {item.label}
          </option>
        ))}
      </select>
    </label>
  );
}
