import { AppPlugin } from '@grafana/data';
import React from 'react';
import { App } from './App';

export const plugin = new AppPlugin<{}>().setRootPage(App);
