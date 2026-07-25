import '../transformations/matchers.js';
import '../transformations/transformers/calculateField.js';
import '../transformations/transformers/concat.js';
import '../transformations/transformers/convertFieldType.js';
import '../transformations/transformers/ensureColumns.js';
import '../transformations/transformers/filter.js';
import '../transformations/transformers/filterByName.js';
import '../transformations/transformers/filterByRefId.js';
import '../transformations/transformers/filterByValue.js';
import '../transformations/transformers/formatString.js';
import '../transformations/transformers/formatTime.js';
import '../transformations/transformers/groupBy.js';
import '../transformations/transformers/groupToNestedTable.js';
import '../transformations/transformers/groupingToMatrix.js';
import '../transformations/transformers/histogram.js';
import '../transformations/transformers/joinByField.js';
import '../transformations/transformers/labelsToFields.js';
import '../transformations/transformers/limit.js';
import '../transformations/transformers/merge.js';
import '../transformations/transformers/noop.js';
import '../transformations/transformers/order.js';
import '../transformations/transformers/organize.js';
import '../transformations/transformers/reduce.js';
import '../transformations/transformers/rename.js';
import '../transformations/transformers/renameByRegex.js';
import '../transformations/transformers/seriesToRows.js';
import '../transformations/transformers/sortBy.js';
import '../transformations/fieldReducer.js';
import 'rxjs';
import 'rxjs/operators';
import '../transformations/standardTransformersRegistry.js';
import '../transformations/matchers/nameMatcher.js';
import '../vector/FunctionalVector.js';
import 'lodash';
import { FieldType, TIME_SERIES_TIME_FIELD_NAME, TIME_SERIES_VALUE_FIELD_NAME } from '../types/dataFrame.js';
import '../datetime/moment_wrapper.js';
import '../datetime/rangeutil.js';
import '../datetime/timezones.js';
import '../datetime/formats.js';
import 'moment-timezone';
import '@grafana/schema';
import 'date-fns';
import '../types/vector.js';
import '../types/datasource.js';
import '../types/legacyEvents.js';
import { formatLabels } from '../utils/labels.js';
import '../dataframe/StreamingDataFrame.js';

function getFrameDisplayName(frame, index) {
  if (frame.name) {
    return frame.name;
  }
  const valueFieldNames = [];
  for (const field of frame.fields) {
    if (field.type === FieldType.time) {
      continue;
    }
    if (valueFieldNames.length > 1) {
      break;
    }
    valueFieldNames.push(getFieldDisplayName(field, frame));
  }
  if (valueFieldNames.length === 1) {
    return valueFieldNames[0];
  }
  if (index === void 0 && frame.fields.length > 0) {
    return frame.fields.filter((f) => f.type !== FieldType.time).map((f) => getFieldDisplayName(f, frame)).join(", ");
  }
  if (frame.refId) {
    return `Series (${frame.refId})`;
  }
  return `Series (${index})`;
}
function cacheFieldDisplayNames(frames) {
  frames.forEach((frame) => {
    frame.fields.forEach((field) => {
      getFieldDisplayName(field, frame, frames);
    });
  });
}
function getFieldDisplayName(field, frame, allFrames) {
  var _a, _b;
  const existingTitle = (_a = field.state) == null ? void 0 : _a.displayName;
  const multipleFrames = Boolean(allFrames && allFrames.length > 1);
  if (existingTitle && multipleFrames === ((_b = field.state) == null ? void 0 : _b.multipleFrames)) {
    return existingTitle;
  }
  const displayName = calculateFieldDisplayName(field, frame, allFrames);
  field.state = field.state || {};
  field.state.displayName = displayName;
  field.state.multipleFrames = multipleFrames;
  return displayName;
}
function calculateFieldDisplayName(field, frame, allFrames) {
  var _a, _b, _c, _d, _e;
  const hasConfigTitle = ((_a = field.config) == null ? void 0 : _a.displayName) && ((_b = field.config) == null ? void 0 : _b.displayName.length);
  const isComparisonSeries = Boolean((_d = (_c = frame == null ? void 0 : frame.meta) == null ? void 0 : _c.timeCompare) == null ? void 0 : _d.isTimeShiftQuery);
  let displayName = hasConfigTitle ? field.config.displayName : field.name;
  if (hasConfigTitle) {
    return isComparisonSeries ? `${displayName} (comparison)` : displayName;
  }
  if (frame && ((_e = field.config) == null ? void 0 : _e.displayNameFromDS)) {
    return isComparisonSeries ? `${field.config.displayNameFromDS} (comparison)` : field.config.displayNameFromDS;
  }
  if (field.type === FieldType.time && !field.labels) {
    return displayName != null ? displayName : TIME_SERIES_TIME_FIELD_NAME;
  }
  let parts = [];
  let frameNamesDiffer = false;
  if (allFrames && allFrames.length > 1) {
    for (let i = 1; i < allFrames.length; i++) {
      const frame2 = allFrames[i];
      if (frame2.name !== allFrames[i - 1].name) {
        frameNamesDiffer = true;
        break;
      }
    }
  }
  let frameNameAdded = false;
  let labelsAdded = false;
  if (frameNamesDiffer && (frame == null ? void 0 : frame.name)) {
    parts.push(frame.name);
    frameNameAdded = true;
  }
  if (field.name && field.name !== TIME_SERIES_VALUE_FIELD_NAME) {
    parts.push(field.name);
  }
  if (field.labels && frame) {
    let singleLabelName = getSingleLabelName(allFrames != null ? allFrames : [frame]);
    if (!singleLabelName) {
      let allLabels = formatLabels(field.labels);
      if (allLabels) {
        parts.push(allLabels);
        labelsAdded = true;
      }
    } else if (field.labels[singleLabelName]) {
      parts.push(field.labels[singleLabelName]);
      labelsAdded = true;
    }
  }
  if (frame && !frameNameAdded && !labelsAdded && field.name === TIME_SERIES_VALUE_FIELD_NAME) {
    if (frame.name && frame.name.length > 0) {
      parts.push(frame.name);
      frameNameAdded = true;
    }
  }
  if (parts.length) {
    displayName = parts.join(" ");
  } else if (field.name) {
    displayName = field.name;
  } else {
    displayName = TIME_SERIES_VALUE_FIELD_NAME;
  }
  if (displayName === field.name) {
    displayName = getUniqueFieldName(field, frame);
  }
  if (isComparisonSeries) {
    displayName = `${displayName} (comparison)`;
  }
  return displayName;
}
function getUniqueFieldName(field, frame) {
  let dupeCount = 0;
  let foundSelf = false;
  if (frame) {
    for (let i = 0; i < frame.fields.length; i++) {
      const otherField = frame.fields[i];
      if (field === otherField) {
        foundSelf = true;
        if (dupeCount > 0) {
          dupeCount++;
          break;
        }
      } else if (field.name === otherField.name) {
        dupeCount++;
        if (foundSelf) {
          break;
        }
      }
    }
  }
  if (dupeCount) {
    return `${field.name} ${dupeCount}`;
  }
  return field.name;
}
function getSingleLabelName(frames) {
  let singleName = null;
  for (let i = 0; i < frames.length; i++) {
    const frame = frames[i];
    for (const field of frame.fields) {
      if (!field.labels) {
        continue;
      }
      for (const labelKey in field.labels) {
        if (singleName === null) {
          singleName = labelKey;
        } else if (labelKey !== singleName) {
          return null;
        }
      }
    }
  }
  return singleName;
}

export { cacheFieldDisplayNames, calculateFieldDisplayName, getFieldDisplayName, getFrameDisplayName, getUniqueFieldName };
//# sourceMappingURL=fieldState.js.map
