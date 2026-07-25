import { Global } from '@emotion/react';
import React__default from 'react';
import { useTheme2 } from '../ThemeContext.js';
import { getAgularPanelStyles } from './angularPanelStyles.js';
import { getCardStyles } from './card.js';
import { getElementStyles } from './elements.js';
import { getExtraStyles } from './extra.js';
import { getFormElementStyles } from './forms.js';
import { getLegacySelectStyles } from './legacySelect.js';
import { getMarkdownStyles } from './markdownStyles.js';
import { getPageStyles } from './page.js';
import { getRcTimePickerStyles } from './rcTimePicker.js';
import { getSkeletonStyles } from './skeletonStyles.js';
import { getUplotStyles } from './uPlot.js';

function GlobalStyles() {
  const theme = useTheme2();
  return /* @__PURE__ */ React__default.createElement(
    Global,
    {
      styles: [
        getElementStyles(theme),
        getExtraStyles(theme),
        getFormElementStyles(theme),
        getPageStyles(theme),
        getCardStyles(theme),
        getAgularPanelStyles(theme),
        getMarkdownStyles(),
        getSkeletonStyles(),
        getRcTimePickerStyles(theme),
        getUplotStyles(),
        getLegacySelectStyles(theme)
      ]
    }
  );
}

export { GlobalStyles };
//# sourceMappingURL=GlobalStyles.js.map
