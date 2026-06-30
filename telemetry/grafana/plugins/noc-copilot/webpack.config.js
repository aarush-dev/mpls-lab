const path = require('path');
const CopyPlugin = require('copy-webpack-plugin');

module.exports = {
  entry: './src/module.ts',
  output: {
    filename: 'module.js',
    path: path.resolve(__dirname, 'dist'),
    libraryTarget: 'amd',
    clean: true,
  },
  externals: [
    'react',
    'react-dom',
    'lodash',
    '@grafana/ui',
    '@grafana/data',
    '@grafana/runtime',
    '@grafana/schema',
    '@grafana/e2e-selectors',
    'moment',
    function ({ request }, callback) {
      if (/^grafana\//.test(request)) return callback(null, request);
      callback();
    },
  ],
  module: {
    rules: [
      {
        test: /\.[tj]sx?$/,
        exclude: /node_modules/,
        use: {
          loader: 'ts-loader',
          options: { transpileOnly: true },
        },
      },
    ],
  },
  resolve: { extensions: ['.tsx', '.ts', '.js'] },
  plugins: [
    new CopyPlugin({ patterns: [{ from: 'src/plugin.json', to: '.' }] }),
  ],
};
