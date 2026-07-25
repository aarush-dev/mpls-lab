import React__default from 'react';
import { useStyles2 } from '../../themes/ThemeContext.js';
import { getSelectStyles } from './getSelectStyles.js';

var __defProp = Object.defineProperty;
var __getOwnPropSymbols = Object.getOwnPropertySymbols;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __propIsEnum = Object.prototype.propertyIsEnumerable;
var __defNormalProp = (obj, key, value) => key in obj ? __defProp(obj, key, { enumerable: true, configurable: true, writable: true, value }) : obj[key] = value;
var __spreadValues = (a, b) => {
  for (var prop in b || (b = {}))
    if (__hasOwnProp.call(b, prop))
      __defNormalProp(a, prop, b[prop]);
  if (__getOwnPropSymbols)
    for (var prop of __getOwnPropSymbols(b)) {
      if (__propIsEnum.call(b, prop))
        __defNormalProp(a, prop, b[prop]);
    }
  return a;
};
const SelectOptionGroup = ({
  children,
  cx,
  getClassNames,
  getStyles,
  Heading,
  headingProps,
  label,
  selectProps,
  theme
}) => {
  const styles = useStyles2(getSelectStyles);
  return /* @__PURE__ */ React__default.createElement("div", { className: styles.group }, /* @__PURE__ */ React__default.createElement(
    Heading,
    __spreadValues({
      cx,
      getClassNames,
      getStyles,
      selectProps,
      theme
    }, headingProps),
    label
  ), children);
};

export { SelectOptionGroup };
//# sourceMappingURL=SelectOptionGroup.js.map
