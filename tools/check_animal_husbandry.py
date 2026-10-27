"""Controlled crop-fed v4 enclosure and supply fixtures, separate from actual agent evaluation.

Administrative terrain, teleports and replacement blocks are test-only.
The reference world is copied and never changed.
"""

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path

from npabench.evaluation.reference_world import start_agent_run_slot, stop_agent_run_slot
from npabench.evaluation.run_slot import AgentRunSlot
from npabench.minecraft.rcon_client import rcon_session
from npabench.minecraft.server_probe import wait_for_ready
from npabench.missions.animal_husbandry.plugin import install_plugin, snapshot
from npabench.missions.registry import get_mission

BOT = r"""
const m=require(MODULE),V=require(VEC).Vec3;
const b=m.createBot({host:'127.0.0.1',port:26665,username:'npabench_agent',version:'1.21.11'});
const wait=ms=>new Promise(r=>setTimeout(r,ms));
b.on('login',()=>{const Item=require(ITEM)(b.registry);b._client.removeAllListeners('set_player_inventory');b._client.on('set_player_inventory',p=>{const n=p.slotId,s=n<9?36+n:n<=35?n:n<=39?44-n:n===40?45:-1;if(s>=0)b._setSlot(s,Item.fromNotch(p.contents));});});
b.on('spawn',()=>setTimeout(()=>b._client.write('player_loaded',{}),300));
b.on('messagestr',s=>{if(s.includes('NPA_FARM '))process.stdout.write(JSON.stringify({farm:JSON.parse(s.slice(s.indexOf('NPA_FARM ')+9))})+'\n');});
b.on('error',e=>console.error(e));
require('readline').createInterface({input:process.stdin}).on('line',async line=>{try{const q=JSON.parse(line);if(q.chat){b.chat(q.chat);return;}await wait(200);await b.equip(b.inventory.items().find(i=>i.name==='oak_fence'),'hand');await b.placeBlock(b.blockAt(new V(q.x,99,q.z)),new V(0,1,0));await wait(80);process.stdout.write('{"placed":true}\n');}catch(e){process.stdout.write(JSON.stringify({error:e.message})+'\n');}});
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-world", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    reference = out / "reference"
    shutil.copytree(args.reference_world.resolve(), reference)
    mission = get_mission("animal_husbandry")
    base = mission.load_config(mission.default_config_path())
    config = mission.build_mission_config(base, mission.generate_task(base, 10))
    install_plugin(reference, config)
    slot = AgentRunSlot.allocate(
        data_root=out / "run",
        base_game_port=26665,
        base_rcon_port=26675,
        container_prefix="npa-enclosure-check",
    )
    bot = None
    checks = []
    try:
        start_agent_run_slot(slot, config, reference)
        wait_for_ready(slot.server_endpoint(), timeout=180)
        modules = root / "tools/recorder/node_modules"
        script = BOT
        for token, name in [("MODULE", "mineflayer"), ("VEC", "vec3"), ("ITEM", "prismarine-item")]:
            script = script.replace(token, json.dumps(str(modules / name)))
        (out / "fixture.cjs").write_text(script)
        with (out / "bot.log").open("w") as log:
            bot = subprocess.Popen(
                ["node", str(out / "fixture.cjs")],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log,
                text=True,
                bufsize=1,
            )
            with rcon_session(port=slot.rcon_port, password=slot.rcon_password) as rcon:
                for _ in range(180):
                    if (
                        "time is"
                        in rcon.command(
                            "execute if entity @a[name=npabench_agent] run time query gametime"
                        ).lower()
                    ):
                        break
                    time.sleep(0.25)
                else:
                    raise RuntimeError("fixture bot failed to join")
                mission.configure_world(rcon, config)
                state = mission.setup_agent(rcon, config)
                from collections import Counter

                from npabench.minecraft.rcon_helpers import parse_scalar

                sx, sy, sz = state["spawn"]
                expected = [
                    ("beetroot_seeds", config.starter_crops["beetroot_seeds"]),
                    ("carrot", config.starter_crops["carrot"]),
                    ("oak_fence", 64),
                    ("oak_fence", 64),
                    ("oak_fence_gate", 8),
                    ("potato", config.starter_crops["potato"]),
                    ("wheat_seeds", config.starter_crops["wheat_seeds"]),
                ]
                stock = Counter()
                for index, (name, count) in enumerate(expected):
                    prefix = f"data get block {sx + 2} {sy} {sz} Items[{{Slot:{index}b}}]"
                    assert rcon.command(prefix + ".id").strip().endswith('"minecraft:' + name + '"')
                    assert parse_scalar(rcon.command(prefix + ".count")) == count
                    stock[name] += count
                assert (
                    "time is"
                    not in rcon.command(
                        f"execute if data block {sx + 2} {sy} {sz} Items[{{Slot:7b}}] run time query gametime"
                    ).lower()
                )
                assert parse_scalar(rcon.command("gamerule random_tick_speed")) == 3
                (out / "starter_supplies.json").write_text(json.dumps(dict(stock), indent=2))
                print("PASS limited planting supplies and native crop tick rate", flush=True)

                for cmd in (
                    "fill 0 99 0 21 99 21 grass_block",
                    "fill 0 100 0 21 103 21 air",
                    "tp npabench_agent 10.5 100 10.5",
                    "give npabench_agent oak_fence 128",
                ):
                    rcon.command(cmd)
                time.sleep(1)

                def request(q):
                    bot.stdin.write(json.dumps(q) + "\n")
                    bot.stdin.flush()
                    line = bot.stdout.readline()
                    if not line:
                        raise RuntimeError("fixture bot stopped")
                    return json.loads(line)

                def claim(expected, text="register 0 100 0 20 20", code=None):
                    time.sleep(1.1)
                    reply = request({"chat": "/npafarm " + text})["farm"]
                    assert reply["valid"] is expected, reply
                    if code:
                        assert code in {i["code"] for i in reply["issues"]}, reply
                    checks.append({"command": text, "expected_valid": expected, "reply": reply})
                    print("PASS", text, expected, code or "", flush=True)
                    return reply

                claim(False, "register 0 100 0 19 20", "invalid_dimensions_or_height")
                claim(False, "register 0 2147483646 0 20 20", "invalid_dimensions_or_height")
                rcon.command("setblock 0 100 0 oak_fence")
                claim(False, code="fence_not_agent_built")
                rcon.command("setblock 0 100 0 air")
                ring = [(x, 0) for x in range(22)] + [(21, z) for z in range(1, 22)]
                ring += [(x, 21) for x in range(20, -1, -1)] + [(0, z) for z in range(20, 0, -1)]
                for x, z in ring:
                    sx, sz = max(2, min(19, x)), max(2, min(19, z))
                    rcon.command(f"tp npabench_agent {sx + 0.5} 100 {sz + 0.5}")
                    reply = request({"x": x, "z": z})
                    assert reply.get("placed"), (x, z, reply)
                reply = claim(True)
                assert len(reply["starters"]) == 4
                ids = {a["uuid"] for a in reply["starters"]}
                assert {a["uuid"] for a in claim(True)["starters"]} == ids
                claim(False, "register 1 100 1 20 20")
                mutations = [
                    ("setblock 10 99 10 air", False, "floor_not_full_solid"),
                    ("setblock 10 99 10 stone_slab", False, "floor_not_full_solid"),
                    ("setblock 10 99 10 grass_block", True, None),
                    ("setblock 10 100 10 stone", False, "interior_obstructed"),
                    ("setblock 10 100 10 air", True, None),
                    (
                        "setblock 10 100 0 oak_fence_gate[facing=north,open=true]",
                        False,
                        "gate_open",
                    ),
                    (
                        "setblock 10 100 0 oak_fence_gate[facing=east,open=false]",
                        False,
                        "gate_wrong_orientation",
                    ),
                    ("setblock 10 100 0 oak_fence_gate[facing=north,open=false]", True, None),
                    ("setblock 10 100 0 air", False, "missing_fence"),
                ]
                for cmd, valid, code in mutations:
                    rcon.command(cmd)
                    claim(valid, "status", code)
                saved = snapshot(rcon, state["run_id"])
                assert saved["enclosure"]["valid"] is False
                assert len(saved["starters"]) == 4 and saved["animals"] == []
                (out / "snapshot.json").write_text(json.dumps(saved, indent=2))
                (out / "checks.json").write_text(json.dumps(checks, indent=2))
                print("PASS final snapshot revalidates enclosure", flush=True)
    finally:
        if bot:
            bot.terminate()
            bot.wait(timeout=10)
        stop_agent_run_slot(slot)


if __name__ == "__main__":
    main()
