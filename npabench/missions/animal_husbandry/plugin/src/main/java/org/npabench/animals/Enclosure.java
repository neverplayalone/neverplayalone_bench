package org.npabench.animals;

import java.util.*;
import org.bukkit.*;
import org.bukkit.block.Block;
import org.bukkit.block.BlockFace;
import org.bukkit.block.data.type.Fence;
import org.bukkit.block.data.type.Gate;
import org.bukkit.util.BoundingBox;

/** Bounded, synchronous validation of a player-built rectangular enclosure. */
public final class Enclosure {
    public record Cell(UUID world, int x, int y, int z) {
        public static Cell of(Block b) { return new Cell(b.getWorld().getUID(), b.getX(), b.getY(), b.getZ()); }
    }
    public record Plot(int x, int y, int z, int width, int depth) {
        public int right() { return x + width + 1; }
        public int south() { return z + depth + 1; }
        public double[] bounds() { return new double[]{x + 1, y, z + 1, right(), y + 16, south()}; }
    }
    private final List<Map<String,Object>> issues = new ArrayList<>();
    private int issueCount;
    private void issue(String code, int x, int y, int z) {
        issueCount++;
        if (issues.size() < 16) issues.add(Map.of("code", code, "x", x, "y", y, "z", z));
    }
    public static boolean fullCube(Block b) {
        if (!b.getType().isSolid()) return false;
        Collection<BoundingBox> boxes = b.getCollisionShape().getBoundingBoxes();
        if (boxes.size() != 1) return false;
        BoundingBox box = boxes.iterator().next();
        return Math.abs(box.getWidthX()-1) < 1e-6 && Math.abs(box.getHeight()-1) < 1e-6
            && Math.abs(box.getWidthZ()-1) < 1e-6;
    }
    private static boolean walkable(Block b) {
        return b.isPassable() && !b.isLiquid() && !Set.of(Material.FIRE, Material.SOUL_FIRE,
            Material.SWEET_BERRY_BUSH, Material.POWDER_SNOW, Material.COBWEB).contains(b.getType());
    }
    public static Map<String,Object> validate(World world, Plot plot, Set<Cell> owned, int minimum, int maximum) {
        Enclosure check = new Enclosure();
        if (plot == null) return Map.of("valid", false, "issue_count", 1,
            "issues", List.of(Map.of("code", "not_registered")));
        if (plot.width() < minimum || plot.depth() < minimum || plot.width() > maximum || plot.depth() > maximum
            || Math.abs((long)plot.x()) > 29999000 || Math.abs((long)plot.z()) > 29999000
            || plot.y() < world.getMinHeight()+1 || plot.y() > world.getMaxHeight()-16) {
            check.issue("invalid_dimensions_or_height", plot.x(), plot.y(), plot.z());
            return check.result(plot);
        }
        for (int cx = plot.x() >> 4; cx <= plot.right() >> 4; cx++)
            for (int cz = plot.z() >> 4; cz <= plot.south() >> 4; cz++)
                if (!world.isChunkLoaded(cx,cz)) {
                    check.issue("unloaded_chunk", cx*16, plot.y(), cz*16);
                    return check.result(plot);
                }
        for (int x=plot.x(); x<=plot.right(); x++) for (int z=plot.z(); z<=plot.south(); z++) {
            if (!world.getWorldBorder().isInside(new Location(world,x+.5,plot.y(),z+.5)))
                check.issue("outside_world_border",x,plot.y(),z);
            if (!fullCube(world.getBlockAt(x,plot.y()-1,z))) check.issue("floor_not_full_solid",x,plot.y()-1,z);
            boolean edge = x==plot.x() || x==plot.right() || z==plot.z() || z==plot.south();
            if (!edge) {
                for (int y=plot.y(); y<=plot.y()+1; y++)
                    if (!walkable(world.getBlockAt(x,y,z))) check.issue("interior_obstructed",x,y,z);
                continue;
            }
            Block block=world.getBlockAt(x,plot.y(),z);
            if (!(block.getBlockData() instanceof Fence) && !(block.getBlockData() instanceof Gate)) {
                check.issue("missing_fence",x,plot.y(),z); continue;
            }
            if (!owned.contains(Cell.of(block))) check.issue("fence_not_agent_built",x,plot.y(),z);
            boolean corner=(x==plot.x() || x==plot.right()) && (z==plot.z() || z==plot.south());
            if (block.getBlockData() instanceof Gate gate) {
                if (corner) check.issue("gate_at_corner",x,plot.y(),z);
                if (gate.isOpen()) check.issue("gate_open",x,plot.y(),z);
                boolean facingEastWest=gate.getFacing()==BlockFace.EAST || gate.getFacing()==BlockFace.WEST;
                if (!corner && facingEastWest != (x==plot.x() || x==plot.right()))
                    check.issue("gate_wrong_orientation",x,plot.y(),z);
            } else if (block.getBlockData() instanceof Fence fence) {
                if (fence.isWaterlogged()) check.issue("waterlogged_fence",x,plot.y(),z);
                List<BlockFace> needed=new ArrayList<>();
                if (x>plot.x() && (z==plot.z() || z==plot.south())) needed.add(BlockFace.WEST);
                if (x<plot.right() && (z==plot.z() || z==plot.south())) needed.add(BlockFace.EAST);
                if (z>plot.z() && (x==plot.x() || x==plot.right())) needed.add(BlockFace.NORTH);
                if (z<plot.south() && (x==plot.x() || x==plot.right())) needed.add(BlockFace.SOUTH);
                for (BlockFace face:needed) if (!fence.hasFace(face)) check.issue("fence_disconnected",x,plot.y(),z);
            }
        }
        return check.result(plot);
    }
    private Map<String,Object> result(Plot p) {
        return Map.of("valid",issueCount==0,"issue_count",issueCount,"issues",issues,
            "corner",List.of(p.x(),p.y(),p.z()),"width",p.width(),"depth",p.depth(),"bounds",p.bounds());
    }
}
