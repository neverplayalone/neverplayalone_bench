// Sidecar packet recorder bot. Connects to the server as a spectator, attaches
// to the target agent, and writes packets that can be exported to ReplayMod.
//
// Env vars (set by the Python runner):
//   MCBENCH_REC_HOST       (default: 127.0.0.1)
//   MCBENCH_REC_PORT       (default: 25565)
//   MCBENCH_REC_USERNAME   (default: BenchmarkRecorder)
//   MCBENCH_REC_TARGET     (the agent's username to spectate)
//   MCBENCH_REC_PACKET_OUTPUT
//   MCBENCH_REC_PACKET_MANIFEST
//
// Lifecycle: runs until SIGTERM; flushes the packet log cleanly on shutdown.

const mineflayer = require('mineflayer');
const { createPacketRecorder } = require('./packet-recorder');

const HOST = process.env.MCBENCH_REC_HOST || '127.0.0.1';
const PORT = parseInt(process.env.MCBENCH_REC_PORT || '25565', 10);
const USERNAME = process.env.MCBENCH_REC_USERNAME || 'BenchmarkRecorder';
const TARGET = process.env.MCBENCH_REC_TARGET || '';
const PACKET_OUTPUT = process.env.MCBENCH_REC_PACKET_OUTPUT;
const PACKET_MANIFEST = process.env.MCBENCH_REC_PACKET_MANIFEST;

if (!PACKET_OUTPUT) {
  console.error('MCBENCH_REC_PACKET_OUTPUT is required');
  process.exit(2);
}

function log(msg, extra = {}) {
  process.stderr.write(`[recorder] ${msg} ${JSON.stringify(extra)}\n`);
}

const bot = mineflayer.createBot({
  host: HOST, port: PORT, username: USERNAME,
  version: false,
  auth: 'offline',
});

let shuttingDown = false;
let packetRecorder = createPacketRecorder(bot._client, {
  output: PACKET_OUTPUT,
  manifest: PACKET_MANIFEST,
  host: HOST,
  port: PORT,
  username: USERNAME,
  targetUsername: TARGET,
  onLog: log,
});
let packetRecorderStopped = false;

function stopPackets() {
  if (!packetRecorder || packetRecorderStopped) return Promise.resolve();
  packetRecorderStopped = true;
  return Promise.resolve(packetRecorder.stop()).catch((err) => {
    log('packet recorder stop failed', { err: String(err) });
  });
}

bot.once('spawn', () => {
  log('spawned, attempting to spectate', { target: TARGET });

  // Become a spectator and attach to the target.
  bot.chat('/gamemode spectator');
  if (TARGET) {
    setTimeout(() => bot.chat(`/spectate ${TARGET}`), 500);
  }
  log('recording packets', { output: PACKET_OUTPUT });
});

bot.on('kicked', (reason) => log('kicked', { reason: String(reason) }));
bot.on('error', (err) => log('error', { err: String(err) }));
bot.on('end', () => {
  log('disconnected');
  if (!shuttingDown) process.exit(0);
});

function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  log('shutdown', { signal });
  try { bot.quit(); } catch {}

  let exited = false;
  const finish = (code = 0) => {
    if (exited) return;
    exited = true;
    process.exit(code);
  };
  const packetStop = stopPackets();
  packetStop.then(() => finish(0));

  // Give gzip time to flush the packet log, then force exit so shutdown cannot
  // hang the benchmark.
  setTimeout(() => finish(0), 8000);
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));
