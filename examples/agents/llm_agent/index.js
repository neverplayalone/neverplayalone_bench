// Example LLM agent for the resource_gathering mission.
//
// Same contract as the other reference agents, but instead of hard-coding what
// to gather it asks an LLM (through the validator's OpenAI-compatible proxy) to
// turn the natural-language prompt into a structured plan, then executes it with
// the same mineflayer primitives as log_gatherer. Intentionally a baseline —
// a starting point for a real miner, not a competitive agent.

const mineflayer = require('mineflayer');
const { pathfinder, Movements, goals } = require('mineflayer-pathfinder');

const host = process.env.NPABENCH_HOST || '127.0.0.1';
const port = parseInt(process.env.NPABENCH_PORT || '25565', 10);
const username = process.env.NPABENCH_AGENT_USERNAME || 'npabench_agent';
const prompt = process.env.NPABENCH_AGENT_PROMPT || '';
const timeoutSec = parseInt(process.env.NPABENCH_TIMEOUT_SECONDS || '1200', 10);

// LLM proxy (OpenAI-compatible) — injected by the validator when enabled.
const LLM_BASE_URL = process.env.OPENAI_BASE_URL || process.env.OPENROUTER_BASE_URL || '';
const LLM_API_KEY = process.env.OPENAI_API_KEY || process.env.OPENROUTER_API_KEY || '';
// The agent chooses its own model — it must be on the validator's allowlist
// (NPA_PROXY_ALLOWED_MODELS) or the proxy rejects the call with 403.
const LLM_MODEL = process.env.AGENT_LLM_MODEL || 'openai/gpt-4o-mini';

// Block id -> item id when the mined block drops something else, so progress
// is counted correctly (mining stone yields cobblestone, etc.).
const DROP_ALIAS = { stone: 'cobblestone', grass_block: 'dirt' };

function emit(kind, data = {}) {
  process.stdout.write(JSON.stringify({ kind, data, t: Date.now() / 1000 }) + '\n');
}

async function safeWait(bot, ticks) {
  try {
    await bot.waitForTicks(ticks);
    return true;
  } catch (e) {
    return false;
  }
}

// ---- LLM planning ----
async function planWithLLM(text) {
  if (!LLM_BASE_URL || !LLM_API_KEY) {
    emit('info', { msg: 'no LLM proxy configured; using heuristic plan' });
    return heuristicPlan(text);
  }
  const system =
    'You convert a Minecraft resource-gathering instruction into JSON. Respond with ONLY ' +
    '{"targets":[{"name":"logs","count":24,"blocks":["oak_log","birch_log"]}]}. ' +
    '"blocks" are concrete Minecraft block ids to mine for the resource ' +
    '(cobblestone -> ["stone"], dirt -> ["dirt","grass_block"], sand -> ["sand"]). No prose.';
  try {
    const res = await fetch(`${LLM_BASE_URL}/chat/completions`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${LLM_API_KEY}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: LLM_MODEL,
        temperature: 0,
        max_tokens: 400,
        stream: false, // the proxy rejects streaming
        messages: [
          { role: 'system', content: system },
          { role: 'user', content: text },
        ],
      }),
    });
    if (!res.ok) {
      emit('info', { msg: 'llm request failed', status: res.status });
      return heuristicPlan(text);
    }
    const payload = await res.json();
    const content = (payload.choices && payload.choices[0].message.content) || '';
    const parsed = JSON.parse((content.match(/\{[\s\S]*\}/) || [content])[0]);
    const targets = (parsed.targets || []).filter(
      (t) => t && t.count > 0 && Array.isArray(t.blocks) && t.blocks.length
    );
    if (!targets.length) return heuristicPlan(text);
    emit('info', { msg: 'llm plan', model: LLM_MODEL, targets });
    return targets;
  } catch (e) {
    emit('info', { msg: 'llm plan error', err: String(e) });
    return heuristicPlan(text);
  }
}

// Fallback so the agent still runs without an LLM (or when the call errors).
function heuristicPlan(text) {
  const p = text.toLowerCase();
  const rules = [
    { name: 'logs', re: /(\d+)\s+logs?/, blocks: ['oak_log', 'birch_log', 'spruce_log', 'jungle_log', 'acacia_log', 'dark_oak_log', 'mangrove_log', 'cherry_log'] },
    { name: 'cobblestone', re: /(\d+)\s+cobble/, blocks: ['stone', 'cobblestone'] },
    { name: 'dirt', re: /(\d+)\s+dirt/, blocks: ['dirt', 'grass_block'] },
    { name: 'sand', re: /(\d+)\s+sand/, blocks: ['sand', 'red_sand'] },
  ];
  const targets = [];
  for (const r of rules) {
    const m = p.match(r.re);
    if (m) targets.push({ name: r.name, count: parseInt(m[1], 10), blocks: r.blocks });
  }
  return targets;
}

// ---- gathering primitives (adapted from the log_gatherer reference) ----
function haveCount(bot, blocks) {
  const wanted = new Set(blocks);
  for (const b of blocks) if (DROP_ALIAS[b]) wanted.add(DROP_ALIAS[b]);
  return bot.inventory.items().filter((i) => wanted.has(i.name)).reduce((s, i) => s + i.count, 0);
}

function findNearest(bot, mcData, blocks) {
  const ids = blocks.map((n) => mcData.blocksByName[n] && mcData.blocksByName[n].id).filter((id) => id != null);
  if (!ids.length) return null;
  const positions = bot.findBlocks({ matching: ids, maxDistance: 64, count: 64 });
  const found = positions.map((pos) => bot.blockAt(pos)).filter((b) => b && blocks.includes(b.name));
  if (!found.length) return null;
  found.sort((a, b) => a.position.distanceTo(bot.entity.position) - b.position.distanceTo(bot.entity.position));
  return found[0];
}

async function equipBestTool(bot, name) {
  let suffix = null;
  if (name.endsWith('_log') || name.endsWith('_wood')) suffix = '_axe';
  else if (name === 'stone' || name.endsWith('stone') || name.endsWith('_ore') || name.startsWith('deepslate')) suffix = '_pickaxe';
  else if (['dirt', 'grass_block', 'sand', 'red_sand', 'gravel', 'clay'].includes(name)) suffix = '_shovel';
  if (!suffix) return;
  const tool = bot.inventory.items().find((i) => i.name.endsWith(suffix));
  if (tool) {
    try { await bot.equip(tool, 'hand'); } catch (e) { /* keep bare hands */ }
  }
}

async function collectNearbyDrops(bot) {
  for (let i = 0; i < 12; i += 1) {
    const item = bot.nearestEntity((e) => e.name === 'item' && e.position.distanceTo(bot.entity.position) < 18);
    if (!item) return;
    try {
      await bot.pathfinder.goto(new goals.GoalNear(item.position.x, item.position.y, item.position.z, 1));
      await safeWait(bot, 6);
    } catch (e) {
      return;
    }
  }
}

async function wander(bot) {
  const dx = Math.floor(Math.random() * 41) - 20;
  const dz = Math.floor(Math.random() * 41) - 20;
  try {
    await bot.pathfinder.goto(new goals.GoalNear(bot.entity.position.x + dx, bot.entity.position.y, bot.entity.position.z + dz, 2));
  } catch (e) {
    /* ignore */
  }
}

async function returnToSpawn(bot, spawnPos) {
  if (!spawnPos) return;
  try {
    await bot.pathfinder.goto(new goals.GoalNear(spawnPos.x, spawnPos.y, spawnPos.z, 8));
    emit('action', { action: 'return_to_spawn', pos: spawnPos });
    await safeWait(bot, 5);
  } catch (e) {
    emit('error', { msg: 'return to spawn failed', err: String(e) });
  }
}

async function waitForKit(bot) {
  // Mission setup runs after `ready` (starting items, survival mode). Wait for it.
  for (let i = 0; i < 200; i += 1) {
    if (bot.inventory.items().length > 0) return true;
    await safeWait(bot, 2);
  }
  return false;
}

function inventorySummary(bot) {
  const out = {};
  for (const i of bot.inventory.items()) out[i.name] = (out[i.name] || 0) + i.count;
  return out;
}

async function gather(bot, mcData, target, deadline, isStopped) {
  emit('action', { action: 'gather_start', target: target.name, count: target.count });
  let misses = 0;
  while (!isStopped() && Date.now() < deadline && haveCount(bot, target.blocks) < target.count) {
    const block = findNearest(bot, mcData, target.blocks);
    if (!block) {
      await wander(bot);
      if (++misses > 8) break;
      continue;
    }
    misses = 0;
    try {
      await equipBestTool(bot, block.name);
      await bot.pathfinder.goto(new goals.GoalNear(block.position.x, block.position.y, block.position.z, 1));
      emit('action', { action: 'dig', block: block.name });
      await bot.dig(block);
      await safeWait(bot, 4);
      await collectNearbyDrops(bot);
    } catch (e) {
      emit('error', { msg: 'dig failed', err: String(e) });
      await safeWait(bot, 8);
    }
  }
  emit('action', { action: 'gather_done', target: target.name, have: haveCount(bot, target.blocks) });
}

// ---- run ----
const bot = mineflayer.createBot({ host, port, username, version: false, auth: 'offline' });
bot.loadPlugin(pathfinder);

let finished = false;
let stopRequested = false;
let spawnPos = null;

function finish(reason) {
  if (finished) return;
  stopRequested = true;
  finished = true;
  try { bot.pathfinder.stop(); } catch (e) { /* ignore */ }
  emit('done', { msg: reason, inventory: inventorySummary(bot) });
}

bot.once('spawn', async () => {
  emit('ready', { prompt });
  const mcData = require('minecraft-data')(bot.version);
  const movements = new Movements(bot, mcData);
  movements.canDig = true;
  bot.pathfinder.setMovements(movements);

  await waitForKit(bot);
  spawnPos = bot.entity.position.clone();
  const budgetMs = Math.max(1, timeoutSec - 30) * 1000;
  const deadline = Date.now() + budgetMs;
  // Reserve time to walk back to spawn before the hard timeout — the score has a
  // distance-from-spawn multiplier.
  setTimeout(() => {
    if (!finished) returnToSpawn(bot, spawnPos).finally(() => finish('time budget exhausted'));
  }, budgetMs);
  emit('info', { msg: 'spawned', spawnPos });

  try {
    const plan = await planWithLLM(prompt);
    if (!plan.length) emit('info', { msg: 'empty plan' });
    for (const target of plan) {
      if (stopRequested || Date.now() >= deadline) break;
      await gather(bot, mcData, target, deadline, () => stopRequested);
    }
  } catch (e) {
    emit('error', { msg: 'run error', err: String(e) });
  }

  await returnToSpawn(bot, spawnPos);
  finish('plan complete');
});

bot.on('death', () => {
  emit('dead', {});
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
