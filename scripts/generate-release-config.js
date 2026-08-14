#!/usr/bin/env node

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const PLUGINS_DIR = path.join(ROOT, 'plugins');
const MARKETPLACE_PATH = path.join(ROOT, '.claude-plugin', 'marketplace.json');
const CONFIG_PATH = path.join(ROOT, 'release-please-config.json');
const MANIFEST_PATH = path.join(ROOT, '.release-please-manifest.json');

function discoverPlugins() {
  const plugins = [];
  if (!fs.existsSync(PLUGINS_DIR)) return plugins;

  const entries = fs.readdirSync(PLUGINS_DIR, { withFileTypes: true });
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const pluginJsonPath = path.join(PLUGINS_DIR, entry.name, '.claude-plugin', 'plugin.json');
    if (!fs.existsSync(pluginJsonPath)) {
      console.warn(`Warning: ${entry.name}/ has no .claude-plugin/plugin.json, skipping`);
      continue;
    }
    const pluginJson = JSON.parse(fs.readFileSync(pluginJsonPath, 'utf8'));
    plugins.push({ dirName: entry.name, json: pluginJson });
  }
  return plugins;
}

function syncMarketplace(plugins) {
  const marketplace = JSON.parse(fs.readFileSync(MARKETPLACE_PATH, 'utf8'));
  const existingByName = new Map(marketplace.plugins.map((p, i) => [p.name, i]));
  let updated = false;

  for (const plugin of plugins) {
    const pj = plugin.json;
    if (existingByName.has(pj.name)) {
      const idx = existingByName.get(pj.name);
      const existing = marketplace.plugins[idx];
      const changes = [];
      if (existing.source !== `./plugins/${plugin.dirName}`) { existing.source = `./plugins/${plugin.dirName}`; changes.push('source'); }
      if (existing.description !== pj.description) { existing.description = pj.description || ''; changes.push('description'); }
      if (existing.version !== pj.version) { existing.version = pj.version || '0.1.0'; changes.push('version'); }
      if (JSON.stringify(existing.keywords) !== JSON.stringify(pj.keywords || [])) { existing.keywords = pj.keywords || []; changes.push('keywords'); }
      if (changes.length > 0) { updated = true; console.log(`Updated ${pj.name}: ${changes.join(', ')}`); }
    } else {
      marketplace.plugins.push({
        name: pj.name,
        source: `./plugins/${plugin.dirName}`,
        description: pj.description || '',
        version: pj.version || '0.1.0',
        keywords: pj.keywords || []
      });
      updated = true;
      console.log(`Added new plugin: ${pj.name}`);
    }
  }

  if (updated) {
    fs.writeFileSync(MARKETPLACE_PATH, JSON.stringify(marketplace, null, 2) + '\n');
    console.log('Updated .claude-plugin/marketplace.json');
  }
  return marketplace;
}

function generateReleaseConfig(marketplace) {
  let existingManifest = {};
  if (fs.existsSync(MANIFEST_PATH)) {
    existingManifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf8'));
  }

  const packages = {};
  const manifest = {};

  marketplace.plugins.forEach((plugin) => {
    const pluginPath = `plugins/${plugin.name}`;
    const pluginJsonPath = path.join(ROOT, pluginPath, '.claude-plugin', 'plugin.json');
    if (!fs.existsSync(pluginJsonPath)) {
      console.warn(`Warning: Plugin ${plugin.name} directory not found, skipping`);
      return;
    }
    const pluginJson = JSON.parse(fs.readFileSync(pluginJsonPath, 'utf8'));
    const version = existingManifest[pluginPath] || pluginJson.version || '0.1.0';

    packages[pluginPath] = {
      component: plugin.name,
      'changelog-path': 'CHANGELOG.md',
      'extra-files': [
        { type: 'json', path: '.claude-plugin/plugin.json', jsonpath: '$.version' }
      ]
    };
    manifest[pluginPath] = version;
  });

  const config = {
    '$schema': 'https://raw.githubusercontent.com/googleapis/release-please/main/schemas/config.json',
    'release-type': 'simple',
    'bump-minor-pre-major': false,
    'bump-patch-for-minor-pre-major': false,
    'include-component-in-tag': true,
    'include-v-in-tag': true,
    'pull-request-title-pattern': 'chore(release): ${component} ${version}',
    'separate-pull-requests': true,
    'changelog-sections': [
      { type: 'feat', section: 'Features' },
      { type: 'fix', section: 'Bug Fixes' },
      { type: 'perf', section: 'Performance' },
      { type: 'docs', section: 'Documentation' },
      { type: 'chore', section: 'Maintenance', hidden: true }
    ],
    packages
  };

  fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2) + '\n');
  fs.writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2) + '\n');
  console.log(`Generated release-please-config.json with ${Object.keys(packages).length} package(s)`);
}

function main() {
  const plugins = discoverPlugins();
  console.log(`Discovered ${plugins.length} plugin(s) in plugins/`);
  const marketplace = syncMarketplace(plugins);
  generateReleaseConfig(marketplace);
}

main();
