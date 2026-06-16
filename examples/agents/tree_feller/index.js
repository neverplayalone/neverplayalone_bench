// Improved reference agent: a cluster-harvesting "tree feller".
//
// Strategy follows the scoring rule
//   score = (gathered / target) * 100 * distance_multiplier   (time is only a tiebreaker)
// so it:
//   1) maximizes throughput — harvests many logs per scan, skips the baseline's
//      expensive "path to every dropped item" step (drops auto-pickup when the
//      bot is adjacent), and only targets ground-reachable trunk logs so it never
//      wastes the clock pathing to unreachable canopy;
//   2) spends the whole time budget gathering; then
//   3) returns to within a few blocks of spawn for the full distance multiplier.
//
// Every navigation/dig is wrapped in a hard timeout, so a stuck path can never
// hang the run — it just skips that block and moves on.
//
// Logs-specialized (a log block's name equals its item name). To generalize to
// cobblestone/coal/sand, map the goal's resource to the blocks to mine (note
// stone->cobblestone and coal_ore->coal differ from the item name).

const mineflayer = require('mineflayer');
const { pathfinder, Movements, goals } = require('mineflayer-pathfinder');

const host = process.env.MCBENCH_HOST || '127.0.0.1';
const port = parseInt(process.env.MCBENCH_PORT || '25565', 10);
const username = process.env.MCBENCH_USERNAME || 'BenchmarkBot';
const goalText = process.env.MCBENCH_GOAL || '';
const timeoutSec = parseInt(process.env.MCBENCH_TIMEOUT || '1200', 10);

const LOG_NAMES = [
  'oak_log', 'birch_log', 'spruce_log', 'jungle_log',
  'acacia_log', 'dark_oak_log', 'mangrove_log', 'cherry_log',
];

const WALK_BPS = 3.2;         // conservative speed, so we always make it home
const RETURN_SAFETY_SEC = 6;  // hard stop this many seconds before the kill timeout
const SEARCH_RADIUS = 96;
const GOTO_TIMEOUT_MS = 5000;
const DIG_TIMEOUT_MS = 5000;
const REACH_UP = 2;           // only walk toward logs within this many blocks above feet
const REACH_DOWN = 4;

function emit(kind, data = {}) {
  process.stdout.write(JSON.stringify({ kind, data, t: Date.now() / 1000 }) + '\n');
}

function targetFromGoal(goal) {
  const m = goal.match(/\b(?:gather|collect)\s+(\d+)\b/i);
  return m ? parseInt(m[1], 10) : 64;
}

function countLogs(bot) {
  return bot.inventory.items()
    .filter((it) => LOG_NAMES.includes(it.name))
    .reduce((sum, it) => sum + it.count, 0);
}

function inventorySummary(bot) {
  const out = {};
  for (const it of bot.inventory.items()) out[it.name] = (out[it.name] || 0) + it.count;
  return out;
}

function horizDistance(a, b) {
  const dx = a.x - b.x;
  const dz = a.z - b.z;
  return Math.sqrt(dx * dx + dz * dz);
}

// Reject after `ms` so a stuck pathfinder/dig can never hang the whole run.
function withTimeout(promise, ms) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error('timeout')), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

async function safeWait(bot, ticks) {
  try { await bot.waitForTicks(ticks); } catch (e) { /* ignore */ }
}

async function waitForKit(bot) {
  for (let i = 0; i < 200; i += 1) {
    if (bot.inventory.items().some((it) => it.name.endsWith('_axe'))) return true;
    await safeWait(bot, 2);
  }
  return false;
}

async function equipAxe(bot) {
  const axe = bot.inventory.items().find((it) => it.name.endsWith('_axe'));
  if (!axe) return;
  try { await bot.equip(axe, 'hand'); } catch (e) { /* ignore */ }
}

function logBlockIds(bot, mcData) {
  return LOG_NAMES
    .map((n) => mcData.blocksByName[n] && mcData.blocksByName[n].id)
    .filter((id) => id != null);
}

// Ground-reachable logs near the bot, nearest first (skip canopy we can't climb to).
function reachableLogs(bot, ids) {
  const me = bot.entity.position;
  return bot.findBlocks({ matching: ids, maxDistance: SEARCH_RADIUS, count: 128 })
    .map((p) => bot.blockAt(p))
    .filter((b) => {
      if (!b || !LOG_NAMES.includes(b.name)) return false;
      const dy = b.position.y - me.y;
      return dy <= REACH_UP && dy >= -REACH_DOWN;
    })
    .sort((a, b) => a.position.distanceTo(me) - b.position.distanceTo(me));
}

// Strip every log currently within arm's reach (no pathfinding per block). After
// walking to a tree this fells the reachable trunk + neighbouring trunks, so one
// expensive path yields many logs instead of one.
async function digReachableLogs(bot, ids) {
  let dug = 0;
  for (let pass = 0; pass < 6; pass += 1) {
    if (stopRequested) break;
    const targets = bot.findBlocks({ matching: ids, maxDistance: 5, count: 24 })
      .map((p) => bot.blockAt(p))
      .filter((b) => b && LOG_NAMES.includes(b.name) && bot.canDigBlock(b));
    if (!targets.length) break;
    for (const b of targets) {
      if (stopRequested) return dug;
      try {
        await withTimeout(bot.lookAt(b.position.offset(0.5, 0.5, 0.5), true), 1500);
        await withTimeout(bot.dig(b), DIG_TIMEOUT_MS);
        dug += 1;
        await safeWait(bot, 1);   // brief pause; drops auto-pickup while adjacent
      } catch (e) { /* out of reach now / interrupted — skip */ }
    }
  }
  return dug;
}

const bot = mineflayer.createBot({ host, port, username, version: false, auth: 'offline' });
bot.loadPlugin(pathfinder);

const targetCount = targetFromGoal(goalText);
let stopRequested = false;
let finished = false;

bot.once('spawn', async () => {
  emit('ready', { goal: goalText, targetCount });
  const mcData = require('minecraft-data')(bot.version);
  const movements = new Movements(bot, mcData);
  movements.canDig = true;          // dig through leaves to reach trunks
  movements.allowSprinting = true;
  bot.pathfinder.setMovements(movements);
  bot.pathfinder.thinkTimeout = 3000;   // fail unreachable paths fast, don't burn the clock

  const kitReady = await waitForKit(bot);
  await equipAxe(bot);
  const spawnPos = bot.entity.position.clone();
  emit('info', { msg: 'spawned', kitReady, spawnPos });

  const ids = logBlockIds(bot, mcData);
  const hardStopMs = Date.now() + Math.max(1, timeoutSec - RETURN_SAFETY_SEC) * 1000;
  const guard = setTimeout(() => {
    stopRequested = true;
    try { bot.pathfinder.stop(); } catch (e) { /* ignore */ }
  }, Math.max(1, timeoutSec - RETURN_SAFETY_SEC) * 1000);

  function timeLeftSec() { return (hardStopMs - Date.now()) / 1000; }
  function mustReturnNow() {
    return timeLeftSec() <= horizDistance(bot.entity.position, spawnPos) / WALK_BPS + 1;
  }

  async function gotoNear(pos, range) {
    try { bot.pathfinder.setGoal(null); } catch (e) { /* ignore */ }
    await withTimeout(
      bot.pathfinder.goto(new goals.GoalNear(pos.x, pos.y, pos.z, range)),
      GOTO_TIMEOUT_MS,
    );
  }

  async function hopOutward() {
    const angle = Math.random() * Math.PI * 2;
    const tx = bot.entity.position.x + Math.cos(angle) * 24;
    const tz = bot.entity.position.z + Math.sin(angle) * 24;
    try { await gotoNear({ x: tx, y: bot.entity.position.y, z: tz }, 3); } catch (e) { /* ignore */ }
  }

  async function returnToSpawn() {
    for (const radius of [2, 4, 8]) {
      try { await gotoNear(spawnPos, radius); break; } catch (e) { /* looser radius */ }
    }
    emit('action', {
      action: 'return_to_spawn',
      distanceFromSpawn: horizDistance(bot.entity.position, spawnPos),
    });
  }

  function finish(reason) {
    if (finished) return;
    finished = true;
    clearTimeout(guard);
    emit('done', {
      msg: reason,
      gathered: countLogs(bot),
      distanceFromSpawn: horizDistance(bot.entity.position, spawnPos),
      inventory: inventorySummary(bot),
    });
  }

  let reason = 'time budget exhausted';
  let idleScans = 0;
  try {
    while (!stopRequested) {
      if (countLogs(bot) >= targetCount) { reason = 'target gathered'; break; }
      if (mustReturnNow()) { reason = 'returning before deadline'; break; }

      const logs = reachableLogs(bot, ids);
      if (!logs.length) {
        if ((idleScans += 1) > 10) { reason = 'no reachable logs'; break; }
        await hopOutward();
        continue;
      }
      idleScans = 0;

      // Walk to the nearest tree ONCE, then strip every log within reach.
      let reachedTree = false;
      try { await gotoNear(logs[0].position, 2); reachedTree = true; }
      catch (e) { try { bot.pathfinder.stop(); } catch (_) { /* ignore */ } }

      const dug = reachedTree ? await digReachableLogs(bot, ids) : 0;
      emit('info', { msg: 'progress', gathered: countLogs(bot), dug, timeLeft: Math.round(timeLeftSec()) });
      if (dug === 0) await hopOutward();   // unstick if we couldn't reach/cut anything
    }
  } catch (e) {
    emit('error', { msg: 'main loop error', err: String(e) });
  }

  await returnToSpawn();
  finish(reason);
});

bot.on('death', () => { emit('dead', {}); try { bot.respawn(); } catch (e) { /* ignore */ } });
bot.on('kicked', (r) => emit('error', { msg: 'kicked', reason: String(r) }));
bot.on('error', (e) => emit('error', { msg: 'bot error', err: String(e) }));
process.on('unhandledRejection', (e) => emit('error', { msg: 'unhandled rejection', err: String(e) }));
bot.on('end', () => { emit('info', { msg: 'disconnected' }); process.exit(0); });

async function shutdown() {
  stopRequested = true;
  try { bot.pathfinder.stop(); } catch (e) { /* ignore */ }
  try { bot.quit(); } catch (e) { /* ignore */ }
}
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
