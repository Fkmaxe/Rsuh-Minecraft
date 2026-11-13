package fr.rush.paper;

import com.google.gson.*;
import org.bukkit.*;
import org.bukkit.block.Block;
import org.bukkit.block.BlockFace;
import org.bukkit.block.data.type.Bed;
import java.util.*;

/** Map definition shared by games and the editor (mirror of orchestrator/maps.py). */
final class GameMap {
    static final List<String> SHOPS=List.of("blocs","armes","armures","equipement");
    static final List<String> GENERATORS=List.of("bronze","fer","or");
    static final class Npc { double x,y,z,yaw; String shop; int side; }
    static final class Gen { double x,y,z; String type; }

    String name, layout;
    JsonArray modes; boolean enabled;
    final double[][] spawns=new double[2][];
    final int[][] beds=new int[2][];
    final String[] facings=new String[2];
    final List<Npc> npcs=new ArrayList<>();
    final List<Gen> generators=new ArrayList<>();
    int minY, maxY, voidY;
    double borderX, borderZ, borderSize;

    static GameMap parse(JsonObject o) {
        GameMap m=new GameMap();
        m.name=o.get("name").getAsString();
        m.layout=o.has("layout")&&!o.get("layout").isJsonNull()?o.get("layout").getAsString():null;
        m.modes=o.has("modes")?o.getAsJsonArray("modes"):new JsonArray();
        m.enabled=o.has("enabled")&&o.get("enabled").getAsBoolean();
        for(int i=0;i<2;i++) {
            JsonElement s=o.getAsJsonArray("spawns").get(i);
            if(!s.isJsonNull()){JsonArray a=s.getAsJsonArray();m.spawns[i]=new double[]{a.get(0).getAsDouble(),a.get(1).getAsDouble(),a.get(2).getAsDouble(),a.get(3).getAsDouble(),a.get(4).getAsDouble()};}
            JsonElement b=o.getAsJsonArray("beds").get(i);
            if(!b.isJsonNull()){JsonArray a=b.getAsJsonArray();m.beds[i]=new int[]{a.get(0).getAsInt(),a.get(1).getAsInt(),a.get(2).getAsInt()};m.facings[i]=a.get(3).getAsString();}
        }
        for(JsonElement e:o.getAsJsonArray("npcs")) {
            JsonObject n=e.getAsJsonObject();Npc npc=new Npc();
            npc.x=n.get("x").getAsDouble();npc.y=n.get("y").getAsDouble();npc.z=n.get("z").getAsDouble();npc.yaw=n.get("yaw").getAsDouble();
            npc.shop=n.get("shop").getAsString();npc.side=n.get("side").getAsInt();m.npcs.add(npc);
        }
        for(JsonElement e:o.getAsJsonArray("generators")) {
            JsonObject g=e.getAsJsonObject();Gen gen=new Gen();
            gen.x=g.get("x").getAsDouble();gen.y=g.get("y").getAsDouble();gen.z=g.get("z").getAsDouble();gen.type=g.get("type").getAsString();m.generators.add(gen);
        }
        JsonObject build=o.getAsJsonObject("build");m.minY=build.get("min_y").getAsInt();m.maxY=build.get("max_y").getAsInt();
        m.voidY=o.get("void_y").getAsInt();
        JsonObject border=o.getAsJsonObject("border");
        m.borderX=border.get("x").getAsDouble();m.borderZ=border.get("z").getAsDouble();m.borderSize=border.get("size").getAsDouble();
        return m;
    }

    JsonObject toJson() {
        JsonObject o=new JsonObject();
        o.addProperty("name",name);o.add("modes",modes);o.addProperty("enabled",enabled);
        JsonArray spawnArray=new JsonArray(),bedArray=new JsonArray();
        for(int i=0;i<2;i++) {
            if(spawns[i]==null)spawnArray.add(JsonNull.INSTANCE);else{JsonArray a=new JsonArray();for(double v:spawns[i])a.add(v);spawnArray.add(a);}
            if(beds[i]==null)bedArray.add(JsonNull.INSTANCE);else{JsonArray a=new JsonArray();for(int v:beds[i])a.add(v);a.add(facings[i]);bedArray.add(a);}
        }
        o.add("spawns",spawnArray);o.add("beds",bedArray);
        JsonArray npcArray=new JsonArray();
        for(Npc n:npcs){JsonObject j=new JsonObject();j.addProperty("x",n.x);j.addProperty("y",n.y);j.addProperty("z",n.z);j.addProperty("yaw",n.yaw);j.addProperty("shop",n.shop);j.addProperty("side",n.side);npcArray.add(j);}
        o.add("npcs",npcArray);
        JsonArray genArray=new JsonArray();
        for(Gen g:generators){JsonObject j=new JsonObject();j.addProperty("x",g.x);j.addProperty("y",g.y);j.addProperty("z",g.z);j.addProperty("type",g.type);genArray.add(j);}
        o.add("generators",genArray);
        JsonObject build=new JsonObject();build.addProperty("min_y",minY);build.addProperty("max_y",maxY);o.add("build",build);
        o.addProperty("void_y",voidY);
        JsonObject border=new JsonObject();border.addProperty("x",borderX);border.addProperty("z",borderZ);border.addProperty("size",borderSize);o.add("border",border);
        return o;
    }

    Location spawn(World w,int side) {
        double[] s=spawns[side];
        return s==null?new Location(w,0.5,65,0.5):new Location(w,s[0],s[1],s[2],(float)s[3],(float)s[4]);
    }
    Block bedFoot(World w,int side) { return beds[side]==null?null:w.getBlockAt(beds[side][0],beds[side][1],beds[side][2]); }
    BlockFace facing(int side) { return BlockFace.valueOf(facings[side].toUpperCase(Locale.ROOT)); }

    /** Team owning the bed this block belongs to, or -1. */
    int bedSide(Block block) {
        if(!(block.getBlockData() instanceof Bed bed))return -1;
        Block foot=bed.getPart()==Bed.Part.FOOT?block:block.getRelative(bed.getFacing().getOppositeFace());
        for(int side=0;side<2;side++)
            if(beds[side]!=null&&foot.getX()==beds[side][0]&&foot.getY()==beds[side][1]&&foot.getZ()==beds[side][2])return side;
        return -1;
    }
    static String shopLabel(String shop) {
        return switch(shop){case "blocs"->"Blocs";case "armes"->"Armes";case "armures"->"Armures";default->"Équipement";};
    }
    static String generatorLabel(String type) { return switch(type){case "bronze"->"Bronze";case "fer"->"Fer";default->"Or";}; }
    static Material generatorItem(String type) { return switch(type){case "bronze"->Material.BRICK;case "fer"->Material.IRON_INGOT;default->Material.GOLD_INGOT;}; }
    static int generatorPeriod(String type) { return switch(type){case "bronze"->1;case "fer"->5;default->10;}; }
}
