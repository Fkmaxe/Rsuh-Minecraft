package fr.rush.paper;

import com.google.gson.*;
import org.bukkit.*;
import org.bukkit.attribute.Attribute;
import org.bukkit.command.*;
import org.bukkit.entity.*;
import org.bukkit.event.*;
import org.bukkit.event.player.*;
import org.bukkit.event.entity.*;
import org.bukkit.event.block.*;
import org.bukkit.generator.ChunkGenerator;
import org.bukkit.plugin.java.JavaPlugin;
import java.nio.file.*;
import java.util.*;
import java.util.logging.*;

public final class RushPlugin extends JavaPlugin implements Listener {
    JsonObject config;
    Api api;
    Arena arena;
    Menus menus;
    World world;
    private Logger events;
    private FileHandler handler;

    @Override public void onEnable() {
        try {
            config=JsonParser.parseString(Files.readString(getDataFolder().toPath().resolve("instance.json"))).getAsJsonObject();
            events=Logger.getLogger("rush.events."+config.get("id").getAsString()); events.setUseParentHandlers(false);
            handler=new FileHandler(getDataFolder()+"/events-%g.jsonl",10_000_000,5,true);
            handler.setFormatter(new java.util.logging.Formatter(){public String format(LogRecord r){return r.getMessage()+"\n";}});
            events.addHandler(handler);
            api=new Api(this); menus=new Menus(this);
            getServer().getPluginManager().registerEvents(this,this);
            getServer().getPluginManager().registerEvents(menus,this);
            // onEnable runs before the default world is created for STARTUP plugins.
            Bukkit.getScheduler().runTask(this,()-> {
                world=Bukkit.getWorlds().getFirst();
                world.setGameRule(GameRule.DO_DAYLIGHT_CYCLE,false);
                world.setGameRule(GameRule.DO_WEATHER_CYCLE,false);
                world.setGameRule(GameRule.DO_MOB_SPAWNING,false);
                world.setGameRule(GameRule.DO_IMMEDIATE_RESPAWN,true);
                world.setGameRule(GameRule.KEEP_INVENTORY,false);
                world.setGameRule(GameRule.ANNOUNCE_ADVANCEMENTS,false);
                world.setTime(6000); world.setStorm(false);
                world.setAutoSave(false); // Worlds are rebuilt on every start: saving only costs I/O.
                world.getWorldBorder().setCenter(0,0); world.getWorldBorder().setSize(180);
                if(!config.get("match").isJsonNull()) {
                    arena=new Arena(this);
                    getServer().getPluginManager().registerEvents(arena,this);
                    arena.build();
                } else {
                    for(int x=-12;x<=12;x++) for(int z=-12;z<=12;z++) world.getBlockAt(x,64,z).setType(Material.SMOOTH_STONE,false);
                    world.setSpawnLocation(0,65,0);
                }
                event("server_ready",Map.of("instance",config.get("id").getAsString()),false);
                getLogger().info("RUSH_READY");
            });
        } catch(Exception e) { throw new IllegalStateException("Configuration Rush invalide",e); }
    }

    @Override public ChunkGenerator getDefaultWorldGenerator(String worldName,String id) {
        return new ChunkGenerator() {
            @Override public boolean shouldGenerateNoise(){return false;}
            @Override public boolean shouldGenerateSurface(){return false;}
            @Override public boolean shouldGenerateBedrock(){return false;}
            @Override public boolean shouldGenerateCaves(){return false;}
            @Override public boolean shouldGenerateDecorations(){return false;}
            @Override public boolean shouldGenerateMobs(){return false;}
            @Override public boolean shouldGenerateStructures(){return false;}
        };
    }

    void event(String type,Map<String,?> fields,boolean diagnostic) {
        if(diagnostic && !config.get("diagnostic").getAsBoolean()) return;
        Map<String,Object> data=new LinkedHashMap<>(); data.put("time",java.time.Instant.now().toString()); data.put("event",type);
        data.put("instance",config.get("id").getAsString()); data.putAll(fields); events.info(new Gson().toJson(data));
    }

    void combat(Player p) {
        var attack=p.getAttribute(Attribute.ATTACK_SPEED);
        if(attack!=null) attack.setBaseValue(config.getAsJsonObject("pvp").get("attack_speed").getAsDouble());
        p.setMaximumNoDamageTicks(config.getAsJsonObject("pvp").get("hurt_ticks").getAsInt());
        p.setFoodLevel(20); p.setSaturation(20);
    }

    @EventHandler public void join(PlayerJoinEvent e) {
        Player p=e.getPlayer(); combat(p); e.setJoinMessage(null);
        if(arena==null) {
            p.getInventory().clear(); p.setGameMode(GameMode.ADVENTURE); p.teleport(new Location(world,0.5,65,0.5));
            p.getInventory().setItem(0,Menus.item(Material.COMPASS,"§aMenu Rush"));
            p.sendMessage("§aRush Training §7— /rush, /team, /friend, /private, /top");
        }
    }
    @EventHandler public void quit(PlayerQuitEvent e) { e.setQuitMessage(null); }
    @EventHandler public void hunger(FoodLevelChangeEvent e) { e.setCancelled(true); }
    @EventHandler public void lobbyDamage(EntityDamageEvent e) { if(arena==null && e.getEntity() instanceof Player) e.setCancelled(true); }
    @EventHandler public void lobbyBreak(BlockBreakEvent e) { if(arena==null)e.setCancelled(true); }
    @EventHandler public void lobbyPlace(BlockPlaceEvent e) { if(arena==null)e.setCancelled(true); }
    @EventHandler public void lobbyMove(PlayerMoveEvent e) {
        if(arena==null && e.getTo().getY()<60) e.getPlayer().teleport(new Location(world,0.5,65,0.5));
    }
    @EventHandler public void use(PlayerInteractEvent e) {
        if(arena==null && e.getItem()!=null && e.getItem().getType()==Material.COMPASS) {e.setCancelled(true);menus.main(e.getPlayer());}
    }
    @EventHandler public void drop(PlayerDropItemEvent e) { if(arena==null)e.setCancelled(true); }

    @Override public boolean onCommand(CommandSender sender,Command cmd,String label,String[] args) {
        if(!(sender instanceof Player p)) {sender.sendMessage("Administration : python3 -m orchestrator.cli");return true;}
        String action=cmd.getName();
        if(action.equals("shop")) {if(arena!=null)menus.shop(p);return true;}
        if(action.equals("rush")) {
            if(args.length==0) {menus.main(p);return true;}
            action=args[0]; args=Arrays.copyOfRange(args,1,args.length);
            if(Set.of("1v1","2v2","5v5").contains(action)) {args=new String[]{action};action="queue";}
        }
        if(arena!=null && !Set.of("leave","status","leaderboard","friend","admin").contains(action)) {p.sendMessage("§cQuittez la partie avec /rush leave avant de vous réinscrire.");return true;}
        String finalAction=action;
        api.command(p,action,args,result->{
            p.sendMessage("§a"+result.get("message").getAsString());
            if(result.has("lines")) for(JsonElement line:result.getAsJsonArray("lines"))p.sendMessage("§7"+line.getAsString());
            if(finalAction.equals("leave") && arena!=null) arena.forfeit(p);
        });
        return true;
    }

    @Override public List<String> onTabComplete(CommandSender s,Command cmd,String label,String[] args) {
        if(args.length!=1)return Collections.emptyList();
        List<String> choices=switch(cmd.getName()) {
            case "rush" -> List.of("1v1","2v2","5v5","leave","status","admin");
            case "team" -> List.of("invite","accept","kick","leave","list");
            case "friend" -> List.of("add","accept","remove","list");
            case "private" -> List.of("create","join","start","list");
            case "leaderboard" -> List.of("global","1v1","2v2","5v5");
            default -> List.of();
        };
        return choices.stream().filter(v->v.startsWith(args[0].toLowerCase())).toList();
    }
    @Override public void onDisable(){if(api!=null)api.close();if(handler!=null){events.removeHandler(handler);handler.close();}}
}
