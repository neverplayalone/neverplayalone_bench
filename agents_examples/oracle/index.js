// Validation oracle for the mcbench harness.
//
// Reads the task's success rule from MCBENCH_RULES and directly performs it, so
// `mcbench bench --valid` can confirm a generated task is actually solvable:
//   blocks_broken    -> equip a tool, find & mine the target block N times
//   blocks_placed    -> equip the block, place it N times
//   entities_killed  -> equip a sword, kill N of the target mob
//   inventory_contains -> craft (or smelt) the target item
//
// It is NOT a smart agent — it's a deterministic solver that knows the win
// condition (which a normally-evaluated agent never sees).

const mineflayer = require('mineflayer');
const { pathfinder, Movements, goals } = require('mineflayer-pathfinder');
const vec3 = require('vec3');

const host = process.env.MCBENCH_HOST || '127.0.0.1';
const port = parseInt(process.env.MCBENCH_PORT || '25565', 10);
const username = process.env.MCBENCH_USERNAME || 'BenchmarkBot';
const timeoutSec = parseInt(process.env.MCBENCH_TIMEOUT || '120', 10);
const rules = JSON.parse(process.env.MCBENCH_RULES || '[]');
const rule = rules[0] || {};
const target = rule.block || rule.entity || rule.item || null;
const count = rule.min_count || 1;
const deadline = Date.now() + timeoutSec * 1000;

function emit(kind, data = {}) {
  process.stdout.write(JSON.stringify({ kind, data, t: Date.now() / 1000 }) + '\n');
}
const itemsOf = (bot, pred) => bot.inventory.items().filter(pred);
const TOOL_RE = /_(pickaxe|axe|shovel|hoe|sword)$/;

async function equip(bot, pred, dest = 'hand') {
  const item = bot.inventory.items().find(pred);
  if (item) { try { await bot.equip(item, dest); return true; } catch (e) { /* ignore */ } }
  return false;
}

// Wait for the runner's setup (give/summon/fill, run on our 'ready' event) to land.
async function waitForSetup(bot, mcData) {
  for (let i = 0; i < 120; i += 1) {
    if (rule.kind === 'blocks_broken') {
      const id = mcData.blocksByName[target] && mcData.blocksByName[target].id;
      if (id != null && bot.findBlock({ matching: id, maxDistance: 48 })) return;
    } else if (rule.kind === 'entities_killed') {
      if (bot.nearestEntity((e) => e.name === target)) return;
    } else if (bot.inventory.items().length > 0) {
      return;
    }
    await bot.waitForTicks(2);
  }
}

async function mineBlocks(bot, mcData) {
  const id = mcData.blocksByName[target] && mcData.blocksByName[target].id;
  if (id == null) { emit('error', { msg: `unknown block ${target}` }); return 0; }
  await equip(bot, (i) => TOOL_RE.test(i.name) || i.name === 'shears');
  let done = 0;
  while (done < count && Date.now() < deadline) {
    const block = bot.findBlock({ matching: id, maxDistance: 48 });
    if (!block) break;
    try {
      await bot.pathfinder.goto(new goals.GoalGetToBlock(block.position.x, block.position.y, block.position.z));
      await bot.dig(block);
      done += 1;
      emit('action', { action: 'dig', block: target });
      await bot.waitForTicks(3);
    } catch (e) { emit('error', { msg: 'dig failed', err: String(e) }); }
  }
  return done;
}

async function placeBlocks(bot) {
  if (!(await equip(bot, (i) => i.name === target))) {
    emit('error', { msg: `no ${target} to place` });
    return 0;
  }
  let done = 0;
  const ring = [];
  for (let r = 2; r <= 5 && ring.length < count + 4; r += 1)
    for (const [dx, dz] of [[-r, 0], [r, 0], [0, -r], [0, r], [-r, -r], [r, r]]) ring.push([dx, dz]);
  for (const [dx, dz] of ring) {
    if (done >= count || Date.now() >= deadline) break;
    const item = bot.inventory.items().find((i) => i.name === target);
    if (!item) break;
    const refPos = bot.entity.position.floored().offset(dx, -1, dz);
    const ref = bot.blockAt(refPos);
    if (!ref || ref.name === 'air') continue;
    try {
      await bot.equip(item, 'hand');
      await bot.lookAt(refPos.offset(0.5, 1, 0.5), true);
      await bot.placeBlock(ref, vec3(0, 1, 0));
      done += 1;
      emit('action', { action: 'place', block: target });
      await bot.waitForTicks(4);
    } catch (e) { emit('error', { msg: 'place failed', err: String(e) }); }
  }
  return done;
}

async function killMobs(bot) {
  await equip(bot, (i) => i.name.endsWith('_sword'));
  let killed = 0;
  const engaged = new Set();
  bot.on('entityGone', (e) => { if (e && e.name === target && engaged.has(e.id)) killed += 1; });
  for (let attempt = 0; attempt < count * 20 && killed < count && Date.now() < deadline; attempt += 1) {
    const mob = bot.nearestEntity((e) => e.name === target && e.position.distanceTo(bot.entity.position) < 32);
    if (!mob) { await bot.waitForTicks(5); continue; }
    engaged.add(mob.id);
    try {
      if (mob.position.distanceTo(bot.entity.position) > 2.5)
        await bot.pathfinder.goto(new goals.GoalNear(mob.position.x, mob.position.y, mob.position.z, 1));
      await bot.lookAt(mob.position.offset(0, 1, 0), true);
      await bot.attack(mob);
      emit('action', { action: 'attack', entity: target });
      await bot.waitForTicks(6);
    } catch (e) { emit('error', { msg: 'attack failed', err: String(e) }); }
  }
  return killed;
}

async function placeStation(bot, name) {
  const item = bot.inventory.items().find((i) => i.name === name);
  if (!item) return null;
  const refPos = bot.entity.position.floored().offset(2, -1, 0);
  const ref = bot.blockAt(refPos);
  if (!ref || ref.name === 'air') return null;
  try {
    await bot.equip(item, 'hand');
    await bot.lookAt(refPos.offset(0.5, 1, 0.5), true);
    await bot.placeBlock(ref, vec3(0, 1, 0));
    await bot.waitForTicks(4);
    return bot.blockAt(refPos.offset(0, 1, 0));
  } catch (e) { emit('error', { msg: `place ${name} failed`, err: String(e) }); return null; }
}

async function craftOrSmelt(bot, mcData) {
  const itemId = mcData.itemsByName[target] && mcData.itemsByName[target].id;
  if (itemId == null) { emit('error', { msg: `unknown item ${target}` }); return false; }

  // 1) Try crafting (2x2 first, then place a crafting table for 3x3 recipes).
  let recipe = bot.recipesFor(itemId, null, 1, null)[0];
  let table = null;
  if (!recipe && bot.inventory.items().some((i) => i.name === 'crafting_table')) {
    table = await placeStation(bot, 'crafting_table');
    if (table) {
      // Some recipes need the bot adjacent to the table; pathfind close and look at it.
      try {
        await bot.pathfinder.goto(new goals.GoalNear(table.position.x, table.position.y, table.position.z, 1));
        await bot.lookAt(table.position.offset(0.5, 0.5, 0.5), true);
      } catch (e) { /* ignore */ }
      // recipesFor pre-filters by bot inventory; for some items (e.g. items with
      // metadata variants) that pre-filter mismatches even when ingredients are
      // present. recipesAll returns recipes regardless — bot.craft itself will then
      // gather and craft if it actually can.
      recipe = bot.recipesFor(itemId, null, 1, table)[0]
        || (bot.recipesAll && bot.recipesAll(itemId, null, table)[0]);
    }
  }
  if (recipe) {
    try { await bot.craft(recipe, count, table); emit('action', { action: 'craft', item: target }); return true; }
    catch (e) { emit('error', { msg: 'craft failed', err: String(e) }); }
  }

  // 2) Try smelting: input = the inventory item that isn't furnace/fuel.
  const furnaceBlock = await placeStation(bot, 'furnace');
  if (!furnaceBlock) { emit('error', { msg: `cannot craft or smelt ${target}` }); return false; }
  const skip = new Set(['furnace', 'coal', 'charcoal', 'crafting_table']);
  const raw = itemsOf(bot, (i) => !skip.has(i.name))[0];
  const fuel = bot.inventory.items().find((i) => i.name === 'coal' || i.name === 'charcoal');
  if (!raw || !fuel) { emit('error', { msg: 'missing smelt input/fuel' }); return false; }
  try {
    const furnace = await bot.openFurnace(furnaceBlock);
    await furnace.putFuel(fuel.type, null, Math.min(fuel.count, count + 1));
    await furnace.putInput(raw.type, null, Math.min(raw.count, count));
    for (let i = 0; i < 40 && Date.now() < deadline; i += 1) {
      if (furnace.outputItem()) { await furnace.takeOutput(); }
      await bot.waitForTicks(20);
      if (bot.inventory.items().some((x) => x.name === target)) break;
    }
    furnace.close();
    emit('action', { action: 'smelt', item: target });
    return true;
  } catch (e) { emit('error', { msg: 'smelt failed', err: String(e) }); return false; }
}

const bot = mineflayer.createBot({ host, port, username, version: false, auth: 'offline' });
bot.loadPlugin(pathfinder);
let finished = false;

bot.once('spawn', async () => {
  emit('ready', { rule });
  const mcData = require('minecraft-data')(bot.version);
  bot.pathfinder.setMovements(new Movements(bot, mcData));
  await waitForSetup(bot, mcData);

  let result;
  try {
    if (rule.kind === 'blocks_broken') result = await mineBlocks(bot, mcData);
    else if (rule.kind === 'blocks_placed') result = await placeBlocks(bot);
    else if (rule.kind === 'entities_killed') result = await killMobs(bot);
    else if (rule.kind === 'inventory_contains') result = await craftOrSmelt(bot, mcData);
    else emit('error', { msg: `oracle has no solver for rule kind ${rule.kind}` });
  } catch (e) { emit('error', { msg: 'solver crashed', err: String(e) }); }

  finished = true;
  const inv = bot.inventory.items().map((i) => `${i.name}x${i.count}`);
  emit('done', { msg: 'oracle finished', kind: rule.kind, target, count, result, inv });
});

bot.on('death', () => { emit('dead', { msg: 'oracle died' }); try { bot.respawn(); } catch (e) { /* ignore */ } });
bot.on('kicked', (reason) => emit('error', { msg: 'kicked', reason: String(reason) }));
bot.on('error', (err) => emit('error', { msg: 'bot error', err: String(err) }));
bot.on('end', () => { emit('info', { msg: 'disconnected' }); process.exit(0); });
function shutdown() { if (!finished) emit('done', { msg: 'shutdown' }); try { bot.quit(); } catch (e) { /* ignore */ } }
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
