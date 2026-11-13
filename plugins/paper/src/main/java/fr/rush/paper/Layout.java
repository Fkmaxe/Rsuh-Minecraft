package fr.rush.paper;

import org.bukkit.*;
import org.bukkit.block.Block;
import org.bukkit.block.BlockFace;
import org.bukkit.block.data.type.Bed;

/** Built-in layouts for maps that have no saved world yet. */
final class Layout {
    private Layout() {}

    static void generate(World world,GameMap map) {
        if("classic".equals(map.layout))classic(world,map);
        else if("empty".equals(map.layout))
            for(int x=-2;x<=2;x++)for(int z=-2;z<=2;z++)world.getBlockAt(x,63,z).setType(Material.GLASS,false);
    }

    private static void classic(World world,GameMap map) {
        for(int side=0;side<2;side++) {
            int x=side==0?-32:32;
            for(int dx=-7;dx<=7;dx++)for(int z=-7;z<=7;z++) {
                world.getBlockAt(x+dx,63,z).setType(Material.BEDROCK,false);
                world.getBlockAt(x+dx,64,z).setType(side==0?Material.RED_TERRACOTTA:Material.BLUE_TERRACOTTA,false);
            }
            if(map.beds[side]!=null)placeBed(map.bedFoot(world,side),map.facing(side),side);
        }
        for(int x=-4;x<=4;x++)for(int z=-4;z<=4;z++)world.getBlockAt(x,64,z).setType(Material.SANDSTONE,false);
    }

    static void placeBed(Block foot,BlockFace facing,int side) {
        Material type=side==0?Material.RED_BED:Material.BLUE_BED;
        for(Bed.Part part:Bed.Part.values()) {
            Block block=part==Bed.Part.FOOT?foot:foot.getRelative(facing);
            Bed data=(Bed)Bukkit.createBlockData(type);data.setFacing(facing);data.setPart(part);block.setBlockData(data,false);
        }
    }
}
