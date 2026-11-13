package fr.rush.paper;

import com.google.gson.*;
import org.bukkit.Bukkit;
import org.bukkit.entity.Player;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

/** Lobby heads-up display: TAB list, personal sidebar and queue action bar, fed by /profiles. */
final class Hud {
    private final RushPlugin plugin;
    private final Map<UUID,JsonObject> profiles=new ConcurrentHashMap<>();
    private volatile JsonObject network=new JsonObject();
    private final Map<UUID,Sidebar> sidebars=new HashMap<>();
    private int ticks;
    private boolean fetching;

    Hud(RushPlugin plugin){this.plugin=plugin;}
    void start(){Bukkit.getScheduler().runTaskTimer(plugin,this::tick,20,20);}
    JsonObject profile(Player p){return profiles.get(p.getUniqueId());}
    JsonObject network(){return network;}

    static String roleName(String role){return switch(role){case "admin"->"<red>Administrateur";case "moderator"->"<gold>Modérateur";default->"<gray>Joueur";};}
    static String rolePrefix(String role){return switch(role){case "admin"->"<red><bold>ADMIN</bold></red> ";case "moderator"->"<gold><bold>MODO</bold></gold> ";default->"";};}

    void join(Player p) {
        var board=Bukkit.getScoreboardManager().getNewScoreboard();p.setScoreboard(board);
        sidebars.put(p.getUniqueId(),new Sidebar(board,"<gold><bold>RUSH</bold></gold> <gray>Training"));
        fetch();
    }
    void quit(Player p){sidebars.remove(p.getUniqueId());profiles.remove(p.getUniqueId());}

    private void tick() {
        if(ticks++%2==0)fetch();
        for(Player p:Bukkit.getOnlinePlayers())render(p);
    }
    void fetch() {
        if(fetching||Bukkit.getOnlinePlayers().isEmpty())return;
        fetching=true;
        JsonArray ids=new JsonArray();for(Player p:Bukkit.getOnlinePlayers())ids.add(p.getUniqueId().toString());
        JsonObject body=new JsonObject();body.add("players",ids);
        plugin.api.post("/profiles",body,r->{
            fetching=false;network=r.getAsJsonObject("network");
            r.getAsJsonObject("players").entrySet().forEach(e->profiles.put(UUID.fromString(e.getKey()),e.getValue().getAsJsonObject()));
        },error->fetching=false);
    }

    private void render(Player p) {
        JsonObject pr=profiles.get(p.getUniqueId());JsonObject net=network;
        int online=net.has("online")?net.get("online").getAsInt():Bukkit.getOnlinePlayers().size();
        int matches=net.has("matches")?net.get("matches").getAsInt():0;
        int queued=0;if(net.has("queue"))for(var e:net.getAsJsonObject("queue").entrySet())queued+=e.getValue().getAsInt();
        boolean offline=net.has("offline")&&net.get("offline").getAsBoolean();
        String role=pr==null?"player":pr.get("role").getAsString();
        int rating=pr==null?1000:pr.get("rating").getAsInt();
        String rank=pr==null||pr.get("rank").isJsonNull()?"—":"#"+pr.get("rank").getAsInt();
        p.playerListName(Text.mm(rolePrefix(role)+"<white>"+p.getName()+"</white> <dark_gray>•</dark_gray> <gold>"+rating+"</gold>"));
        p.sendPlayerListHeaderAndFooter(
            Text.mm("\n<gold><bold>RUSH TRAINING</bold></gold>\n<gray>1v1 • 2v2 • 5v5</gray>\n"),
            Text.mm("\n<gray>En ligne :</gray> <green>"+online+"</green> <dark_gray>|</dark_gray> <gray>Parties :</gray> <yellow>"+matches
                +"</yellow> <dark_gray>|</dark_gray> <gray>En file :</gray> <yellow>"+queued+"</yellow>\n<gray>Ton Elo :</gray> <gold>"+rating+"</gold> <dark_gray>("+rank+")</dark_gray>"
                +(offline?"\n<red><bold>MODE HORS LIGNE</bold> — parties non classées</red>":"")+"\n"));
        Sidebar side=sidebars.get(p.getUniqueId());
        String queue=null;
        if(pr!=null&&pr.has("queue")&&!pr.get("queue").isJsonNull()) {
            JsonObject q=pr.getAsJsonObject("queue");
            long waited=System.currentTimeMillis()/1000-(long)q.get("since").getAsDouble();
            queue=q.get("mode").getAsString()+" <gray>"+Text.clock(waited);
            Text.actionBar(p,"<yellow>⏳ Recherche</yellow> <gold><bold>"+q.get("mode").getAsString()+"</bold></gold> <dark_gray>•</dark_gray> <white>"+Text.clock(waited)
                +"</white> <dark_gray>•</dark_gray> <gray>/rush leave pour annuler");
        } else if(pr!=null&&pr.has("room")&&!pr.get("room").isJsonNull())
            Text.actionBar(p,"<light_purple>Salon privé</light_purple> <white>"+pr.get("room").getAsString()+"</white> <dark_gray>•</dark_gray> <gray>/private list");
        if(side==null)return;
        List<String> lines=new ArrayList<>();
        lines.add("<dark_gray>"+java.time.LocalDate.now().format(java.time.format.DateTimeFormatter.ofPattern("dd/MM/yyyy")));
        lines.add("");
        lines.add("<white>Grade : "+roleName(role));
        lines.add("<white>Elo : <gold>"+rating+"</gold> <gray>("+rank+")");
        if(pr!=null){
            lines.add("<white>Victoires : <green>"+pr.get("wins").getAsInt()+"</green> <dark_gray>/</dark_gray> <red>"+pr.get("losses").getAsInt());
            int k=pr.get("kills").getAsInt(),d=pr.get("deaths").getAsInt();
            lines.add("<white>K/D : <yellow>"+String.format(Locale.ROOT,"%.2f",d==0?k:(double)k/d)+"</yellow> <gray>("+k+"/"+d+")");
        }
        lines.add("");
        int party=pr==null?0:pr.getAsJsonArray("party").size();
        lines.add("<white>Équipe : "+(party==0?"<gray>solo":"<aqua>"+party+"/5"+(pr.get("leader").getAsBoolean()?" <gold>♛":"")));
        lines.add("<white>File : "+(queue==null?"<gray>—":"<yellow>"+queue));
        lines.add("");
        lines.add("<white>En ligne : <green>"+online);
        lines.add("<white>Parties : <yellow>"+matches);
        if(offline)lines.add("<red>Hors ligne : non classé");
        lines.add("");
        lines.add("<gold>/rush <gray>• menu");
        side.update(lines);
    }
}
