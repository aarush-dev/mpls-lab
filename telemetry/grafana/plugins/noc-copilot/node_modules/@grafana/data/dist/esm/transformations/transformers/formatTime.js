import { map } from 'rxjs/operators';
import 'lodash';
import '../../vector/FunctionalVector.js';
import '../../datetime/moment_wrapper.js';
import '../../datetime/rangeutil.js';
import '../../datetime/timezones.js';
import '../../datetime/formats.js';
import 'moment-timezone';
import '@grafana/schema';
import 'date-fns';
import { cacheFieldDisplayNames } from '../../field/fieldState.js';
import { TransformationApplicabilityLevels } from '../../types/transformations.js';
import '../../types/vector.js';
import '../../types/datasource.js';
import '../../types/legacyEvents.js';
import { DataTransformerID } from './ids.js';
import '../matchers.js';
import './calculateField.js';
import './concat.js';
import { fieldToStringField } from './convertFieldType.js';
import './ensureColumns.js';
import './filter.js';
import './filterByName.js';
import './filterByRefId.js';
import './filterByValue.js';
import './formatString.js';
import './groupBy.js';
import './groupToNestedTable.js';
import './groupingToMatrix.js';
import './histogram.js';
import './joinByField.js';
import './labelsToFields.js';
import './limit.js';
import './merge.js';
import './noop.js';
import './order.js';
import './organize.js';
import './reduce.js';
import './rename.js';
import './renameByRegex.js';
import './seriesToRows.js';
import './sortBy.js';
import '../fieldReducer.js';
import 'rxjs';
import '../standardTransformersRegistry.js';
import '../matchers/nameMatcher.js';
import '../../dataframe/StreamingDataFrame.js';
import '../../field/fieldColor.js';
import '../../field/standardFieldConfigEditorRegistry.js';
import 'react';
import 'react-use/lib/usePrevious';
import 'tinycolor2';
import 'papaparse';
import '../../utils/binaryOperators.js';
import '../../utils/unaryOperators.js';
import 'marked';
import 'marked-mangle';
import '../../text/sanitize.js';

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
const formatTimeTransformer = {
  id: DataTransformerID.formatTime,
  name: "Format time",
  description: "Set the output format of a time field",
  defaultOptions: { timeField: "", outputFormat: "", useTimezone: true },
  isApplicable: (data) => {
    for (const frame of data) {
      for (const field of frame.fields) {
        if (field.type === "time") {
          return TransformationApplicabilityLevels.Applicable;
        }
      }
    }
    return TransformationApplicabilityLevels.NotApplicable;
  },
  isApplicableDescription: "The Format time transformation requires a time field to work. No time field could be found.",
  operator: (options, ctx) => (source) => source.pipe(
    map((data) => {
      return applyFormatTime(options, data, ctx);
    })
  )
};
const applyFormatTime = ({ timeField, outputFormat, timezone }, data, ctx) => {
  var _a;
  if (!Array.isArray(data) || data.length === 0) {
    return data;
  }
  cacheFieldDisplayNames(data);
  outputFormat = (_a = ctx == null ? void 0 : ctx.interpolate(outputFormat)) != null ? _a : outputFormat;
  return data.map((frame) => __spreadProps(__spreadValues({}, frame), {
    fields: frame.fields.map((field) => {
      var _a2;
      if (((_a2 = field.state) == null ? void 0 : _a2.displayName) === timeField) {
        field = fieldToStringField(field, outputFormat, { timeZone: timezone });
      }
      return field;
    })
  }));
};

export { applyFormatTime, formatTimeTransformer };
//# sourceMappingURL=formatTime.js.map
