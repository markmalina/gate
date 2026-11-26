import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../../..');
const read = name => JSON.parse(fs.readFileSync(path.join(here, name), 'utf8'));
const assert = (ok, message) => { if (!ok) throw new Error(message); };
const inv = read('source-inventory.json');
const data = read('data-model.json');
const cfg = read('config-catalog.json');
const api = read('api-catalog.json');
const back = read('backlog.json');
const findings = read('findings.json');
assert(inv.files.length === 943, 'source count');
assert(data.tables.length === 113 && data.migrations.length === 119, 'data counts');
assert(cfg.fields.length === 161 && cfg.missing_aliases.length === 48, 'config counts');
assert(api.core_tools.length === 19 && api.shadowed_transcription_tools.length === 5, 'MCP counts');
assert(back.issues.length === 34 && back.pull_requests.length === 14, 'backlog counts');
assert(new Set(back.issues.map(x => x.number)).size === 34, 'issue duplicate');
assert(new Set(back.pull_requests.map(x => x.number)).size === 14, 'PR duplicate');
assert(findings.findings.length === 16, 'finding count');
const git = spawnSync('git', ['cat-file', '--batch'], {
  cwd: root, input: inv.files.map(x => x.sha).join('\n') + '\n',
  maxBuffer: 32 * 1024 * 1024
});
assert(!git.error && git.status === 0, 'baseline Git objects unavailable');
let offset = 0;
const contents = new Map();
for (const item of inv.files) {
  const newline = git.stdout.indexOf(10, offset);
  assert(newline >= 0, 'Git header missing');
  const header = git.stdout.subarray(offset, newline).toString('ascii');
  const match = /^([0-9a-f]{40}) blob ([0-9]+)$/.exec(header);
  assert(match && match[1] === item.sha, 'Git object header mismatch');
  const size = Number(match[2]), start = newline + 1;
  const bytes = git.stdout.subarray(start, start + size);
  assert(bytes.length === item.size_bytes && size === item.size_bytes, 'Git source size mismatch');
  const hash = crypto.createHash('sha1').update(Buffer.from('blob ' + size + '\0')).update(bytes).digest('hex');
  assert(hash === item.sha, 'Git source hash mismatch');
  contents.set(item.path, bytes.toString('utf8'));
  offset = start + size + 1;
}
assert(offset === git.stdout.length, 'unexpected Git batch tail');
for (const f of findings.findings) for (const ev of f.evidence) {
  const c = contents.get(ev.path);
  assert(c !== undefined && c.split('\n')[ev.line - 1] !== undefined, 'evidence coordinate');
  assert(inv.files.find(x => x.path === ev.path)?.sha === ev.blob_sha, 'evidence blob');
}
const children = new Set(data.migrations.flatMap(m => m.down_revisions));
const heads = data.migrations.filter(m => !children.has(m.revision)).map(m => m.revision);
assert(JSON.stringify(heads) === JSON.stringify(data.expected_heads), 'migration heads');
assert(new Set(data.migrations.map(m => m.revision)).size === 119, 'duplicate migration revision');
const sourceMain = contents.get('app/main.py');
assert(sourceMain.indexOf('app.include_router(mcp_metadata_router)') <
  sourceMain.indexOf('app.include_router(mcp_router)'), 'source route ordering changed');
assert(contents.get('app/api/mcp_metadata_routes.py').includes('router.include_router(communication_feed_router)'), 'nested MCP router');
assert(contents.get('app/api/communication_feed_mcp.py').includes('return await mcp_routes.mcp_endpoint('), 'source delegate');
const configAliases = [...contents.get('app/core/config.py').matchAll(/validation_alias=\(?\s*"([^"]+)"/g)].map(x => x[1]);
assert(configAliases.length === 161, 'source aliases');
const coreBlock = contents.get('app/api/mcp_routes.py').split('MCP_TOOL_NAMES = [')[1].split('\n]')[0];
assert((coreBlock.match(/"gate_[^"]+"/g) || []).length + 1 === 19, 'source core names');
const seenMcp = new Map();
for (const module of api.mount_order) for (const r of api.routes.filter(r => r.module === module)) {
  const key = r.method + ' ' + r.path;
  if (!seenMcp.has(key)) seenMcp.set(key, r.module);
}
assert(seenMcp.get('POST /mcp') === 'app/api/communication_feed_mcp.py', 'first MCP match');
let links = 0;
for (const name of fs.readdirSync(here).filter(x => x.endsWith('.md'))) {
  const text = fs.readFileSync(path.join(here, name), 'utf8');
  assert((text.match(/^```/gm) || []).length % 2 === 0, 'unclosed Markdown fence: ' + name);
  for (const m of text.matchAll(/\[[^\]]+\]\(([^)\s]+)\)/g)) {
    const href = m[1];
    if (/^[a-z]+:\/\//i.test(href) || href.startsWith('#')) continue;
    const target = path.resolve(here, href.split('#')[0]);
    assert(target.startsWith(root + path.sep), 'link escapes repository');
    assert(fs.existsSync(target), 'missing relative link: ' + name + ' -> ' + href);
    links++;
  }
}
console.log(JSON.stringify({result:'PASS', source_blobs:943, source_bytes:inv.verification.total_bytes,
  tables:113, migrations:119, config_aliases:161, issues:34, prs:14, findings:16,
  relative_links:links, limitation:'Static audit consistency, not product/runtime acceptance'}));
