// Sidecar recorder bot. Connects to the server as a spectator, attaches its
// camera to the target agent, and records an MP4 via prismarine-viewer's
// headless mode (which pipes frames to ffmpeg). Requires ffmpeg on PATH.
//
// Env vars (set by the Python runner):
//   MCBENCH_REC_HOST       (default: 127.0.0.1)
//   MCBENCH_REC_PORT       (default: 25565)
//   MCBENCH_REC_USERNAME   (default: BenchmarkRecorder)
//   MCBENCH_REC_TARGET     (the agent's username to spectate)
//   MCBENCH_REC_OUTPUT     (absolute path to .mp4)
//   MCBENCH_REC_WIDTH      (default: 640)
//   MCBENCH_REC_HEIGHT     (default: 480)
//   MCBENCH_REC_FPS        (default: 20)
//   MCBENCH_REC_POV        (first|third, default: first)
//
// Lifecycle: runs until SIGTERM; flushes the MP4 cleanly on shutdown.

const mineflayer = require('mineflayer');

const HOST = process.env.MCBENCH_REC_HOST || '127.0.0.1';
const PORT = parseInt(process.env.MCBENCH_REC_PORT || '25565', 10);
const USERNAME = process.env.MCBENCH_REC_USERNAME || 'BenchmarkRecorder';
const TARGET = process.env.MCBENCH_REC_TARGET || '';
const OUTPUT = process.env.MCBENCH_REC_OUTPUT;
const WIDTH = parseInt(process.env.MCBENCH_REC_WIDTH || '640', 10);
const HEIGHT = parseInt(process.env.MCBENCH_REC_HEIGHT || '480', 10);
const FPS = parseInt(process.env.MCBENCH_REC_FPS || '20', 10);
const POV = (process.env.MCBENCH_REC_POV || 'first').toLowerCase();

if (!OUTPUT) {
  console.error('MCBENCH_REC_OUTPUT is required');
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

let viewerStarted = false;
let shuttingDown = false;
let ffmpegClient = null;
let followTimer = null;

function mirrorCameraToTarget() {
  if (!TARGET || !bot.entity) return;
  const target = bot.players[TARGET]?.entity;
  if (!target) return;

  if (POV === 'third') {
    bot.entity.position = target.position.offset(0, 2.8, 0);
    bot.entity.yaw = target.yaw;
    bot.entity.pitch = -80 * Math.PI / 180;
    bot.emit('move');
    return;
  }

  // Move the render camera slightly in front of the target's head so the
  // target entity model does not occlude the first-person view.
  const forwardX = -Math.sin(target.yaw) * 0.45;
  const forwardZ = -Math.cos(target.yaw) * 0.45;
  bot.entity.position = target.position.offset(forwardX, 0, forwardZ);
  bot.entity.yaw = target.yaw;
  bot.entity.pitch = target.pitch;
  bot.emit('move');
}

bot.once('spawn', () => {
  log('spawned, attempting to spectate', { target: TARGET, pov: POV });

  // Become a spectator and attach to the target.
  bot.chat('/gamemode spectator');
  if (TARGET && POV !== 'third') {
    setTimeout(() => bot.chat(`/spectate ${TARGET}`), 500);
  }

  // prismarine-viewer's headless export writes via ffmpeg to OUTPUT.
  // Loaded lazily so a missing canvas/ffmpeg fails loudly only when --record is used.
  let headless;
  try {
    headless = require('prismarine-viewer').headless;
  } catch (err) {
    log('failed to load prismarine-viewer', { err: String(err) });
    process.exit(3);
  }

  try {
    ffmpegClient = headless(bot, {
      output: OUTPUT,
      frames: -1,            // record until killed
      width: WIDTH,
      height: HEIGHT,
      firstPerson: POV !== 'third',
      fps: FPS,
      logFFMPEG: process.env.MCBENCH_REC_LOG_FFMPEG === '1',
    });
    viewerStarted = true;
    followTimer = setInterval(mirrorCameraToTarget, Math.max(20, Math.floor(1000 / FPS)));
    log('recording', { output: OUTPUT, width: WIDTH, height: HEIGHT, fps: FPS });
  } catch (err) {
    log('headless start failed', { err: String(err) });
    process.exit(4);
  }
});

bot.on('kicked', (reason) => log('kicked', { reason: String(reason) }));
bot.on('error', (err) => log('error', { err: String(err) }));
bot.on('end', () => {
  log('disconnected');
  if (!shuttingDown) process.exit(viewerStarted ? 0 : 5);
});

function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  log('shutdown', { signal });
  if (followTimer) clearInterval(followTimer);
  try { bot.quit(); } catch {}

  let exited = false;
  const finish = (code = 0) => {
    if (exited) return;
    exited = true;
    process.exit(code);
  };

  if (ffmpegClient) {
    ffmpegClient.once('close', (code) => {
      log('ffmpeg closed', { code });
      finish(0);
    });
  }

  // Give ffmpeg time to flush the MP4 trailer, then force exit so shutdown
  // cannot hang the benchmark.
  setTimeout(() => finish(0), 8000);
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));
