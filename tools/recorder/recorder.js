'use strict';

const fs = require('fs');
const zlib = require('zlib');

const SKIP_PACKET_BODY_NAMES = new Set([
  'map_chunk',
  'map_chunk_bulk',
  'chunk_data',
  'level_chunk_with_light',
  'update_light',
  'login',
  'registry_data',
]);

function jsonSafe(value, depth = 0, seen = new WeakSet()) {
  if (depth > 8) return '[MaxDepth]';
  if (value === null || value === undefined) return value;
  if (typeof value === 'bigint') return value.toString();
  if (typeof value !== 'object') return value;
  if (Buffer.isBuffer(value)) return { $buffer: value.toString('base64') };
  if (seen.has(value)) return '[Circular]';
  seen.add(value);
  if (Array.isArray(value)) return value.map((item) => jsonSafe(item, depth + 1, seen));
  const out = {};
  for (const [key, child] of Object.entries(value)) {
    out[key] = jsonSafe(child, depth + 1, seen);
  }
  return out;
}

function packetDataForLog(name, data) {
  if (SKIP_PACKET_BODY_NAMES.has(name)) {
    return { omitted: true, reason: 'large_or_replay_raw_preferred' };
  }
  return jsonSafe(data);
}

function createRecorder(client, opts = {}) {
  const output = opts.output;
  const manifest = opts.manifest;
  const host = opts.host;
  const port = opts.port;
  const username = opts.username;
  const targetUsername = opts.targetUsername;
  const onLog = opts.onLog || (() => {});

  if (!output) return null;

  fs.mkdirSync(require('path').dirname(output), { recursive: true });
  const file = fs.createWriteStream(output);
  const gzip = zlib.createGzip({ level: 6 });
  gzip.pipe(file);

  const startedAt = Date.now();
  const stats = {
    inbound: 0,
    outbound: 0,
    inboundBytes: 0,
    packetNames: {},
  };

  function writeEvent(event) {
    gzip.write(JSON.stringify(event) + '\n');
  }

  function rememberPacket(name) {
    stats.packetNames[name] = (stats.packetNames[name] || 0) + 1;
  }

  writeEvent({
    kind: 'meta',
    data: {
      schema: 'mcbench.packetlog.v1',
      host,
      port,
      username,
      targetUsername,
      createdAt: new Date(startedAt).toISOString(),
    },
  });

  client.on('packet', (data, metadata, buffer, fullBuffer) => {
    const now = Date.now();
    const raw = Buffer.isBuffer(buffer) ? buffer : fullBuffer;
    const rawLength = Buffer.isBuffer(raw) ? raw.length : 0;
    stats.inbound += 1;
    stats.inboundBytes += rawLength;
    rememberPacket(metadata.name);
    writeEvent({
      kind: 'packet',
      dir: 'in',
      t: (now - startedAt) / 1000,
      state: metadata.state,
      name: metadata.name,
      id: metadata.id,
      raw: Buffer.isBuffer(raw) ? raw.toString('base64') : null,
      rawLength,
      data: packetDataForLog(metadata.name, data),
    });
  });

  const originalWrite = client.write.bind(client);
  client.write = (name, params) => {
    stats.outbound += 1;
    writeEvent({
      kind: 'packet',
      dir: 'out',
      t: (Date.now() - startedAt) / 1000,
      state: client.state,
      name,
      data: jsonSafe(params || {}),
    });
    return originalWrite(name, params);
  };

  return {
    async stop() {
      const endedAt = Date.now();
      writeEvent({
        kind: 'end',
        data: {
          endedAt: new Date(endedAt).toISOString(),
          durationSeconds: (endedAt - startedAt) / 1000,
        },
      });
      await new Promise((resolve, reject) => {
        let settled = false;
        const done = () => {
          if (settled) return;
          settled = true;
          resolve();
        };
        const fail = (err) => {
          if (settled) return;
          settled = true;
          reject(err);
        };
        file.once('finish', done);
        file.once('error', fail);
        gzip.once('error', fail);
        gzip.end();
      });
      if (manifest) {
        fs.writeFileSync(manifest, JSON.stringify({
          schema: 'mcbench.packetlog.v1',
          output,
          startedAt: new Date(startedAt).toISOString(),
          endedAt: new Date(endedAt).toISOString(),
          durationSeconds: (endedAt - startedAt) / 1000,
          minecraftVersion: client.version,
          host,
          port,
          username,
          targetUsername,
          stats,
        }, null, 2));
      }
      onLog('packet_capture_saved', { output, manifest, inbound: stats.inbound, outbound: stats.outbound });
    },
  };
}

module.exports = { createRecorder };
