package org.npabench.animals;

import com.google.gson.Gson;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.*;
import org.bukkit.*;
import org.bukkit.command.*;
import org.bukkit.entity.*;
import org.bukkit.event.*;
import org.bukkit.event.entity.EntityBreedEvent;
import org.bukkit.event.entity.EntityDeathEvent;
import org.bukkit.event.block.BlockPlaceEvent;
import org.bukkit.event.block.BlockBreakEvent;
import org.bukkit.plugin.java.JavaPlugin;

/** Run-scoped breeding evidence and atomic final survival snapshots. */
public final class AnimalHusbandryPlugin extends JavaPlugin implements Listener {
    private final Gson gson = new Gson();
    private final Map<UUID, Record> records = new LinkedHashMap<>();
    private final List<Map<String, Object>> starters = new ArrayList<>();
    private final Set<UUID> starterIds = new HashSet<>();
    private final Set<String> targets = new TreeSet<>();
    private final Set<Enclosure.Cell> built = new HashSet<>();
    private Enclosure.Plot plot;
    private long lastClaimTick = -100;
    private World world;
    private double[] bounds;
    private String runId;
    private boolean active;
    private boolean interrupted;
    private String encoded;
    private String digest;
    private long tick;
    private final List<Chunk> tickets = new ArrayList<>();

    private static final class Record {
        String uuid;
        String species;
        List<String> parents;
        boolean birth_inside = true;
        boolean matured_inside;
        long birth_tick;
        Long first_adult_tick;
        Long death_tick;
        List<Double> birth_position;
        List<Double> first_adult_position;
        boolean missing;
    }

    @Override public void onEnable() {
        saveDefaultConfig();
        targets.addAll(getConfig().getStringList("targets"));
        if (targets.size() != 2) throw new IllegalStateException("Two targets required");
        interrupted = Files.exists(getDataFolder().toPath().resolve("active-run"));
        Bukkit.getPluginManager().registerEvents(this, this);
        Bukkit.getScheduler().runTaskTimer(this, () -> {
            tick++;
            if (active) observe();
        }, 1L, 1L);
    }

    @Override public void onDisable() {
        for (Chunk chunk : tickets) chunk.removePluginChunkTicket(this);
    }

    private List<Double> position(Location l) {
        return List.of(l.getX(), l.getY(), l.getZ());
    }

    private boolean inside(Entity e) {
        if (bounds == null) return false;
        Location p = e.getLocation();
        return p.getWorld().equals(world) && p.getX() >= bounds[0] && p.getY() >= bounds[1]
            && p.getZ() >= bounds[2] && p.getX() < bounds[3] && p.getY() < bounds[4]
            && p.getZ() < bounds[5];
    }

    private String species(Entity entity) {
        return entity.getType().getKey().getKey();
    }

    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void onBreed(EntityBreedEvent event) {
        if (!active || !(event.getEntity() instanceof Animals baby) || baby.isAdult()) return;
        String type = species(baby);
        if (starterIds.contains(baby.getUniqueId())) return;
        if (!targets.contains(type) || !species(event.getMother()).equals(type)
            || !species(event.getFather()).equals(type) || !inside(baby)
            || !inside(event.getMother()) || !inside(event.getFather())) return;
        Record record = new Record();
        record.uuid = baby.getUniqueId().toString();
        record.species = type;
        record.parents = List.of(event.getMother().getUniqueId().toString(),
                                 event.getFather().getUniqueId().toString());
        record.birth_tick = tick;
        record.birth_position = position(baby.getLocation());
        records.putIfAbsent(baby.getUniqueId(), record);
    }

    @EventHandler(priority = EventPriority.MONITOR)
    public void onDeath(EntityDeathEvent event) {
        if (!active) return;
        Record record = records.get(event.getEntity().getUniqueId());
        if (record != null) record.death_tick = tick;
    }

    private void observe() {
        for (Map.Entry<UUID, Record> entry : records.entrySet()) {
            Record record = entry.getValue();
            if (record.first_adult_tick != null || record.death_tick != null) continue;
            Entity entity = Bukkit.getEntity(entry.getKey());
            if (!(entity instanceof Animals animal) || !entity.isValid()) {
                record.missing = true;
                continue;
            }
            if (animal.isAdult()) {
                record.first_adult_tick = tick;
                record.first_adult_position = position(animal.getLocation());
                record.matured_inside = !record.missing && inside(animal);
            }
            record.missing = false;
        }
    }

    private Map<String, Object> close() throws Exception {
        if (encoded == null) {
            observe();
            active = false;
            List<Map<String, Object>> animals = new ArrayList<>();
            for (Map.Entry<UUID, Record> entry : records.entrySet()) {
                Record r = entry.getValue();
                Entity entity = Bukkit.getEntity(entry.getKey());
                boolean alive = entity instanceof Animals a && a.isValid() && !a.isDead()
                    && a.getHealth() > 0 && r.death_tick == null;
                boolean adult = alive && ((Animals) entity).isAdult();
                Map<String, Object> row = new LinkedHashMap<>();
                row.put("uuid", r.uuid);
                row.put("species", r.species);
                row.put("parents", r.parents);
                row.put("birth_inside", r.birth_inside);
                row.put("birth_tick", r.birth_tick);
                row.put("birth_position", r.birth_position);
                row.put("matured_inside", r.matured_inside);
                row.put("first_adult_tick", r.first_adult_tick);
                row.put("first_adult_position", r.first_adult_position);
                row.put("death_tick", r.death_tick);
                row.put("alive", alive);
                row.put("adult", adult);
                row.put("inside", alive && inside(entity));
                if (alive) row.put("final_position", position(entity.getLocation()));
                animals.add(row);
            }
            Map<String, Object> snapshot = new LinkedHashMap<>();
            snapshot.put("status", "ok");
            snapshot.put("run_id", runId);
            snapshot.put("plugin_version", "4.0.0");
            snapshot.put("closed", true);
            snapshot.put("manifest_sha256", getConfig().getString("manifest_sha256"));
            snapshot.put("snapshot_tick", tick);
            snapshot.put("world", world.getUID().toString());
            snapshot.put("bounds", bounds);
            snapshot.put("enclosure", Enclosure.validate(world,plot,built,
                getConfig().getInt("min_farm_size",20),getConfig().getInt("max_farm_size",64)));
            snapshot.put("targets", targets);
            snapshot.put("animals", animals);
            snapshot.put("starters", starters);
            byte[] raw = gson.toJson(snapshot).getBytes(StandardCharsets.UTF_8);
            Files.write(getDataFolder().toPath().resolve("snapshot-" + runId + ".json"), raw);
            encoded = Base64.getEncoder().encodeToString(raw);
            digest = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(raw));
            Files.deleteIfExists(getDataFolder().toPath().resolve("active-run"));
        }
        return Map.of("status", "ok", "pages", (encoded.length() + 1799) / 1800,
                      "sha256", digest, "run_id", runId);
    }

    @EventHandler(priority=EventPriority.MONITOR,ignoreCancelled=true)
    public void placed(BlockPlaceEvent event) {
        if (active && event.getPlayer().getName().equals(getConfig().getString("username")))
            built.add(Enclosure.Cell.of(event.getBlockPlaced()));
    }
    @EventHandler(priority=EventPriority.MONITOR,ignoreCancelled=true)
    public void broken(BlockBreakEvent event) { built.remove(Enclosure.Cell.of(event.getBlock())); }

    private void spawnStarters() {
        int speciesIndex=0;
        for (String type:targets) {
            for (int index=0; index<2; index++) {
                double sx=(bounds[0]+bounds[3])/2+(index==0?-1.5:1.5);
                double sz=(bounds[2]+bounds[5])/2+(speciesIndex==0?-1.5:1.5);
                Animals animal=(Animals)world.spawnEntity(new Location(world,sx,bounds[1],sz),
                    EntityType.valueOf(type.toUpperCase(Locale.ROOT)));
                animal.setBaby(); animal.setAge(-24000); animal.setAgeLock(false);
                animal.setPersistent(true); animal.setRemoveWhenFarAway(false);
                animal.addScoreboardTag("npa_starter"); starterIds.add(animal.getUniqueId());
                starters.add(Map.of("uuid",animal.getUniqueId().toString(),"species",type,
                    "initial_age",animal.getAge(),"position",position(animal.getLocation())));
            }
            speciesIndex++;
        }
    }
    private boolean farmCommand(CommandSender sender,String[] args) {
        Map<String,Object> response;
        try {
            if (!(sender instanceof Player player) || !player.getName().equals(getConfig().getString("username"))
                || !active || interrupted || !player.getWorld().equals(world))
                throw new IllegalStateException("Only the active agent may register a farm");
            if (tick-lastClaimTick<20) throw new IllegalStateException("Wait one second between farm checks");
            lastClaimTick=tick;
            if (args.length==1 && args[0].equals("status")) {
                response=new LinkedHashMap<>(Enclosure.validate(world,plot,built,
                    getConfig().getInt("min_farm_size",20),getConfig().getInt("max_farm_size",64)));
            } else if (args.length==6 && args[0].equals("register")) {
                Enclosure.Plot candidate=new Enclosure.Plot(Integer.parseInt(args[1]),Integer.parseInt(args[2]),
                    Integer.parseInt(args[3]),Integer.parseInt(args[4]),Integer.parseInt(args[5]));
                if (plot!=null && !plot.equals(candidate)) throw new IllegalStateException("First validated farm is locked for this run");
                if (Math.hypot(player.getLocation().getX()-(candidate.x()+1+candidate.width()/2.0),
                    player.getLocation().getZ()-(candidate.z()+1+candidate.depth()/2.0))>128)
                    throw new IllegalArgumentException("Stand within 128 blocks of farm center");
                response=new LinkedHashMap<>(Enclosure.validate(world,candidate,built,
                    getConfig().getInt("min_farm_size",20),getConfig().getInt("max_farm_size",64)));
                if (Boolean.TRUE.equals(response.get("valid")) && plot==null) {
                    plot=candidate; bounds=plot.bounds();
                    for(int cx=plot.x()>>4;cx<=plot.right()>>4;cx++)
                        for(int cz=plot.z()>>4;cz<=plot.south()>>4;cz++) {
                            Chunk chunk=world.getChunkAt(cx,cz);chunk.addPluginChunkTicket(this);tickets.add(chunk);
                        }
                    spawnStarters();
                }
            } else throw new IllegalArgumentException("Use /npafarm register x y z width depth or /npafarm status");
            response.put("status","ok");response.put("starters_spawned",!starters.isEmpty());
            response.put("starters",starters);
        } catch(Exception e) { response=Map.of("status","error","error",e.toString(),"valid",false); }
        sender.sendMessage("NPA_FARM "+gson.toJson(response));return true;
    }

    @Override public boolean onCommand(CommandSender sender, Command command, String label, String[] args) {
        if (command.getName().equals("npafarm")) return farmCommand(sender,args);
        if (!(sender instanceof RemoteConsoleCommandSender || sender instanceof ConsoleCommandSender)) {
            sender.sendMessage("Server console only");
            return true;
        }
        try {
            if (interrupted) throw new IllegalStateException("Active run interrupted by server restart");
            Map<String, Object> response;
            if (args.length == 1 && args[0].equals("health")) {
                response = Map.of("status", "ok", "plugin_version", "4.0.0");
            } else if (args.length == 1 && args[0].equals("site")) {
                World siteWorld = Bukkit.getWorld(getConfig().getString("world", "world"));
                if (siteWorld == null) throw new IllegalStateException("Mission world missing");
                Location spawn = siteWorld.getSpawnLocation();
                int floorY = Math.max(siteWorld.getMinHeight() + 1,
                    Math.min(spawn.getBlockY(), siteWorld.getMaxHeight() - 20));
                response = Map.of("status", "ok", "spawn",
                    List.of(spawn.getBlockX(), floorY, spawn.getBlockZ()));
            } else if (args.length == 2 && args[0].equals("start")) {
                if (runId != null) throw new IllegalStateException("Run already initialized");
                if (!args[1].matches("[a-f0-9]{32}")) throw new IllegalArgumentException("Invalid run ID");
                world = Bukkit.getWorld(getConfig().getString("world", "world"));
                if (world == null) throw new IllegalStateException("Mission world missing");
                runId = args[1];
                Files.writeString(getDataFolder().toPath().resolve("active-run"),runId);
                active = true;
                response = Map.of("status","ok","run_id",runId,"awaiting_enclosure",true);
            } else if (args.length >= 2 && args[1].equals(runId)) {
                if (args.length == 2 && args[0].equals("close")) response = close();
                else if (args.length == 3 && args[0].equals("read") && encoded != null) {
                    int page = Integer.parseInt(args[2]);
                    if (page < 0 || page >= (encoded.length() + 1799) / 1800)
                        throw new IllegalArgumentException("Invalid page");
                    response = Map.of("status", "ok", "data", encoded.substring(page * 1800,
                        Math.min(encoded.length(), (page + 1) * 1800)));
                } else throw new IllegalArgumentException("Invalid operation");
            } else throw new IllegalArgumentException("Invalid run or command");
            sender.sendMessage("NPA_ANIMALS " + gson.toJson(response));
        } catch (Exception error) {
            getLogger().warning(error.toString());
            sender.sendMessage("NPA_ANIMALS " + gson.toJson(Map.of("status", "error",
                "error", error.toString())));
        }
        return true;
    }
}
