import { cx, css } from '@emotion/css';
import { flip, shift, useFloating, autoUpdate } from '@floating-ui/react';
import { useDialog } from '@react-aria/dialog';
import { FocusScope } from '@react-aria/focus';
import { useOverlay } from '@react-aria/overlays';
import React__default, { useState, useEffect, useCallback, useRef } from 'react';
import Calendar from 'react-calendar';
import { useMedia } from 'react-use';
import { dateTimeFormat, dateTime, isDateTime, dateTimeForTimeZone, getTimeZone } from '@grafana/data';
import { Components } from '@grafana/e2e-selectors';
import { useStyles2, useTheme2 } from '../../../themes/ThemeContext.js';
import 'micro-memoize';
import '@emotion/react';
import 'tinycolor2';
import '../../../utils/skeleton.js';
import { Button } from '../../Button/Button.js';
import { InlineField } from '../../Forms/InlineField.js';
import { Icon } from '../../Icon/Icon.js';
import { Input } from '../../Input/Input.js';
import { Stack } from '../../Layout/Stack/Stack.js';
import { getModalStyles } from '../../Modal/getModalStyles.js';
import { Portal } from '../../Portal/Portal.js';
import { TimeOfDayPicker, POPUP_CLASS_NAME } from '../TimeOfDayPicker.js';
import { getBodyStyles } from '../TimeRangePicker/CalendarBody.js';
import { isValid } from '../utils.js';
import { adjustDateForReactCalendar } from '../utils/adjustDateForReactCalendar.js';

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
const DateTimePicker = ({
  date,
  maxDate,
  minDate,
  label,
  onChange,
  disabledHours,
  disabledMinutes,
  disabledSeconds,
  showSeconds = true,
  clearable = false
}) => {
  const [isOpen, setOpen] = useState(false);
  const ref = useRef(null);
  const { overlayProps, underlayProps } = useOverlay(
    {
      onClose: () => setOpen(false),
      isDismissable: true,
      isOpen,
      shouldCloseOnInteractOutside: (element) => {
        const popupElement = document.getElementsByClassName(POPUP_CLASS_NAME)[0];
        return !(popupElement && popupElement.contains(element));
      }
    },
    ref
  );
  const { dialogProps } = useDialog({}, ref);
  const theme = useTheme2();
  const { modalBackdrop } = useStyles2(getModalStyles);
  const isFullscreen = useMedia(`(min-width: ${theme.breakpoints.values.lg}px)`);
  const styles = useStyles2(getStyles);
  const middleware = [
    flip({
      // see https://floating-ui.com/docs/flip#combining-with-shift
      crossAxis: false,
      boundary: document.body
    }),
    shift()
  ];
  const { refs, floatingStyles } = useFloating({
    open: isOpen,
    placement: "bottom-start",
    onOpenChange: setOpen,
    middleware,
    whileElementsMounted: autoUpdate,
    strategy: "fixed"
  });
  const onApply = useCallback(
    (date2) => {
      setOpen(false);
      onChange(date2);
    },
    [onChange]
  );
  const onOpen = useCallback(
    (event) => {
      event.preventDefault();
      setOpen(true);
    },
    [setOpen]
  );
  return /* @__PURE__ */ React__default.createElement("div", { "data-testid": "date-time-picker", style: { position: "relative" } }, /* @__PURE__ */ React__default.createElement(
    DateTimeInput,
    {
      date,
      onChange,
      isFullscreen,
      onOpen,
      label,
      ref: refs.setReference,
      showSeconds,
      clearable
    }
  ), isOpen ? isFullscreen ? /* @__PURE__ */ React__default.createElement(Portal, null, /* @__PURE__ */ React__default.createElement(FocusScope, { contain: true, autoFocus: true, restoreFocus: true }, /* @__PURE__ */ React__default.createElement("div", __spreadValues(__spreadValues({ ref }, overlayProps), dialogProps), /* @__PURE__ */ React__default.createElement(
    DateTimeCalendar,
    {
      date,
      onChange: onApply,
      isFullscreen: true,
      onClose: () => setOpen(false),
      maxDate,
      minDate,
      ref: refs.setFloating,
      style: floatingStyles,
      showSeconds,
      disabledHours,
      disabledMinutes,
      disabledSeconds
    }
  )))) : /* @__PURE__ */ React__default.createElement(Portal, null, /* @__PURE__ */ React__default.createElement("div", __spreadValues({ className: modalBackdrop }, underlayProps)), /* @__PURE__ */ React__default.createElement(FocusScope, { contain: true, autoFocus: true, restoreFocus: true }, /* @__PURE__ */ React__default.createElement("div", __spreadValues(__spreadValues({ ref }, overlayProps), dialogProps), /* @__PURE__ */ React__default.createElement("div", { className: styles.modal }, /* @__PURE__ */ React__default.createElement(
    DateTimeCalendar,
    {
      date,
      maxDate,
      minDate,
      onChange: onApply,
      isFullscreen: false,
      onClose: () => setOpen(false),
      showSeconds,
      disabledHours,
      disabledMinutes,
      disabledSeconds
    }
  ))))) : null);
};
const DateTimeInput = React__default.forwardRef(
  ({ date, label, onChange, onOpen, showSeconds = true, clearable = false }, ref) => {
    const styles = useStyles2(getStyles);
    const format = showSeconds ? "YYYY-MM-DD HH:mm:ss" : "YYYY-MM-DD HH:mm";
    const [internalDate, setInternalDate] = useState(() => {
      return { value: date ? dateTimeFormat(date) : !clearable ? dateTimeFormat(dateTime()) : "", invalid: false };
    });
    useEffect(() => {
      if (date) {
        setInternalDate({
          invalid: !isValid(dateTimeFormat(date, { format })),
          value: isDateTime(date) ? dateTimeFormat(date, { format }) : date
        });
      }
    }, [date, format]);
    const onChangeDate = useCallback((event) => {
      const isInvalid = !isValid(event.currentTarget.value);
      setInternalDate({
        value: event.currentTarget.value,
        invalid: isInvalid
      });
    }, []);
    const onBlur = useCallback(() => {
      if (!internalDate.invalid && internalDate.value) {
        const date2 = dateTimeForTimeZone(getTimeZone(), internalDate.value);
        onChange(date2);
      }
    }, [internalDate, onChange]);
    const clearInternalDate = useCallback(() => {
      setInternalDate({ value: "", invalid: false });
      onChange();
    }, [onChange]);
    const icon = /* @__PURE__ */ React__default.createElement(Button, { "aria-label": "Time picker", icon: "calendar-alt", variant: "secondary", onClick: onOpen });
    return /* @__PURE__ */ React__default.createElement(InlineField, { label, invalid: !!(internalDate.value && internalDate.invalid), className: styles.field }, /* @__PURE__ */ React__default.createElement(
      Input,
      {
        onChange: onChangeDate,
        addonAfter: icon,
        value: internalDate.value,
        onBlur,
        "data-testid": Components.DateTimePicker.input,
        placeholder: "Select date/time",
        ref,
        suffix: clearable && internalDate.value && /* @__PURE__ */ React__default.createElement(Icon, { name: "times", className: styles.clearIcon, onClick: clearInternalDate })
      }
    ));
  }
);
DateTimeInput.displayName = "DateTimeInput";
const DateTimeCalendar = React__default.forwardRef(
  ({
    date,
    onClose,
    onChange,
    isFullscreen,
    maxDate,
    minDate,
    style,
    showSeconds = true,
    disabledHours,
    disabledMinutes,
    disabledSeconds
  }, ref) => {
    const calendarStyles = useStyles2(getBodyStyles);
    const styles = useStyles2(getStyles);
    const [timeOfDayDateTime, setTimeOfDayDateTime] = useState(() => {
      if (date && date.isValid()) {
        return dateTimeForTimeZone(getTimeZone(), date);
      }
      return dateTimeForTimeZone(getTimeZone(), /* @__PURE__ */ new Date());
    });
    const [reactCalendarDate, setReactCalendarDate] = useState(() => {
      if (date && date.isValid()) {
        return adjustDateForReactCalendar(date.toDate(), getTimeZone());
      }
      return /* @__PURE__ */ new Date();
    });
    const onChangeDate = useCallback((date2) => {
      if (date2 && !Array.isArray(date2)) {
        setReactCalendarDate(date2);
      }
    }, []);
    const onChangeTime = useCallback((date2) => {
      setTimeOfDayDateTime(date2);
    }, []);
    const handleApply = () => {
      const newDate = dateTime(timeOfDayDateTime);
      newDate.set("date", reactCalendarDate.getDate());
      newDate.set("month", reactCalendarDate.getMonth());
      newDate.set("year", reactCalendarDate.getFullYear());
      onChange(newDate);
    };
    return /* @__PURE__ */ React__default.createElement("div", { className: cx(styles.container, { [styles.fullScreen]: isFullscreen }), style, ref }, /* @__PURE__ */ React__default.createElement(
      Calendar,
      {
        next2Label: null,
        prev2Label: null,
        value: reactCalendarDate,
        nextLabel: /* @__PURE__ */ React__default.createElement(Icon, { name: "angle-right" }),
        nextAriaLabel: "Next month",
        prevLabel: /* @__PURE__ */ React__default.createElement(Icon, { name: "angle-left" }),
        prevAriaLabel: "Previous month",
        onChange: onChangeDate,
        locale: "en",
        className: calendarStyles.body,
        tileClassName: calendarStyles.title,
        maxDate,
        minDate
      }
    ), /* @__PURE__ */ React__default.createElement("div", { className: styles.time }, /* @__PURE__ */ React__default.createElement(
      TimeOfDayPicker,
      {
        showSeconds,
        onChange: onChangeTime,
        value: timeOfDayDateTime,
        disabledHours,
        disabledMinutes,
        disabledSeconds
      }
    )), /* @__PURE__ */ React__default.createElement(Stack, null, /* @__PURE__ */ React__default.createElement(Button, { type: "button", onClick: handleApply }, "Apply"), /* @__PURE__ */ React__default.createElement(Button, { variant: "secondary", type: "button", onClick: onClose }, "Cancel")));
  }
);
DateTimeCalendar.displayName = "DateTimeCalendar";
const getStyles = (theme) => ({
  container: css({
    padding: theme.spacing(1),
    border: `1px ${theme.colors.border.weak} solid`,
    borderRadius: theme.shape.radius.default,
    backgroundColor: theme.colors.background.primary,
    zIndex: theme.zIndex.modal
  }),
  fullScreen: css({
    position: "absolute"
  }),
  time: css({
    marginBottom: theme.spacing(2)
  }),
  modal: css({
    position: "fixed",
    top: "50%",
    left: "50%",
    transform: "translate(-50%, -50%)",
    zIndex: theme.zIndex.modal,
    maxWidth: "280px"
  }),
  clearIcon: css({
    cursor: "pointer"
  }),
  field: css({
    marginBottom: 0,
    width: "100%"
  })
});

export { DateTimePicker };
//# sourceMappingURL=DateTimePicker.js.map
