// Adversarial agent: connects as a real player but performs NO real actions.
// It spams fabricated achievement events to try to inflate its score. Under
// server-authoritative grading it must score 0 — proving the grader ignores
// agent self-report. Run against tasks/test/stat_acceptance.yaml.

const mineflayer = require('mineflayer');

const host = process.env.MCBENCH_HOST || '127.0.0.1';
const port = parseInt(process.env.MCBENCH_PORT || '25565', 10);
const username = process.env.MCBENCH_USERNAME || 'BenchmarkBot';

function emit(kind, data = {}) {
  process.stdout.write(JSON.stringify({ kind, data, t: Date.now() / 1000 }) + '\n');
}

const bot = mineflayer.createBot({ host, port, username, version: false, auth: 'offline' });
let finished = false;

bot.once('spawn', async () => {
  emit('ready', {});
  await bot.waitForTicks(20); // let the runner finish setup
  // Lie: claim a pile of achievements without doing anything.
  for (let i = 0; i < 50; i += 1) emit('kill', { entity: 'zombie' });
  for (let i = 0; i < 50; i += 1) emit('action', { action: 'dig', block: 'cobblestone' });
  for (let i = 0; i < 50; i += 1) emit('action', { action: 'place', block: 'oak_planks' });
  await bot.waitForTicks(10);
  finished = true;
  emit('done', { msg: 'lied about everything', self_reported: { placed: 50, mined: 50, killed: 50 } });
});

bot.on('death', () => { try { bot.respawn(); } catch (e) { /* ignore */ } });
bot.on('end', () => { emit('info', { msg: 'disconnected' }); process.exit(0); });
function shutdown() { if (!finished) emit('done', { msg: 'shutdown' }); try { bot.quit(); } catch (e) { /* ignore */ } }
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
