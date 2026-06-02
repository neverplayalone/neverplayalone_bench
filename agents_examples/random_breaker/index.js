// Random breaker for the resource-gathering competition.
//
// This is a deliberately simple baseline, not a strong miner. It searches the
// currently loaded world for useful resource source blocks, randomly picks one,
// mines it with the best available tool, collects nearby drops, and wanders or
// digs downward when nothing useful is visible.

const mineflayer = require('mineflayer');
const { pathfinder, Movements, goals } = require('mineflayer-pathfinder');

const host = process.env.MCBENCH_HOST || '127.0.0.1';
const port = parseInt(process.env.MCBENCH_PORT || '25565', 10);
const username = process.env.MCBENCH_USERNAME || 'BenchmarkBot';
const goalText = process.env.MCBENCH_GOAL || '';
const timeoutSec = parseInt(process.env.MCBENCH_TIMEOUT || '1200', 10);

const RESOURCE_SOURCES = [
  // Surface resources.
  'oak_log',
  'birch_log',
  'spruce_log',
  'jungle_log',
  'acacia_log',
  'dark_oak_log',
  'mangrove_log',
  'cherry_log',
  // Bulk mining and ores. Include deepslate variants for natural worlds.
  'stone',
  'cobblestone',
  'deepslate',
  'cobbled_deepslate',
  'coal_ore',
  'deepslate_coal_ore',
  'iron_ore',
  'deepslate_iron_ore',
  'gold_ore',
  'deepslate_gold_ore',
  'redstone_ore',
  'deepslate_redstone_ore',
  'lapis_ore',
  'deepslate_lapis_ore',
  'diamond_ore',
  'deepslate_diamond_ore',
  'emerald_ore',
  'deepslate_emerald_ore',
];

function emit(kind, data = {}) {
  process.stdout.write(JSON.stringify({ kind, data, t: Date.now() / 1000 }) + '\n');
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
    emit('info', { msg: 'waitForTicks timed out', ticks, err: String(e) });
    return false;
  }
}

function shuffled(items) {
  const copy = [...items];
  for (let i = copy.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
}

function preferredTool(blockName) {
  if (blockName.endsWith('_log')) return (item) => item.name.endsWith('_axe');
  if (blockName.includes('dirt') || blockName.includes('gravel') || blockName.includes('sand')) {
    return (item) => item.name.endsWith('_shovel');
  }
  return (item) => item.name.endsWith('_pickaxe');
}

async function equipTool(bot, blockName) {
  const pred = preferredTool(blockName);
  const item = bot.inventory.items().find(pred);
  if (!item) return false;
  try {
    await bot.equip(item, 'hand');
    return true;
  } catch (e) {
    emit('error', { msg: 'equip failed', item: item.name, err: String(e) });
    return false;
  }
}

async function waitForKit(bot) {
  for (let i = 0; i < 200; i += 1) {
    const hasPick = bot.inventory.items().some((item) => item.name.endsWith('_pickaxe'));
    const hasAxe = bot.inventory.items().some((item) => item.name.endsWith('_axe'));
    if (hasPick && hasAxe) return true;
    await safeWait(bot, 2);
  }
  return false;
}

function findRandomUsefulBlock(bot, mcData) {
  const ids = shuffled(RESOURCE_SOURCES)
    .map((name) => mcData.blocksByName[name] && mcData.blocksByName[name].id)
    .filter((id) => id != null);
  const positions = bot.findBlocks({
    matching: ids,
    maxDistance: 48,
    count: 48,
  });
  const blocks = positions
    .map((pos) => bot.blockAt(pos))
    .filter((block) => block && bot.canDigBlock(block));
  if (!blocks.length) return null;
  return blocks[Math.floor(Math.random() * blocks.length)];
}

async function collectNearbyDrops(bot) {
  for (let i = 0; i < 10; i += 1) {
    const item = bot.nearestEntity((entity) =>
      entity.name === 'item' && entity.position.distanceTo(bot.entity.position) < 20
    );
    if (!item) return;
    try {
      await bot.pathfinder.goto(new goals.GoalNear(item.position.x, item.position.y, item.position.z, 1));
      await safeWait(bot, 8);
    } catch (e) {
      emit('error', { msg: 'collect failed', err: String(e) });
      return;
    }
  }
}

async function wander(bot) {
  const dx = Math.floor(Math.random() * 33) - 16;
  const dz = Math.floor(Math.random() * 33) - 16;
  try {
    await bot.pathfinder.goto(
      new goals.GoalNear(bot.entity.position.x + dx, bot.entity.position.y, bot.entity.position.z + dz, 2)
    );
  } catch (e) {
    emit('info', { msg: 'wander failed', err: String(e) });
  }
}

async function digDownOneStep(bot) {
  const below = bot.blockAt(bot.entity.position.floored().offset(0, -1, 0));
  if (!below || !bot.canDigBlock(below)) return false;
  await equipTool(bot, below.name);
  try {
    emit('action', { action: 'dig_down', block: below.name, pos: below.position });
    await bot.dig(below);
    await safeWait(bot, 5);
    return true;
  } catch (e) {
    emit('error', { msg: 'dig down failed', block: below.name, err: String(e) });
    return false;
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

const deadline = Date.now() + Math.max(1, timeoutSec - 30) * 1000;
let finished = false;
let mined = 0;
let idleRounds = 0;
let stopRequested = false;

function finish(reason) {
  if (finished) return;
  stopRequested = true;
  finished = true;
  try { bot.pathfinder.stop(); } catch (e) { /* ignore */ }
  emit('done', {
    msg: reason,
    mined,
    inventory: inventorySummary(bot),
  });
}

bot.once('spawn', async () => {
  emit('ready', { goal: goalText });
  const mcData = require('minecraft-data')(bot.version);
  const movements = new Movements(bot, mcData);
  movements.canDig = true;
  bot.pathfinder.setMovements(movements);

  const kitReady = await waitForKit(bot);
  emit('info', { msg: 'spawned', kitReady });
  setTimeout(() => finish('time budget exhausted'), Math.max(1, timeoutSec - 30) * 1000);

  while (!stopRequested && Date.now() < deadline - 1000) {
    const block = findRandomUsefulBlock(bot, mcData);
    if (!block) {
      idleRounds += 1;
      if (idleRounds >= 3 && bot.entity.position.y > -40) {
        await digDownOneStep(bot);
        idleRounds = 0;
      } else {
        await wander(bot);
      }
      continue;
    }

    idleRounds = 0;
    try {
      await equipTool(bot, block.name);
      await bot.pathfinder.goto(new goals.GoalGetToBlock(block.position.x, block.position.y, block.position.z));
      emit('action', { action: 'dig', block: block.name, pos: block.position });
      await bot.dig(block);
      mined += 1;
      await safeWait(bot, 6);
      await collectNearbyDrops(bot);
    } catch (e) {
      emit('error', { msg: 'dig failed', block: block.name, err: String(e) });
      await safeWait(bot, 5);
    }
  }

  finish('time budget exhausted');
});

bot.on('death', () => {
  emit('dead', { msg: 'random breaker died' });
  try { bot.respawn(); } catch (e) { /* ignore */ }
});
bot.on('kicked', (reason) => emit('error', { msg: 'kicked', reason: String(reason) }));
bot.on('error', (err) => emit('error', { msg: 'bot error', err: String(err) }));
process.on('unhandledRejection', (err) => emit('error', { msg: 'unhandled rejection', err: String(err) }));
bot.on('end', () => {
  emit('info', { msg: 'disconnected' });
  process.exit(0);
});

function shutdown() {
  finish('shutdown');
  try { bot.quit(); } catch (e) { /* ignore */ }
}

process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
