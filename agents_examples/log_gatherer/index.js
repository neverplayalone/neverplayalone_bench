// Reference agent for the resource-gathering validator path.
//
// It gathers standard overworld logs, keeps them in inventory, and returns near
// spawn before finishing. This is intentionally simple: it is a baseline for
// verifying the resource-gathering scoring path, not a competitive miner.

const mineflayer = require('mineflayer');
const { pathfinder, Movements, goals } = require('mineflayer-pathfinder');

const host = process.env.MCBENCH_HOST || '127.0.0.1';
const port = parseInt(process.env.MCBENCH_PORT || '25565', 10);
const username = process.env.MCBENCH_USERNAME || 'BenchmarkBot';
const goalText = process.env.MCBENCH_GOAL || '';
const timeoutSec = parseInt(process.env.MCBENCH_TIMEOUT || '1200', 10);

const LOG_NAMES = [
  'oak_log',
  'birch_log',
  'spruce_log',
  'jungle_log',
  'acacia_log',
  'dark_oak_log',
  'mangrove_log',
  'cherry_log',
];

function emit(kind, data = {}) {
  process.stdout.write(JSON.stringify({ kind, data, t: Date.now() / 1000 }) + '\n');
}

function targetFromGoal(goal) {
  const match = goal.match(/\bgather\s+(\d+)\b/i) || goal.match(/\bcollect\s+(\d+)\b/i);
  return match ? parseInt(match[1], 10) : 64;
}

function isLogItem(item) {
  return item && LOG_NAMES.includes(item.name);
}

function countInventoryLogs(bot) {
  return bot.inventory.items().filter(isLogItem).reduce((sum, item) => sum + item.count, 0);
}

function inventorySummary(bot) {
  const out = {};
  for (const item of bot.inventory.items()) {
    out[item.name] = (out[item.name] || 0) + item.count;
  }
  return out;
}

async function safeWait(bot, ticks) {
  try {
    await bot.waitForTicks(ticks);
    return true;
  } catch (e) {
    emit('info', { msg: 'wait failed', ticks, err: String(e) });
    return false;
  }
}

async function waitForKit(bot) {
  for (let i = 0; i < 200; i += 1) {
    const hasAxe = bot.inventory.items().some((item) => item.name.endsWith('_axe'));
    if (hasAxe) return true;
    await safeWait(bot, 2);
  }
  return false;
}

async function equipAxe(bot) {
  const axe = bot.inventory.items().find((item) => item.name.endsWith('_axe'));
  if (!axe) return false;
  try {
    await bot.equip(axe, 'hand');
    return true;
  } catch (e) {
    emit('error', { msg: 'equip axe failed', item: axe.name, err: String(e) });
    return false;
  }
}

function findNearestLog(bot, mcData) {
  const ids = LOG_NAMES
    .map((name) => mcData.blocksByName[name] && mcData.blocksByName[name].id)
    .filter((id) => id != null);
  const positions = bot.findBlocks({
    matching: ids,
    maxDistance: 64,
    count: 64,
  });
  const blocks = positions
    .map((pos) => bot.blockAt(pos))
    .filter((block) => block && bot.canDigBlock(block));
  if (!blocks.length) return null;
  blocks.sort((a, b) =>
    a.position.distanceTo(bot.entity.position) - b.position.distanceTo(bot.entity.position)
  );
  return blocks[0];
}

async function collectNearbyDrops(bot) {
  for (let i = 0; i < 12; i += 1) {
    const item = bot.nearestEntity((entity) =>
      entity.name === 'item' && entity.position.distanceTo(bot.entity.position) < 18
    );
    if (!item) return;
    try {
      await bot.pathfinder.goto(new goals.GoalNear(item.position.x, item.position.y, item.position.z, 1));
      await safeWait(bot, 6);
    } catch (e) {
      emit('info', { msg: 'collect failed', err: String(e) });
      return;
    }
  }
}

async function returnToSpawn(bot, spawnPos) {
  if (!spawnPos) return false;
  try {
    await bot.pathfinder.goto(new goals.GoalNear(spawnPos.x, spawnPos.y, spawnPos.z, 8));
    emit('action', { action: 'return_to_spawn', pos: spawnPos });
    await safeWait(bot, 5);
    return true;
  } catch (e) {
    emit('error', { msg: 'return to spawn failed', err: String(e) });
    return false;
  }
}

async function wander(bot) {
  const dx = Math.floor(Math.random() * 41) - 20;
  const dz = Math.floor(Math.random() * 41) - 20;
  try {
    await bot.pathfinder.goto(
      new goals.GoalNear(bot.entity.position.x + dx, bot.entity.position.y, bot.entity.position.z + dz, 2)
    );
  } catch (e) {
    emit('info', { msg: 'wander failed', err: String(e) });
  }
}

const bot = mineflayer.createBot({
  host,
  port,
  username,
  version: false,
  auth: 'offline',
});

bot.loadPlugin(pathfinder);

const targetCount = targetFromGoal(goalText);
const deadline = Date.now() + Math.max(1, timeoutSec - 30) * 1000;
let finished = false;
let mined = 0;
let stopRequested = false;

function finish(reason) {
  if (finished) return;
  stopRequested = true;
  finished = true;
  try { bot.pathfinder.stop(); } catch (e) { /* ignore */ }
  emit('done', {
    msg: reason,
    mined,
    gathered: countInventoryLogs(bot),
    inventory: inventorySummary(bot),
  });
}

bot.once('spawn', async () => {
  emit('ready', { goal: goalText, targetCount });
  const mcData = require('minecraft-data')(bot.version);
  const movements = new Movements(bot, mcData);
  movements.canDig = true;
  bot.pathfinder.setMovements(movements);

  const kitReady = await waitForKit(bot);
  const spawnPos = bot.entity.position.clone();
  emit('info', { msg: 'spawned', kitReady, spawnPos });
  setTimeout(() => finish('time budget exhausted'), Math.max(1, timeoutSec - 30) * 1000);

  while (!stopRequested && Date.now() < deadline - 1000) {
    if (countInventoryLogs(bot) >= targetCount) {
      await returnToSpawn(bot, spawnPos);
      finish('target gathered');
      return;
    }

    const block = findNearestLog(bot, mcData);
    if (!block) {
      await wander(bot);
      continue;
    }

    try {
      await equipAxe(bot);
      await bot.pathfinder.goto(new goals.GoalNear(block.position.x, block.position.y, block.position.z, 1));
      emit('action', { action: 'dig_log', block: block.name, pos: block.position });
      await bot.dig(block);
      mined += 1;
      await safeWait(bot, 4);
      await collectNearbyDrops(bot);
    } catch (e) {
      emit('error', { msg: 'dig failed', block: block.name, err: String(e) });
      await safeWait(bot, 8);
    }
  }

  await returnToSpawn(bot, spawnPos);
  finish('time budget exhausted');
});

bot.on('death', () => {
  emit('dead', { msg: 'log gatherer died' });
  try { bot.respawn(); } catch (e) { /* ignore */ }
});
bot.on('kicked', (reason) => emit('error', { msg: 'kicked', reason: String(reason) }));
bot.on('error', (err) => emit('error', { msg: 'bot error', err: String(err) }));
process.on('unhandledRejection', (err) => emit('error', { msg: 'unhandled rejection', err: String(err) }));
bot.on('end', () => {
  emit('info', { msg: 'disconnected' });
  process.exit(0);
});

async function shutdown() {
  stopRequested = true;
  try { bot.pathfinder.stop(); } catch (e) { /* ignore */ }
  try { bot.quit(); } catch (e) { /* ignore */ }
}

process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
