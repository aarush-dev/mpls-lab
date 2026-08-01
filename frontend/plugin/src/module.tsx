import { AppPlugin } from '@grafana/data';
import { App } from './App';
import { applyBrand } from './brand';

// Reskin the whole Grafana instance as soon as this module loads. With plugin.json "preload": true
// this executes on every page, not just when the app is open.
applyBrand();

export const plugin = new AppPlugin<{}>().setRootPage(App);
