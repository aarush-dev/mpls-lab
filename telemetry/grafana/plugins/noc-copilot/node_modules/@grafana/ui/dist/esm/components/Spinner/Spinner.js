import { cx, css } from '@emotion/css';
import React__default from 'react';
import SVG from 'react-inlinesvg';
import '@grafana/data';
import { useStyles2 } from '../../themes/ThemeContext.js';
import 'micro-memoize';
import '@emotion/react';
import 'tinycolor2';
import '../../utils/skeleton.js';
import { isIconSize } from '../../types/icon.js';
import { t } from '../../utils/i18n.js';
import { Icon } from '../Icon/Icon.js';
import { getIconSubDir, getIconRoot } from '../Icon/utils.js';

const Spinner = ({
  className,
  inline = false,
  iconClassName,
  style,
  size = "md"
}) => {
  const styles = useStyles2(getStyles);
  const deprecatedStyles = useStyles2(getDeprecatedStyles, size);
  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const iconName = prefersReducedMotion ? "hourglass" : "spinner";
  if (typeof size !== "string" || !isIconSize(size)) {
    const iconRoot = getIconRoot();
    const iconName2 = "spinner";
    const subDir = getIconSubDir(iconName2, "default");
    const svgPath = `${iconRoot}${subDir}/${iconName2}.svg`;
    return /* @__PURE__ */ React__default.createElement(
      "div",
      {
        "data-testid": "Spinner",
        style,
        className: cx(
          {
            [styles.inline]: inline
          },
          deprecatedStyles.wrapper,
          className
        )
      },
      /* @__PURE__ */ React__default.createElement(
        SVG,
        {
          src: svgPath,
          width: size,
          height: size,
          className: cx("fa-spin", deprecatedStyles.icon, className),
          style
        }
      )
    );
  }
  return /* @__PURE__ */ React__default.createElement(
    "div",
    {
      "data-testid": "Spinner",
      style,
      className: cx(
        {
          [styles.inline]: inline
        },
        className
      )
    },
    /* @__PURE__ */ React__default.createElement(
      Icon,
      {
        className: cx(
          {
            "fa-spin": !prefersReducedMotion
          },
          iconClassName
        ),
        name: iconName,
        size,
        "aria-label": t("grafana-ui.spinner.aria-label", "Loading")
      }
    )
  );
};
const getStyles = (theme) => ({
  inline: css({
    display: "inline-block"
  })
});
const getDeprecatedStyles = (theme, size) => ({
  wrapper: css({
    fontSize: typeof size === "string" ? size : `${size}px`
  }),
  icon: css({
    display: "inline-block",
    fill: "currentColor",
    flexShrink: 0,
    label: "Icon",
    // line-height: 0; is needed for correct icon alignment in Safari
    lineHeight: 0,
    verticalAlign: "middle"
  })
});

export { Spinner };
//# sourceMappingURL=Spinner.js.map
