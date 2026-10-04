package org.npabench.farming;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.bukkit.Bukkit;
import org.bukkit.Material;
import org.bukkit.block.Block;
import org.bukkit.block.BlockState;
import org.bukkit.block.data.BlockData;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.EventPriority;
import org.bukkit.event.Listener;
import org.bukkit.event.block.BlockBreakEvent;
import org.bukkit.event.block.BlockGrowEvent;
import org.bukkit.event.block.BlockPistonExtendEvent;
import org.bukkit.event.block.BlockPistonRetractEvent;
import org.bukkit.event.block.BlockPlaceEvent;
import org.bukkit.event.block.BlockSpreadEvent;
import org.bukkit.event.player.PlayerHarvestBlockEvent;
import org.bukkit.event.world.StructureGrowEvent;
import org.bukkit.plugin.java.JavaPlugin;
import org.bukkit.scoreboard.Criteria;
import org.bukkit.scoreboard.Objective;
import org.bukkit.scoreboard.Score;
import org.bukkit.scoreboard.Scoreboard;

/** Records real crop harvests without crediting player-placed, un-grown blocks. */
public final class FarmingLedgerPlugin extends JavaPlugin implements Listener {
    private static final String HARVEST_OBJECTIVE = "nff_harvest";
    private static final String SYSTEM_OBJECTIVE = "nff_system";
    private static final String READY_HOLDER = "#plugin";

    private final List<Target> targets = new ArrayList<>();
    private final Set<Material> trackedMaterials = new HashSet<>();
    // A coordinate remains in this set until the actual block grows to maturity or is broken.
    private final Set<Position> unGrownPlacements = new HashSet<>();
    private String agentName;

    private record Position(UUID world, int x, int y, int z) {
        static Position of(Block block) {
            return new Position(
                block.getWorld().getUID(), block.getX(), block.getY(), block.getZ()
            );
        }
    }

    private record Target(String holder, BlockData mature) {
        boolean matches(BlockData blockData) {
            return blockData.matches(mature);
        }
    }

    @Override
    public void onEnable() {
        saveDefaultConfig();
        agentName = getConfig().getString("username");
        if (agentName == null || agentName.isBlank()) {
            throw new IllegalStateException("Farming ledger has no username");
        }
        for (Map<?, ?> row : getConfig().getMapList("targets")) {
            String holder = String.valueOf(row.get("holder"));
            String planted = String.valueOf(row.get("planted_block"));
            String mature = String.valueOf(row.get("mature_block"));
            BlockData plantedData = Bukkit.createBlockData("minecraft:" + planted);
            BlockData matureData = Bukkit.createBlockData("minecraft:" + mature);
            trackedMaterials.add(plantedData.getMaterial());
            trackedMaterials.add(matureData.getMaterial());
            targets.add(new Target(holder, matureData));
        }
        if (targets.isEmpty()) {
            throw new IllegalStateException("Farming ledger has no targets");
        }
        objective(HARVEST_OBJECTIVE);
        objective(SYSTEM_OBJECTIVE).getScore(READY_HOLDER).setScore(1);
        Bukkit.getPluginManager().registerEvents(this, this);
        getLogger().info("Tracking " + targets.size() + " farming targets for " + agentName);
    }

    @Override
    public void onDisable() {
        Scoreboard board = Bukkit.getScoreboardManager().getMainScoreboard();
        Objective system = board.getObjective(SYSTEM_OBJECTIVE);
        if (system != null) {
            system.getScore(READY_HOLDER).setScore(0);
        }
        unGrownPlacements.clear();
    }

    private Objective objective(String name) {
        Scoreboard board = Bukkit.getScoreboardManager().getMainScoreboard();
        Objective existing = board.getObjective(name);
        return existing != null ? existing : board.registerNewObjective(name, Criteria.DUMMY, name);
    }

    private boolean isAgent(Player player) {
        return player.getName().equals(agentName);
    }

    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void onPlace(BlockPlaceEvent event) {
        Block block = event.getBlockPlaced();
        if (trackedMaterials.contains(block.getType())) {
            unGrownPlacements.add(Position.of(block));
        }
    }

    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void onGrow(BlockGrowEvent event) {
        BlockData oldData = event.getBlock().getBlockData();
        BlockData newData = event.getNewState().getBlockData();
        // Aging an already-mature, player-placed block does not make it eligible.
        for (Target target : targets) {
            if (target.matches(newData) && !target.matches(oldData)) {
                unGrownPlacements.remove(Position.of(event.getBlock()));
                return;
            }
        }
    }

    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void onSpread(BlockSpreadEvent event) {
        // Mushrooms (and other spreading plants) create a genuinely new block.
        for (Target target : targets) {
            if (target.matches(event.getNewState().getBlockData())) {
                unGrownPlacements.remove(Position.of(event.getBlock()));
                return;
            }
        }
    }

    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void onStructureGrow(StructureGrowEvent event) {
        for (BlockState state : event.getBlocks()) {
            for (Target target : targets) {
                if (target.matches(state.getBlockData())) {
                    unGrownPlacements.remove(Position.of(state.getBlock()));
                    break;
                }
            }
        }
    }

    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void onBreak(BlockBreakEvent event) {
        Block block = event.getBlock();
        if (isAgent(event.getPlayer())) {
            credit(block);
        }
        unGrownPlacements.remove(Position.of(block));
    }

    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void onHarvest(PlayerHarvestBlockEvent event) {
        if (isAgent(event.getPlayer())) {
            credit(event.getHarvestedBlock());
        }
    }

    // Moving an existing block must never turn an un-grown placement into a
    // supposedly natural block at a new coordinate.
    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void onPistonExtend(BlockPistonExtendEvent event) {
        for (Block source : event.getBlocks()) {
            unGrownPlacements.add(Position.of(source));
            unGrownPlacements.add(Position.of(source.getRelative(event.getDirection())));
        }
    }

    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void onPistonRetract(BlockPistonRetractEvent event) {
        for (Block source : event.getBlocks()) {
            unGrownPlacements.add(Position.of(source));
            unGrownPlacements.add(Position.of(source.getRelative(event.getDirection().getOppositeFace())));
        }
    }

    private void credit(Block block) {
        if (unGrownPlacements.contains(Position.of(block))) {
            return;
        }
        for (Target target : targets) {
            if (target.matches(block.getBlockData())) {
                Score score = objective(HARVEST_OBJECTIVE).getScore(target.holder());
                score.setScore(score.getScore() + 1);
                return;
            }
        }
    }
}
