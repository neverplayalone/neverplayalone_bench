// Deterministic acceptance agent for the mcbench harness.
//
// Performs a KNOWN quantity of server-observable actions — place 3 oak_planks,
// mine 3 cobblestone, kill 2 zombies — so we can assert that the harness's
// server-authoritative scoreboard stats (minecraft.used / mined / killed) match
// reality. Pair with tasks/test/stat_acceptance.yaml.

const mineflayer = require('mineflayer');
const { pathfinder, Movements, goals } = require('mineflayer-pathfinder');
const vec3 = require('vec3');

const host = process.env.MCBENCH_HOST || '127.0.0.1';
const port = parseInt(process.env.MCBENCH_PORT || '25565', 10);
const username = process.env.MCBENCH_USERNAME || 'BenchmarkBot';
const timeoutSec = parseInt(process.env.MCBENCH_TIMEOUT || '120', 10);

const PLACE_N = 3;
const MINE_N = 3;
const KILL_N = 2;

function emit(kind, data = {}) {
  process.stdout.write(JSON.stringify({ kind, data, t: Date.now() / 1000 }) + '\n');
}

function countItem(bot, name) {
  return bot.inventory.items().filter((i) => i.name === name).reduce((t, i) => t + i.count, 0);
}

async function waitForSetup(bot) {
  for (let i = 0; i < 200; i += 1) {
    if (countItem(bot, 'iron_pickaxe') > 0 && countItem(bot, 'oak_planks') > 0) return true;
    await bot.waitForTicks(1);
  }
  return false;
}

async function placePlanks(bot, n) {
  // Place on top of the ground in free cells to the west/north (away from the
  // cobblestone patch at +x/+z and the zombies at the bot's south).
  const offsets = [[-2, 0], [-3, 0], [0, -2], [0, -3], [-2, -2], [-3, -2]];
  let placed = 0;
  for (const [dx, dz] of offsets) {
    if (placed >= n) break;
    const planks = bot.inventory.items().find((i) => i.name === 'oak_planks');
    if (!planks) break;
    const base = bot.entity.position.floored();
    const refPos = base.offset(dx, -1, dz); // ground block we place ON TOP of
    const ref = bot.blockAt(refPos);
    if (!ref || ref.name === 'air') continue;
    try {
      await bot.equip(planks, 'hand');
      await bot.lookAt(refPos.offset(0.5, 1, 0.5), true);
      await bot.placeBlock(ref, vec3(0, 1, 0));
      placed += 1;
      emit('action', { action: 'place', block: 'oak_planks' });
      await bot.waitForTicks(4);
    } catch (e) {
      emit('error', { msg: 'place failed', err: String(e) });
    }
  }
  return placed;
}

async function mineCobble(bot, n, mcData) {
  const id = mcData.blocksByName.cobblestone.id;
  const pick = bot.inventory.items().find((i) => i.name === 'iron_pickaxe');
  if (pick) { try { await bot.equip(pick, 'hand'); } catch (e) { /* ignore */ } }
  let mined = 0;
  for (let attempt = 0; attempt < n * 4 && mined < n; attempt += 1) {
    const block = bot.findBlock({ matching: id, maxDistance: 32 });
    if (!block) break;
    try {
      await bot.pathfinder.goto(new goals.GoalGetToBlock(block.position.x, block.position.y, block.position.z));
      emit('action', { action: 'dig', block: 'cobblestone' });
      await bot.dig(block);
      mined += 1;
      await bot.waitForTicks(4);
    } catch (e) {
      emit('error', { msg: 'dig failed', err: String(e) });
    }
  }
  return mined;
}

async function killZombies(bot, n) {
  const sword = bot.inventory.items().find((i) => i.name === 'diamond_sword');
  if (sword) { try { await bot.equip(sword, 'hand'); } catch (e) { /* ignore */ } }
  let killed = 0;
  const seen = new Set();
  bot.on('entityGone', (e) => {
    if (e && e.name === 'zombie' && seen.has(e.id)) killed += 1;
  });
  for (let attempt = 0; attempt < n * 12 && killed < n; attempt += 1) {
    const zombie = bot.nearestEntity((e) => e.name === 'zombie' && e.position.distanceTo(bot.entity.position) < 24);
    if (!zombie) { await bot.waitForTicks(5); continue; }
    seen.add(zombie.id);
    try {
      if (zombie.position.distanceTo(bot.entity.position) > 2.5) {
        await bot.pathfinder.goto(new goals.GoalNear(zombie.position.x, zombie.position.y, zombie.position.z, 1));
      }
      await bot.lookAt(zombie.position.offset(0, 1, 0), true);
      await bot.attack(zombie);
      emit('action', { action: 'attack', entity: 'zombie' });
      await bot.waitForTicks(6);
    } catch (e) {
      emit('error', { msg: 'attack failed', err: String(e) });
    }
  }
  return killed;
}

const bot = mineflayer.createBot({ host, port, username, version: false, auth: 'offline' });
bot.loadPlugin(pathfinder);

let finished = false;

bot.once('spawn', async () => {
  emit('ready', { phases: ['place', 'mine', 'kill'] });
  const mcData = require('minecraft-data')(bot.version);
  bot.pathfinder.setMovements(new Movements(bot, mcData));

  const ok = await waitForSetup(bot);
  if (!ok) emit('error', { msg: 'setup items never arrived' });

  // Kill first, at full health, before mining/placing expose us to the zombies.
  const killed = await killZombies(bot, KILL_N);
  const mined = await mineCobble(bot, MINE_N, mcData);
  const placed = await placePlanks(bot, PLACE_N);

  finished = true;
  // Self-reported counts — for cross-checking against the server's authoritative
  // tally. The grader does NOT trust these; they're informational only.
  emit('done', { msg: 'probe complete', self_reported: { placed, mined, killed } });
});

// Respawn rather than quit: disconnecting while dead persists Health:0 in this
// offline player's save, which poisons every later run (loads dead, never spawns).
bot.on('death', () => { emit('dead', { msg: 'bot died, respawning' }); try { bot.respawn(); } catch (e) { /* ignore */ } });
bot.on('kicked', (reason) => emit('error', { msg: 'kicked', reason: String(reason) }));
bot.on('error', (err) => emit('error', { msg: 'bot error', err: String(err) }));
bot.on('end', () => { emit('info', { msg: 'disconnected' }); process.exit(0); });

function shutdown() { if (!finished) emit('done', { msg: 'shutdown' }); try { bot.quit(); } catch (e) { /* ignore */ } }
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
