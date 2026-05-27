// Reference mineflayer agent for the mcbench harness.
//
// Reads connection info from env vars set by SubprocessAgent:
//   MCBENCH_HOST, MCBENCH_PORT, MCBENCH_USERNAME, MCBENCH_GOAL, MCBENCH_TIMEOUT
//
// Emits one JSON line per event on stdout. The harness parses these into the trace.
//
// This is a deliberately dumb agent: it tries to dig the first block name mentioned
// in the goal until time runs out. Replace with your own logic.

const mineflayer = require('mineflayer');
const { pathfinder, Movements, goals } = require('mineflayer-pathfinder');

const host = process.env.MCBENCH_HOST || '127.0.0.1';
const port = parseInt(process.env.MCBENCH_PORT || '25565', 10);
const username = process.env.MCBENCH_USERNAME || 'BenchmarkBot';
const goal = process.env.MCBENCH_GOAL || '';
const timeoutSec = parseInt(process.env.MCBENCH_TIMEOUT || '120', 10);

function emit(kind, data = {}) {
  process.stdout.write(JSON.stringify({ kind, data, t: Date.now() / 1000 }) + '\n');
}

function pickTargetBlock(goalText) {
  // Heuristic: find the first known block name mentioned in the goal.
  const normalized = goalText.toLowerCase().replace(/[\s-]+/g, '_');
  const candidates = ['oak_log', 'birch_log', 'spruce_log', 'cobblestone', 'stone', 'iron_ore', 'coal_ore', 'dirt', 'sand'];
  for (const name of candidates) {
    if (normalized.includes(name)) return name;
  }
  return 'oak_log';
}

function pickTargetCount(goalText) {
  const match = goalText.match(/\b(\d+)\b/);
  return match ? parseInt(match[1], 10) : null;
}

function inventoryCount(bot, itemName) {
  return bot.inventory.items()
    .filter((item) => item.name === itemName)
    .reduce((total, item) => total + item.count, 0);
}

async function collectNearbyDrops(bot) {
  for (let i = 0; i < 8; i += 1) {
    const item = bot.nearestEntity((entity) =>
      entity.name === 'item' && entity.position.distanceTo(bot.entity.position) < 16
    );
    if (!item) return;
    try {
      await bot.pathfinder.goto(new goals.GoalNear(item.position.x, item.position.y, item.position.z, 1));
      await bot.waitForTicks(10);
    } catch (e) {
      emit('error', { msg: 'collect failed', err: String(e) });
      return;
    }
  }
}

async function waitForRunnerSetup(bot, target) {
  for (let i = 0; i < 100; i += 1) {
    const hasTool = bot.inventory.items().some((item) => item.name.endsWith('_axe'));
    if (hasTool && inventoryCount(bot, target) === 0) return;
    await bot.waitForTicks(1);
  }
}

const bot = mineflayer.createBot({
  host, port, username,
  version: false,   // auto-detect
  auth: 'offline',
});

bot.loadPlugin(pathfinder);

const deadline = Date.now() + timeoutSec * 1000;
const target = pickTargetBlock(goal);
const targetCount = pickTargetCount(goal);
let finished = false;

bot.once('spawn', async () => {
  emit('ready', { target, targetCount, goal });
  emit('info', { msg: 'spawned', target, targetCount, goal });
  const mcData = require('minecraft-data')(bot.version);
  const movements = new Movements(bot, mcData);
  movements.canDig = true;
  bot.pathfinder.setMovements(movements);
  await waitForRunnerSetup(bot, target);

  const blockId = mcData.blocksByName[target]?.id;
  if (blockId == null) {
    emit('error', { msg: `unknown block: ${target}` });
    bot.quit();
    return;
  }

  let successfulDigs = 0;
  while (Date.now() < deadline && (!targetCount || inventoryCount(bot, target) < targetCount)) {
    const blockPos = bot.findBlock({ matching: blockId, maxDistance: 32 });
    if (!blockPos) {
      emit('info', { msg: 'no target in range, wandering' });
      // step in a random direction to expose new chunks
      const dx = Math.floor(Math.random() * 10) - 5;
      const dz = Math.floor(Math.random() * 10) - 5;
      try {
        await bot.pathfinder.goto(new goals.GoalNear(bot.entity.position.x + dx, bot.entity.position.y, bot.entity.position.z + dz, 1));
      } catch (e) {
        emit('error', { msg: 'goto failed', err: String(e) });
      }
      continue;
    }
    try {
      await bot.pathfinder.goto(new goals.GoalGetToBlock(blockPos.position.x, blockPos.position.y, blockPos.position.z));
      emit('action', { action: 'dig', block: target, pos: blockPos.position });
      await bot.dig(blockPos);
      successfulDigs += 1;
      await bot.waitForTicks(10);
      await collectNearbyDrops(bot);
    } catch (e) {
      emit('error', { msg: 'dig failed', err: String(e) });
    }
  }

  finished = true;
  const finalCount = inventoryCount(bot, target);
  emit('done', {
    msg: targetCount && finalCount >= targetCount ? 'target reached' : 'time up',
    successfulDigs,
    inventory: { [target]: finalCount },
  });
});

bot.on('kicked', (reason) => emit('error', { msg: 'kicked', reason: String(reason) }));
bot.on('error', (err) => emit('error', { msg: 'bot error', err: String(err) }));
bot.on('end', () => {
  emit('info', { msg: 'disconnected' });
  process.exit(0);
});

function shutdown() {
  if (!finished) emit('done', { msg: 'shutdown' });
  try { bot.quit(); } catch {}
}

process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
