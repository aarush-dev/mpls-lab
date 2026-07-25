import { css } from '@emotion/css';
import React__default from 'react';
import SVG from 'react-inlinesvg';
import '@grafana/data';
import { useStyles2 } from '../../themes/ThemeContext.js';
import 'micro-memoize';
import '@emotion/react';
import 'tinycolor2';
import '../../utils/skeleton.js';
import { Box } from '../Layout/Box/Box.js';
import { Stack } from '../Layout/Stack/Stack.js';
import { Text } from '../Text/Text.js';
import { GrotCTA } from './GrotCTA/GrotCTA.js';
import { GrotNotFound } from './GrotNotFound/GrotNotFound.js';
import GrotCompleted from './grot-completed.svg.js';

const EmptyState = ({
  button,
  children,
  image,
  message,
  hideImage = false,
  variant,
  role
}) => {
  const styles = useStyles2(getStyles);
  const imageToShow = image != null ? image : getDefaultImageForVariant(variant);
  return /* @__PURE__ */ React__default.createElement(Box, { paddingY: 4, display: "flex", direction: "column", alignItems: "center", role }, /* @__PURE__ */ React__default.createElement("div", { className: styles.container }, !hideImage && imageToShow, /* @__PURE__ */ React__default.createElement(Stack, { direction: "column", alignItems: "center" }, /* @__PURE__ */ React__default.createElement(Text, { variant: "h4", textAlignment: "center" }, message), children && /* @__PURE__ */ React__default.createElement(Text, { color: "secondary", textAlignment: "center" }, children)), button));
};
function getDefaultImageForVariant(variant) {
  switch (variant) {
    case "call-to-action": {
      return /* @__PURE__ */ React__default.createElement(GrotCTA, { width: 300 });
    }
    case "not-found": {
      return /* @__PURE__ */ React__default.createElement(GrotNotFound, { width: 300 });
    }
    case "completed": {
      return /* @__PURE__ */ React__default.createElement(SVG, { src: GrotCompleted, width: 300 });
    }
    default: {
      throw new Error(`Unknown variant: ${variant}`);
    }
  }
}
const getStyles = (theme) => ({
  container: css({
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: theme.spacing(4),
    maxWidth: "600px"
  })
});

export { EmptyState };
//# sourceMappingURL=EmptyState.js.map
