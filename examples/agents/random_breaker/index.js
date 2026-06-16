// Random breaker baseline for mcbench resource gathering.
//
// No LLM, no API key. The agent walks with direct Mineflayer controls and
// breaks nearby diggable blocks. It is intentionally crude but active: useful
// for validating movement, recording, item counting, and return-to-spawn rules.

const mineflayer = require('mineflayer');
const Vec3 = require('vec3');
const { pathfinder, Movements, goals } = require('mineflayer-pathfinder');

const host = process.env.MCBENCH_HOST || '127.0.0.1';
const port = parseInt(process.env.MCBENCH_PORT || '25565', 10);
const username = process.env.MCBENCH_USERNAME || 'BenchmarkBot';
const goalText = process.env.MCBENCH_GOAL || '';
const timeoutSec = parseInt(process.env.MCBENCH_TIMEOUT || '1200', 10);

const LOG_ITEMS = [
  'oak_log',
  'birch_log',
  'spruce_log',
  'jungle_log',
  'acacia_log',
  'dark_oak_log',
  'mangrove_log',
  'cherry_log',
];

const RESOURCE_ITEMS = {
  logs: LOG_ITEMS,
  cobblestone: ['cobblestone'],
  coal: ['coal'],
  sand: ['sand', 'red_sand'],
};

const UNSAFE_OR_USELESS_BLOCKS = new Set([
  'air',
  'cave_air',
  'void_air',
  'water',
  'lava',
  'bedrock',
  'barrier',
  'command_block',
  'chain_command_block',
  'repeating_command_block',
  'structure_block',
  'structure_void',
  'fire',
  'soul_fire',
]);

const TOOL_ORDER = {
  wooden: 1,
  stone: 2,
  iron: 3,
  golden: 4,
  diamond: 5,
  netherite: 6,
};

function emit(kind, data = {}) {
  process.stdout.write(JSON.stringify({ kind, data, t: Date.now() / 1000 }) + '\n');
}

function plainPos(pos) {
  return {
    x: Math.round(pos.x * 100) / 100,
    y: Math.round(pos.y * 100) / 100,
    z: Math.round(pos.z * 100) / 100,
  };
}

function parseTask(goal) {
  const lower = goal.toLowerCase();
  const countMatch = lower.match(/\b(?:gather|collect|get|mine)\s+(\d+)\b/)
    || lower.match(/\b(\d+)\s+(?:logs?|cobblestone|cobble|coal|sand)\b/);
  let resource = 'logs';
  if (lower.includes('cobblestone') || lower.includes('cobble')) resource = 'cobblestone';
  else if (lower.includes('coal')) resource = 'coal';
  else if (lower.includes('sand')) resource = 'sand';
  else if (lower.includes('log')) resource = 'logs';
  return {
    resource,
    targetCount: countMatch ? parseInt(countMatch[1], 10) : 64,
    itemNames: RESOURCE_ITEMS[resource] || LOG_ITEMS,
  };
}

function inventorySummary(bot) {
  const out = {};
  for (const item of bot.inventory.items()) {
    out[item.name] = (out[item.name] || 0) + item.count;
  }
  return out;
}

function countItems(bot, names) {
  const wanted = new Set(names);
  return bot.inventory.items()
    .filter((item) => wanted.has(item.name))
    .reduce((sum, item) => sum + item.count, 0);
}

function distanceXZ(a, b) {
  const dx = a.x - b.x;
  const dz = a.z - b.z;
  return Math.sqrt(dx * dx + dz * dz);
}

async function waitTicks(bot, ticks) {
  try {
    await bot.waitForTicks(ticks);
  } catch (_) {
    // Ignore disconnect-time waits.
  }
}

function toolSuffixForBlock(blockName) {
  if (blockName.includes('log') || blockName.includes('stem')) return '_axe';
  if (
    blockName.includes('stone')
    || blockName.includes('ore')
    || blockName.includes('deepslate')
    || blockName.includes('cobble')
    || blockName.includes('granite')
    || blockName.includes('diorite')
    || blockName.includes('andesite')
  ) return '_pickaxe';
  if (
    blockName.includes('sand')
    || blockName.includes('dirt')
    || blockName.includes('gravel')
    || blockName.includes('clay')
    || blockName.includes('grass_block')
    || blockName === 'snow'
  ) return '_shovel';
  return null;
}

function toolScore(item, suffix) {
  if (!item || !item.name.endsWith(suffix)) return 0;
  const material = item.name.split('_')[0];
  return TOOL_ORDER[material] || 1;
}

async function equipBestTool(bot, blockName) {
  const suffix = toolSuffixForBlock(blockName);
  if (!suffix) return false;
  const tools = bot.inventory.items()
    .filter((item) => item.name.endsWith(suffix))
    .sort((a, b) => toolScore(b, suffix) - toolScore(a, suffix));
  if (!tools.length) return false;
  try {
    await bot.equip(tools[0], 'hand');
    return true;
  } catch (e) {
    emit('info', { msg: 'equip failed', item: tools[0].name, err: String(e) });
    return false;
  }
}

async function waitForSetup(bot) {
  // The validator gives kit immediately after receiving "ready"; keep this
  // simple and wait a few seconds instead of blocking forever on inventory sync.
  for (let i = 0; i < 60; i += 1) {
    const hasTool = bot.inventory.items().some((item) =>
      item.name.endsWith('_axe') || item.name.endsWith('_pickaxe') || item.name.endsWith('_shovel')
    );
    if (hasTool) return true;
    await waitTicks(bot, 2);
  }
  return false;
}

function findBreakableBlock(bot, tried) {
  const origin = bot.entity.position.floored();
  const candidates = [];
  for (let dx = -5; dx <= 5; dx += 1) {
    for (let dy = -2; dy <= 3; dy += 1) {
      for (let dz = -5; dz <= 5; dz += 1) {
        if (dx === 0 && dz === 0 && dy <= 0) continue;
        const pos = origin.offset(dx, dy, dz);
        const key = `${pos.x},${pos.y},${pos.z}`;
        if (tried.has(key)) continue;
        const block = bot.blockAt(pos);
        if (!block || UNSAFE_OR_USELESS_BLOCKS.has(block.name)) continue;
        if (!block.diggable || !bot.canDigBlock(block)) continue;
        candidates.push(block);
      }
    }
  }
  candidates.sort((a, b) => {
    const ad = a.position.distanceTo(bot.entity.position);
    const bd = b.position.distanceTo(bot.entity.position);
    return ad - bd || Math.random() - 0.5;
  });
  return candidates[0] || null;
}

async function collectNearbyDrops(bot) {
  for (let i = 0; i < 6; i += 1) {
    const entity = bot.nearestEntity((e) =>
      e.name === 'item' && e.position.distanceTo(bot.entity.position) <= 8
    );
    if (!entity) return;
    await lookAt(bot, entity.position);
    bot.setControlState('forward', true);
    bot.setControlState('sprint', true);
    await waitTicks(bot, 8);
    bot.clearControlStates();
  }
}

async function lookAt(bot, pos) {
  try {
    await bot.lookAt(pos.offset ? pos.offset(0, 0.5, 0) : pos, true);
  } catch (_) {
    // Ignore transient look failures.
  }
}

async function digOneNearbyBlock(bot, tried) {
  const block = findBreakableBlock(bot, tried);
  if (!block) return false;
  const key = `${block.position.x},${block.position.y},${block.position.z}`;
  try {
    await equipBestTool(bot, block.name);
    await lookAt(bot, block.position);
    emit('action', { action: 'dig', block: block.name, pos: plainPos(block.position) });
    await bot.dig(block);
    mined += 1;
    await waitTicks(bot, 4);
    await collectNearbyDrops(bot);
    return true;
  } catch (e) {
    tried.add(key);
    emit('info', { msg: 'dig failed', block: block.name, pos: plainPos(block.position), err: String(e) });
    return false;
  }
}

async function randomWalk(bot, spawnPos) {
  const pos = bot.entity.position;
  const farFromSpawn = distanceXZ(pos, spawnPos) > 15;
  const angle = farFromSpawn
    ? Math.atan2(spawnPos.x - pos.x, spawnPos.z - pos.z)
    : Math.random() * Math.PI * 2;
  const target = new Vec3(
    pos.x + Math.sin(angle) * 10,
    pos.y + 1.5,
    pos.z + Math.cos(angle) * 10
  );
  await lookAt(bot, target);
  bot.setControlState('forward', true);
  bot.setControlState('sprint', true);
  for (let i = 0; i < 10 + Math.floor(Math.random() * 20); i += 1) {
    if (stopRequested) break;
    bot.setControlState('jump', i % 4 === 0);
    await waitTicks(bot, 2);
  }
  bot.clearControlStates();
  emit('action', {
    action: farFromSpawn ? 'walk_toward_spawn' : 'walk_randomly',
    pos: plainPos(bot.entity.position),
    distanceFromSpawn: Math.round(distanceXZ(bot.entity.position, spawnPos) * 100) / 100,
  });
}

async function returnNearSpawn(bot, spawnPos) {
  if (distanceXZ(bot.entity.position, spawnPos) <= 10) return true;
  try {
    await bot.pathfinder.goto(new goals.GoalNear(spawnPos.x, spawnPos.y, spawnPos.z, 8));
  } catch (_) {
    for (let i = 0; i < 8 && distanceXZ(bot.entity.position, spawnPos) > 12; i += 1) {
      await randomWalk(bot, spawnPos);
    }
  }
  emit('action', {
    action: 'return_to_spawn',
    pos: plainPos(bot.entity.position),
    distanceFromSpawn: Math.round(distanceXZ(bot.entity.position, spawnPos) * 100) / 100,
  });
  return distanceXZ(bot.entity.position, spawnPos) <= 20;
}

const task = parseTask(goalText);
const deadline = Date.now() + Math.max(1, timeoutSec - 20) * 1000;
let finished = false;
let mined = 0;
let stopRequested = false;

const bot = mineflayer.createBot({
  host,
  port,
  username,
  version: false,
  auth: 'offline',
});

bot.loadPlugin(pathfinder);

function finish(reason) {
  if (finished) return;
  stopRequested = true;
  finished = true;
  bot.clearControlStates();
  try { bot.pathfinder.stop(); } catch (_) { /* ignore */ }
  emit('done', {
    msg: reason,
    resource: task.resource,
    targetCount: task.targetCount,
    gathered: countItems(bot, task.itemNames),
    mined,
    inventory: inventorySummary(bot),
  });
}

bot.once('spawn', async () => {
  emit('ready', { goal: goalText, parsedTask: task });
  const mcData = require('minecraft-data')(bot.version);
  const movements = new Movements(bot, mcData);
  movements.canDig = true;
  movements.allow1by1towers = false;
  bot.pathfinder.setMovements(movements);

  const kitReady = await waitForSetup(bot);
  const spawnPos = bot.entity.position.clone();
  emit('info', { msg: 'spawned', kitReady, spawnPos: plainPos(spawnPos) });

  const tried = new Set();
  let digsSinceWalk = 0;
  setTimeout(() => finish('time budget exhausted'), Math.max(1, timeoutSec - 20) * 1000);

  while (!stopRequested && Date.now() < deadline - 1000) {
    if (countItems(bot, task.itemNames) >= task.targetCount) {
      await returnNearSpawn(bot, spawnPos);
      finish('target gathered');
      return;
    }
    if (deadline - Date.now() < 18000) {
      await returnNearSpawn(bot, spawnPos);
      finish('returning before timeout');
      return;
    }
    const dug = await digOneNearbyBlock(bot, tried);
    if (dug) {
      digsSinceWalk += 1;
      if (digsSinceWalk >= 4) {
        digsSinceWalk = 0;
        await randomWalk(bot, spawnPos);
      }
    } else {
      if (tried.size > 128) tried.clear();
      await randomWalk(bot, spawnPos);
    }
  }

  await returnNearSpawn(bot, spawnPos);
  finish('time budget exhausted');
});

bot.on('death', () => {
  emit('dead', { msg: 'random breaker died' });
  try { bot.respawn(); } catch (_) { /* ignore */ }
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
  bot.clearControlStates();
  try { bot.pathfinder.stop(); } catch (_) { /* ignore */ }
  try { bot.quit(); } catch (_) { /* ignore */ }
}

process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
