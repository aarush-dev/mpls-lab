import React__default from 'react';
import { useStyles2 } from '../../themes/ThemeContext.js';
import { Text } from '../Text/Text.js';
import { getSelectStyles } from './getSelectStyles.js';

const SelectOptionGroupHeader = (props) => {
  var _a;
  const styles = useStyles2(getSelectStyles);
  return /* @__PURE__ */ React__default.createElement("div", { className: styles.groupHeader }, /* @__PURE__ */ React__default.createElement(Text, { weight: "bold", variant: "bodySmall", color: "secondary" }, (_a = props.children) != null ? _a : ""));
};

export { SelectOptionGroupHeader };
//# sourceMappingURL=SelectOptionGroupHeader.js.map
