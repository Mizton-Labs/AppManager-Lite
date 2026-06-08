import { resolveIconSrc } from "../lib/links";
import { teamIconOrDefault } from "../teamIcons";

/**
 * Render a team's small icon at a given size.
 *
 * Bundled catalogue SVGs are stroke icons authored with `currentColor`, so they
 * are drawn as a CSS mask over the current text colour -- this lets the sidebar
 * tint them with the brand/active colour like the other glyphs. Uploaded raster
 * icons (data URIs) and absolute image URLs are rendered as a normal `<img>`.
 */
export function TeamIcon(props: {
  icon: string | null | undefined;
  size?: number;
}) {
  const size = props.size ?? 18;
  const value = teamIconOrDefault(props.icon);
  const src = resolveIconSrc(value);
  const isRaster = value.startsWith("data:") || /^https?:\/\//i.test(value);

  if (isRaster) {
    return (
      <img
        className="team-icon-img"
        src={src}
        alt=""
        width={size}
        height={size}
      />
    );
  }

  // Mask the monochrome SVG so it adopts `currentColor` (theme/active tint).
  return (
    <span
      className="team-icon-mask"
      aria-hidden="true"
      style={{
        width: size,
        height: size,
        WebkitMaskImage: `url(${src})`,
        maskImage: `url(${src})`,
      }}
    />
  );
}
