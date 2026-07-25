import { cx, css } from '@emotion/css';
import React__default, { useState, useRef, useEffect } from 'react';
import { useForm } from 'react-hook-form';
import { selectors } from '@grafana/e2e-selectors';
import '@grafana/data';
import { useStyles2 } from '../../themes/ThemeContext.js';
import 'micro-memoize';
import '@emotion/react';
import 'tinycolor2';
import '../../utils/skeleton.js';
import { Button } from '../Button/Button.js';
import '../Button/ButtonGroup.js';
import { Input } from '../Input/Input.js';
import { Box } from '../Layout/Box/Box.js';
import { Stack } from '../Layout/Stack/Stack.js';
import { Modal } from '../Modal/Modal.js';

const ConfirmModal = ({
  isOpen,
  title,
  body,
  description,
  confirmText,
  confirmVariant = "destructive",
  confirmationText,
  dismissText = "Cancel",
  dismissVariant = "secondary",
  alternativeText,
  modalClass,
  icon = "exclamation-triangle",
  onConfirm,
  onDismiss,
  onAlternative,
  confirmButtonVariant = "destructive"
}) => {
  const [disabled, setDisabled] = useState(Boolean(confirmationText));
  const styles = useStyles2(getStyles);
  const buttonRef = useRef(null);
  const onConfirmationTextChange = (event) => {
    setDisabled((confirmationText == null ? void 0 : confirmationText.toLowerCase().localeCompare(event.currentTarget.value.toLowerCase())) !== 0);
  };
  useEffect(() => {
    var _a;
    if (isOpen) {
      (_a = buttonRef.current) == null ? void 0 : _a.focus();
    }
  }, [isOpen]);
  useEffect(() => {
    if (isOpen) {
      setDisabled(Boolean(confirmationText));
    }
  }, [isOpen, confirmationText]);
  const onConfirmClick = async () => {
    setDisabled(true);
    try {
      await onConfirm();
    } finally {
      setDisabled(false);
    }
  };
  const { handleSubmit } = useForm();
  return /* @__PURE__ */ React__default.createElement(Modal, { className: cx(styles.modal, modalClass), title, icon, isOpen, onDismiss }, /* @__PURE__ */ React__default.createElement("form", { onSubmit: handleSubmit(onConfirmClick) }, /* @__PURE__ */ React__default.createElement("div", { className: styles.modalText }, body, description ? /* @__PURE__ */ React__default.createElement("div", { className: styles.modalDescription }, description) : null, confirmationText ? /* @__PURE__ */ React__default.createElement("div", { className: styles.modalConfirmationInput }, /* @__PURE__ */ React__default.createElement(Stack, { alignItems: "flex-start" }, /* @__PURE__ */ React__default.createElement(Box, null, /* @__PURE__ */ React__default.createElement(Input, { placeholder: `Type "${confirmationText}" to confirm`, onChange: onConfirmationTextChange })))) : null), /* @__PURE__ */ React__default.createElement(Modal.ButtonRow, null, /* @__PURE__ */ React__default.createElement(Button, { variant: dismissVariant, onClick: onDismiss, fill: "outline" }, dismissText), /* @__PURE__ */ React__default.createElement(
    Button,
    {
      type: "submit",
      variant: confirmButtonVariant,
      disabled,
      ref: buttonRef,
      "data-testid": selectors.pages.ConfirmModal.delete
    },
    confirmText
  ), onAlternative ? /* @__PURE__ */ React__default.createElement(Button, { variant: "primary", onClick: onAlternative }, alternativeText) : null)));
};
const getStyles = (theme) => ({
  modal: css({
    width: "500px"
  }),
  modalText: css({
    fontSize: theme.typography.h5.fontSize,
    color: theme.colors.text.primary
  }),
  modalDescription: css({
    fontSize: theme.typography.body.fontSize
  }),
  modalConfirmationInput: css({
    paddingTop: theme.spacing(1)
  })
});

export { ConfirmModal };
//# sourceMappingURL=ConfirmModal.js.map
