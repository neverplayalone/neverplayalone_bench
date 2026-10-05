"use strict";

const mineflayer = require("mineflayer");
const { Vec3 } = require("vec3");

const bot = mineflayer.createBot({
  host: process.env.NPABENCH_HOST,
  port: Number(process.env.NPABENCH_PORT),
  username: process.env.NPABENCH_AGENT_USERNAME,
  version: "1.21.11",
});

function emit(kind, data = {}) {
  process.stdout.write(`${JSON.stringify({ kind, data })}\n`);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function take(container, name, count) {
  const item = container.containerItems().find((entry) => entry.name === name);
  if (!item || item.count < count) throw new Error(`barrel lacks ${count} ${name}`);
  await container.withdraw(item.type, null, count);
}

async function carried(name) {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const item = bot.inventory.items().find((entry) => entry.name === name);
    if (item) return item;
    await sleep(100);
  }
  throw new Error(`inventory lacks ${name}: ${bot.inventory.items().map((item) => item.name)}`);
}

async function run() {
  let barrel;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    barrel = bot.findBlock({ matching: (block) => block.name === "barrel", maxDistance: 6 });
    if (barrel) break;
    await sleep(250);
  }
  if (!barrel) throw new Error("supply barrel not found");
  const container = await bot.openContainer(barrel);
  await take(container, "red_mushroom", 3);
  await take(container, "podzol", 1);
  container.close();

  const feet = bot.entity.position.floored();
  const candidates = [new Vec3(-1, 0, 0), new Vec3(0, 0, -1), new Vec3(-1, 0, -1)];
  const offset = candidates.find((candidate) => {
    const ground = bot.blockAt(feet.plus(candidate).offset(0, -1, 0));
    const space = bot.blockAt(feet.plus(candidate));
    return ground && ground.name !== "air" && space && space.name === "air";
  });
  if (!offset) throw new Error("no nearby ground to replace with podzol");
  const groundPosition = feet.plus(offset).offset(0, -1, 0);
  await bot.dig(bot.blockAt(groundPosition));
  const below = bot.blockAt(groundPosition.offset(0, -1, 0));
  if (!below || below.name === "air") throw new Error("no support under podzol");
  await bot.equip(await carried("podzol"), "hand");
  await bot.placeBlock(below, new Vec3(0, 1, 0));

  for (let cycle = 1; cycle <= 3; cycle += 1) {
    const podzol = bot.blockAt(groundPosition);
    await bot.equip(await carried("red_mushroom"), "hand");
    await bot.placeBlock(podzol, new Vec3(0, 1, 0));
    await sleep(200);
    const mushroom = bot.blockAt(groundPosition.offset(0, 1, 0));
    if (!mushroom || mushroom.name !== "red_mushroom") {
      throw new Error(`mushroom placement ${cycle} failed`);
    }
    await bot.dig(mushroom);
    emit("info", { msg: "placed_and_broke_mushroom", cycle });
    await sleep(250);
  }
  emit("done", { msg: "cycled three mushrooms without growth" });
}

bot.once("spawn", () => {
  emit("ready");
  run().catch((error) => {
    emit("info", { msg: "cycle_test_failed", error: String(error) });
    emit("done", { msg: "cycle test failed" });
  });
});
bot.on("error", (error) => emit("info", { msg: "bot_error", error: String(error) }));
