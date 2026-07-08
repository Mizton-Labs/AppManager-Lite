import { ReactNode, useState } from "react";

export interface SubTab {
  id: string;
  label: string;
  render: () => ReactNode;
}

/**
 * A lightweight sub-tab strip for grouping the cards within a Settings
 * section, reusing the same visual pattern as the top-level Settings tabs
 * and the Audit category tabs.
 */
export function SubTabs(props: {
  tabs: SubTab[];
  ariaLabel: string;
  /** Id of the tab shown first; defaults to the first tab. */
  initialTab?: string;
}) {
  const { tabs, ariaLabel, initialTab } = props;
  const [active, setActive] = useState(
    initialTab && tabs.some((t) => t.id === initialTab)
      ? initialTab
      : tabs[0]?.id ?? "",
  );
  const current = tabs.find((t) => t.id === active) ?? tabs[0];
  if (!current) return null;

  return (
    <div className="subtabs-wrap">
      <nav className="tabs subtabs" aria-label={ariaLabel}>
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={tab.id === active ? "tab active" : "tab"}
            aria-current={tab.id === active}
            onClick={() => setActive(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </nav>
      {current?.render()}
    </div>
  );
}
