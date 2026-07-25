import { cx } from '@emotion/css';
import React__default, { useState } from 'react';
import { formattedValueToString } from '@grafana/data';
import { TableCellDisplayMode } from '@grafana/schema';
import { useStyles2 } from '../../themes/ThemeContext.js';
import 'micro-memoize';
import '@emotion/react';
import 'tinycolor2';
import '../../utils/skeleton.js';
import '../../utils/dom.js';
import '../../utils/colors.js';
import 'slate';
import { getCellLinks } from '../../utils/table.js';
import 'lodash';
import 'ansicolor';
import '../../utils/logger.js';
import { clearLinkButtonStyles } from '../Button/Button.js';
import '../Button/ButtonGroup.js';
import { DataLinksContextMenu } from '../DataLinks/DataLinksContextMenu.js';
import { CellActions } from './CellActions.js';
import { getCellOptions, getCellColors } from './utils.js';

var __defProp = Object.defineProperty;
var __defProps = Object.defineProperties;
var __getOwnPropDescs = Object.getOwnPropertyDescriptors;
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
var __spreadProps = (a, b) => __defProps(a, __getOwnPropDescs(b));
const DefaultCell = (props) => {
  var _a, _b, _c;
  const { field, cell, tableStyles, row, cellProps, frame, rowStyled, textWrapped, height } = props;
  const inspectEnabled = Boolean((_a = field.config.custom) == null ? void 0 : _a.inspect);
  const displayValue = field.display(cell.value);
  const showFilters = props.onCellFilterAdded && field.config.filterable;
  const showActions = showFilters && cell.value !== void 0 || inspectEnabled;
  const cellOptions = getCellOptions(field);
  const hasLinks = Boolean((_b = getCellLinks(field, row)) == null ? void 0 : _b.length);
  const clearButtonStyle = useStyles2(clearLinkButtonStyles);
  const [hover, setHover] = useState(false);
  let value;
  const OG_TWEET_LENGTH = 140;
  const onMouseLeave = () => {
    setHover(false);
  };
  const onMouseEnter = () => {
    setHover(true);
  };
  if (cellOptions.type === TableCellDisplayMode.Custom) {
    const CustomCellComponent = cellOptions.cellComponent;
    value = /* @__PURE__ */ React__default.createElement(CustomCellComponent, { field, value: cell.value, rowIndex: row.index, frame });
  } else {
    if (React__default.isValidElement(cell.value)) {
      value = cell.value;
    } else {
      value = formattedValueToString(displayValue);
    }
  }
  const isStringValue = typeof value === "string";
  const textShouldWrap = displayValue.text.length <= OG_TWEET_LENGTH && /\s/.test(displayValue.text);
  const cellStyle = getCellStyle(
    tableStyles,
    cellOptions,
    displayValue,
    inspectEnabled,
    isStringValue,
    textShouldWrap,
    textWrapped,
    rowStyled
  );
  if (isStringValue) {
    let justifyContent = (_c = cellProps.style) == null ? void 0 : _c.justifyContent;
    if (justifyContent === "flex-end") {
      cellProps.style = __spreadProps(__spreadValues({}, cellProps.style), { textAlign: "right" });
    } else if (justifyContent === "center") {
      cellProps.style = __spreadProps(__spreadValues({}, cellProps.style), { textAlign: "center" });
    }
  }
  if (height) {
    cellProps.style = __spreadProps(__spreadValues({}, cellProps.style), { height });
  }
  if (textWrapped) {
    cellProps.style = __spreadProps(__spreadValues({}, cellProps.style), { textWrap: "wrap" });
  }
  return /* @__PURE__ */ React__default.createElement(
    "div",
    __spreadProps(__spreadValues({}, cellProps), {
      onMouseEnter: showActions ? onMouseEnter : void 0,
      onMouseLeave: showActions ? onMouseLeave : void 0,
      className: cellStyle
    }),
    !hasLinks && (isStringValue ? `${value}` : /* @__PURE__ */ React__default.createElement("div", { className: tableStyles.cellText }, value)),
    hasLinks && /* @__PURE__ */ React__default.createElement(DataLinksContextMenu, { links: () => getCellLinks(field, row) || [] }, (api) => {
      if (api.openMenu) {
        return /* @__PURE__ */ React__default.createElement(
          "button",
          {
            className: cx(clearButtonStyle, getLinkStyle(tableStyles, cellOptions, api.targetClassName)),
            onClick: api.openMenu
          },
          value
        );
      } else {
        return /* @__PURE__ */ React__default.createElement("div", { className: getLinkStyle(tableStyles, cellOptions, api.targetClassName) }, value);
      }
    }),
    hover && showActions && /* @__PURE__ */ React__default.createElement(CellActions, __spreadProps(__spreadValues({}, props), { previewMode: "text", showFilters }))
  );
};
function getCellStyle(tableStyles, cellOptions, displayValue, disableOverflowOnHover = false, isStringValue = false, shouldWrapText = false, textWrapped = false, rowStyled = false) {
  let textColor = void 0;
  let bgColor = void 0;
  let bgHoverColor = void 0;
  const colors = getCellColors(tableStyles, cellOptions, displayValue);
  textColor = colors.textColor;
  bgColor = colors.bgColor;
  bgHoverColor = colors.bgHoverColor;
  return tableStyles.buildCellContainerStyle(
    textColor,
    bgColor,
    bgHoverColor,
    !disableOverflowOnHover,
    isStringValue,
    shouldWrapText,
    textWrapped,
    rowStyled
  );
}
function getLinkStyle(tableStyles, cellOptions, targetClassName) {
  if (cellOptions.type === TableCellDisplayMode.Auto) {
    return cx(tableStyles.cellLink, targetClassName);
  }
  return cx(tableStyles.cellLinkForColoredCell, targetClassName);
}

export { DefaultCell };
//# sourceMappingURL=DefaultCell.js.map
