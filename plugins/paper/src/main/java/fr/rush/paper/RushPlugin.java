package fr.rush.paper;

import com.google.gson.*;
import io.papermc.paper.event.player.AsyncChatEvent;
import net.kyori.adventure.text.Component;
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

/** Entry point: the same plugin runs the lobby, a game (match) or the map editor (editor). */
public final class RushPlugin extends JavaPlugin implements Listener {
    JsonObject config;
    Api api;
    Arena arena;
    Editor editor;
    Menus menus;
    Hud hud;
    World world;
    private Logger events;
    private FileHandler handler;

    boolean lobby(){return arena==null&&editor==null;}

    @Override public void onEnable() {
        try {
            config=JsonParser.parseString(Files.readString(getDataFolder().toPath().resolve("instance.json"))).getAsJsonObject();
            events=Logger.getLogger("rush.events."+config.get("id").getAsString()); events.setUseParentHandlers(false);
            handler=new FileHandler(getDataFolder()+"/events-%g.jsonl",10_000_000,5,true);
            handler.setFormatter(new java.util.logging.Formatter(){public String format(LogRecord r){return r.getMessage()+"\n";}});
            events.addHandler(handler);
            api=new Api(this); menus=new Menus(this); hud=new Hud(this);
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
                world.getWorldBorder().setCenter(0,0); world.getWorldBorder().setSize(180);
                if(config.has("editor")&&!config.get("editor").isJsonNull()) {
                    // The editor keeps autosave on: its world is the work in progress.
                    editor=new Editor(this);
                    getServer().getPluginManager().registerEvents(editor,this);
                    editor.build();
                } else {
                    world.setAutoSave(false); // Lobby and games are rebuilt on every start: saving only costs I/O.
                    if(!config.get("match").isJsonNull()) {
                        arena=new Arena(this);
                        getServer().getPluginManager().registerEvents(arena,this);
                        arena.build();
                    } else {
                        for(int x=-12;x<=12;x++) for(int z=-12;z<=12;z++) world.getBlockAt(x,64,z).setType((x+z)%2==0?Material.SMOOTH_STONE:Material.POLISHED_ANDESITE,false);
                        for(int x=-12;x<=12;x++)for(int z=-12;z<=12;z++)if(Math.abs(x)==12||Math.abs(z)==12)world.getBlockAt(x,65,z).setType(Material.STONE_BRICK_WALL,false);
                        world.setSpawnLocation(0,65,0);
                        hud.start();
                    }
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

    // ------------------------------------------------------------------ lobby
    @EventHandler public void join(PlayerJoinEvent e) {
        Player p=e.getPlayer(); combat(p); e.joinMessage(null);
        if(!lobby())return;
        p.getInventory().clear(); p.setGameMode(GameMode.ADVENTURE); p.teleport(new Location(world,0.5,65,0.5));
        p.getInventory().setItem(4,Menus.icon(Material.COMPASS,"<gold><bold>Menu Rush</bold></gold> <gray>(clic droit)","<gray>Jouer, équipe, amis, classements."));
        hud.join(p);
        Text.title(p,"<gold><bold>RUSH</bold></gold>","<gray>Entraînement 1v1 • 2v2 • 5v5",10,40,15);
        Text.send(p,"<gray>Bienvenue,</gray> <yellow>"+p.getName()+"</yellow><gray> !</gray> "
            +"<click:run_command:'/rush'><hover:show_text:\"<gray>Ouvrir le menu\"><gold><bold>[Menu]</bold></gold></hover></click> "
            +"<click:run_command:'/rush 1v1'><hover:show_text:\"<gray>Rejoindre la file 1v1\"><green><bold>[Jouer 1v1]</bold></green></hover></click> "
            +"<click:run_command:'/leaderboard'><hover:show_text:\"<gray>Top 10 global\"><yellow><bold>[Classement]</bold></yellow></hover></click>");
    }
    @EventHandler public void quit(PlayerQuitEvent e) { e.quitMessage(null); if(lobby())hud.quit(e.getPlayer()); }
    @EventHandler public void hunger(FoodLevelChangeEvent e) { e.setCancelled(true); }
    @EventHandler public void lobbyDamage(EntityDamageEvent e) { if((lobby()||editor!=null) && e.getEntity() instanceof Player) e.setCancelled(true); }
    @EventHandler public void lobbyBreak(BlockBreakEvent e) { if(lobby())e.setCancelled(true); }
    @EventHandler public void lobbyPlace(BlockPlaceEvent e) { if(lobby())e.setCancelled(true); }
    @EventHandler public void lobbyMove(PlayerMoveEvent e) {
        if(lobby() && e.getTo().getY()<60) e.getPlayer().teleport(new Location(world,0.5,65,0.5));
    }
    @EventHandler public void use(PlayerInteractEvent e) {
        if(lobby() && e.getItem()!=null && e.getItem().getType()==Material.COMPASS) {e.setCancelled(true);menus.main(e.getPlayer());}
    }
    @EventHandler public void drop(PlayerDropItemEvent e) { if(lobby())e.setCancelled(true); }

    /** Chat: role and Elo in the lobby, team color in games. Player text is never parsed as markup. */
    @EventHandler public void chat(AsyncChatEvent e) {
        Player sender=e.getPlayer();
        String prefix;
        if(arena!=null) prefix=arena.chatName(sender);
        else if(editor!=null) prefix="<aqua>[Éditeur]</aqua> <white>"+sender.getName();
        else {
            JsonObject pr=hud.profile(sender);
            prefix=pr==null?"<white>"+sender.getName():Hud.rolePrefix(pr.get("role").getAsString())+"<white>"+sender.getName()+"</white> <dark_gray>[</dark_gray><gold>"+pr.get("rating").getAsInt()+"</gold><dark_gray>]</dark_gray>";
        }
        Component name=Text.mm(prefix);
        e.renderer((source,displayName,message,viewer)->name.append(Text.mm(" <dark_gray>»</dark_gray> ")).append(message.colorIfAbsent(net.kyori.adventure.text.format.NamedTextColor.WHITE)));
    }

    // ------------------------------------------------------------------ commands
    @Override public boolean onCommand(CommandSender sender,Command cmd,String label,String[] args) {
        if(!(sender instanceof Player p)) {sender.sendMessage("Administration : python3 -m orchestrator.cli");return true;}
        String action=cmd.getName();
        if(action.equals("shop")) {if(arena!=null)menus.shop(p);else Text.error(p,"La boutique n’existe que pendant une partie");return true;}
        if(action.equals("map")&&editor!=null){editor.command(p,args);return true;}
        if(action.equals("map")&&args.length==0&&lobby()){menus.maps(p);return true;}
        if(action.equals("rush")) {
            if(args.length==0) {if(lobby())menus.main(p);else Text.error(p,"Le menu est disponible au lobby");return true;}
            action=args[0]; args=Arrays.copyOfRange(args,1,args.length);
            if(Set.of("1v1","2v2","5v5").contains(action)) {args=new String[]{action};action="queue";}
        }
        if(action.equals("friend")&&args.length==0&&lobby()){menus.friends(p);return true;}
        if(action.equals("team")&&args.length==0&&lobby()){menus.team(p);return true;}
        if(action.equals("private")&&args.length==0&&lobby()){menus.privateMenu(p);return true;}
        if(!lobby() && !Set.of("leave","status","leaderboard","friend","admin").contains(action)) {Text.error(p,"Commande indisponible ici : /rush leave pour revenir au lobby");return true;}
        String finalAction=action;
        api.command(p,action,args,result->{
            Text.ok(p,result.get("message").getAsString());
            if(result.has("lines")) for(JsonElement line:result.getAsJsonArray("lines"))p.sendMessage(Text.mm("  "+line.getAsString()));
            if(finalAction.equals("leave") && arena!=null) arena.forfeit(p);
        });
        return true;
    }

    @Override public List<String> onTabComplete(CommandSender s,Command cmd,String label,String[] args) {
        List<String> choices;
        if(args.length==1) choices=switch(cmd.getName()) {
            case "rush" -> List.of("1v1","2v2","5v5","leave","status","admin");
            case "team" -> List.of("invite","accept","deny","kick","leave","list");
            case "friend" -> List.of("add","accept","deny","remove","list");
            case "private" -> List.of("create","join","invite","start","list");
            case "leaderboard" -> List.of("global","1v1","2v2","5v5");
            case "map" -> editor!=null?List.of("spawn","lit","tp","pnj","gen","bordure","hauteur","vide","info","save","quitter")
                :List.of("list","create","edit","enable","disable","modes","delete");
            default -> List.of();
        };
        else if(args.length==2) choices=switch(cmd.getName()+" "+args[0]) {
            case "private create" -> List.of("1v1","2v2","5v5");
            case "team invite","team accept","team deny","team kick","friend add","friend accept","friend deny","friend remove","private invite" ->
                Bukkit.getOnlinePlayers().stream().map(Player::getName).toList();
            case "map spawn","map lit","map tp" -> List.of("rouge","bleu");
            case "map pnj" -> List.of("blocs","armes","armures","equipement","deplacer","supprimer","boutique");
            case "map gen" -> List.of("bronze","fer","or","supprimer");
            case "map bordure" -> List.of("centre","100","180","300");
            default -> List.of();
        };
        else if(args.length==3&&cmd.getName().equals("map")&&args[0].equals("pnj")) choices=args[1].equals("boutique")?GameMap.SHOPS:List.of("rouge","bleu");
        else if(args.length==3&&cmd.getName().equals("map")&&args[0].equals("create")) choices=List.of("classic","vide");
        else choices=List.of();
        String typed=args.length==0?"":args[args.length-1].toLowerCase(Locale.ROOT);
        return choices.stream().filter(v->v.toLowerCase(Locale.ROOT).startsWith(typed)).toList();
    }
    @Override public void onDisable(){if(api!=null)api.close();if(handler!=null){events.removeHandler(handler);handler.close();}}
}
