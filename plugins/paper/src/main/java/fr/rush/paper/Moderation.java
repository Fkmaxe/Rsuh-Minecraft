package fr.rush.paper;

import com.google.gson.*;
import io.papermc.paper.event.player.AsyncChatEvent;
import org.bukkit.*;
import org.bukkit.entity.Player;
import org.bukkit.event.*;
import org.bukkit.event.block.*;
import org.bukkit.event.entity.*;
import org.bukkit.event.inventory.ClickType;
import org.bukkit.event.player.*;
import org.bukkit.inventory.ItemStack;
import org.bukkit.inventory.PlayerInventory;
import java.util.*;

/**
 * Applies the network's moderation state on this server (polled every second): vanish, freeze and
 * mutes. Also hosts the staff tools that act locally (inventory view, teleport) and the staff menu.
 */
final class Moderation implements Listener {
    private final RushPlugin plugin;
    private volatile Set<UUID> frozen=Set.of(), vanished=Set.of();
    private volatile Map<UUID,String> staff=Map.of();
    private volatile Map<UUID,JsonObject> muted=Map.of();
    private boolean polling;
    private int ticks;

    Moderation(RushPlugin plugin){this.plugin=plugin;}
    void start(){Bukkit.getScheduler().runTaskTimer(plugin,this::tick,20,20);}
    boolean isStaff(Player p){return staff.containsKey(p.getUniqueId());}
    boolean isFrozen(Player p){return frozen.contains(p.getUniqueId());}
    boolean isVanished(Player p){return vanished.contains(p.getUniqueId());}

    private void tick() {
        ticks++;
        if(!polling&&!Bukkit.getOnlinePlayers().isEmpty()) {
            polling=true;
            JsonArray ids=new JsonArray();for(Player p:Bukkit.getOnlinePlayers())ids.add(p.getUniqueId().toString());
            JsonObject body=new JsonObject();body.add("players",ids);
            plugin.api.post("/moderation",body,r->{
                polling=false;
                Set<UUID> f=new HashSet<>(),v=new HashSet<>();Map<UUID,String> s=new HashMap<>();Map<UUID,JsonObject> m=new HashMap<>();
                r.getAsJsonArray("frozen").forEach(e->f.add(UUID.fromString(e.getAsString())));
                r.getAsJsonArray("vanished").forEach(e->v.add(UUID.fromString(e.getAsString())));
                r.getAsJsonObject("staff").entrySet().forEach(e->s.put(UUID.fromString(e.getKey()),e.getValue().getAsString()));
                r.getAsJsonObject("muted").entrySet().forEach(e->m.put(UUID.fromString(e.getKey()),e.getValue().getAsJsonObject()));
                frozen=f;vanished=v;staff=s;muted=m;apply();
            },error->polling=false);
        }
        for(Player p:Bukkit.getOnlinePlayers()) {
            if(isFrozen(p)&&ticks%2==0){Text.title(p,"<aqua><bold>❄ GELÉ","<red>Ne te déconnecte pas • attends le staff",0,45,5);}
            else if(isVanished(p))Text.actionBar(p,"<aqua>Vanish actif</aqua> <dark_gray>•</dark_gray> <gray>invisible des joueurs • /vanish • /staff");
        }
    }
    /** Vanished staff are hidden (TAB included) from everyone except other staff members. */
    private void apply() {
        for(Player viewer:Bukkit.getOnlinePlayers())for(Player target:Bukkit.getOnlinePlayers()) {
            if(viewer==target)continue;
            boolean hide=isVanished(target)&&!isStaff(viewer);
            if(hide&&viewer.canSee(target))viewer.hidePlayer(plugin,target);
            else if(!hide&&!viewer.canSee(target))viewer.showPlayer(plugin,target);
        }
    }
    @EventHandler public void join(PlayerJoinEvent e){Bukkit.getScheduler().runTask(plugin,this::apply);}

    // ------------------------------------------------------------------ freeze
    @EventHandler(priority=EventPriority.LOWEST) public void move(PlayerMoveEvent e) {
        if(!isFrozen(e.getPlayer()))return;
        Location from=e.getFrom(),to=e.getTo();
        if(from.getX()!=to.getX()||from.getY()!=to.getY()||from.getZ()!=to.getZ()){Location back=from.clone();back.setYaw(to.getYaw());back.setPitch(to.getPitch());e.setTo(back);}
    }
    @EventHandler(priority=EventPriority.LOWEST) public void breakBlock(BlockBreakEvent e){if(isFrozen(e.getPlayer()))e.setCancelled(true);}
    @EventHandler(priority=EventPriority.LOWEST) public void place(BlockPlaceEvent e){if(isFrozen(e.getPlayer()))e.setCancelled(true);}
    @EventHandler(priority=EventPriority.LOWEST) public void interact(PlayerInteractEvent e){if(isFrozen(e.getPlayer()))e.setCancelled(true);}
    @EventHandler(priority=EventPriority.LOWEST) public void interactEntity(PlayerInteractEntityEvent e){if(isFrozen(e.getPlayer()))e.setCancelled(true);}
    @EventHandler(priority=EventPriority.LOWEST) public void drop(PlayerDropItemEvent e){if(isFrozen(e.getPlayer()))e.setCancelled(true);}
    @EventHandler(priority=EventPriority.LOWEST) public void damage(EntityDamageEvent e){if(e.getEntity() instanceof Player p&&isFrozen(p))e.setCancelled(true);}
    @EventHandler(priority=EventPriority.LOWEST) public void attack(EntityDamageByEntityEvent e){if(e.getDamager() instanceof Player p&&isFrozen(p))e.setCancelled(true);}
    @EventHandler(priority=EventPriority.LOWEST) public void command(PlayerCommandPreprocessEvent e) {
        if(!isFrozen(e.getPlayer()))return;
        String cmd=e.getMessage().split(" ")[0].toLowerCase(Locale.ROOT);
        if(!cmd.equals("/report")){e.setCancelled(true);Text.error(e.getPlayer(),"Commandes bloquées pendant le freeze");}
    }

    // ------------------------------------------------------------------ chat
    @EventHandler(priority=EventPriority.LOWEST) public void chat(AsyncChatEvent e) {
        Player p=e.getPlayer();
        JsonObject mute=muted.get(p.getUniqueId());
        if(mute!=null) {
            e.setCancelled(true);
            long left=mute.get("until").isJsonNull()?-1:Math.max(60,(long)(mute.get("until").getAsDouble()-System.currentTimeMillis()/1000.0));
            String end=left<0?"définitif":"encore "+(left>=3600?left/3600+" h "+left%3600/60+" min":left/60+" min");
            String reason=mute.get("reason").getAsString();
            Bukkit.getScheduler().runTask(plugin,()->{Text.error(p,"Tu es réduit au silence ("+end+") : "+reason);});
        } else if(isVanished(p)&&plugin.editor==null) {
            e.setCancelled(true);
            Bukkit.getScheduler().runTask(plugin,()->Text.error(p,"En vanish, ton message te trahirait : utilise /sc pour le chat staff (ou /vanish)"));
        }
    }

    // ------------------------------------------------------------------ local tools
    private Player local(Player staffMember,String name) {
        Player target=Bukkit.getPlayerExact(name);
        if(target==null)Text.error(staffMember,name+" n’est pas sur ce serveur : /spec "+name+" pour rejoindre sa partie");
        return target;
    }
    void invsee(Player p,String name) {
        if(!isStaff(p)){Text.error(p,"Staff uniquement");return;}
        Player target=local(p,name);if(target==null)return;
        PlayerInventory inv=target.getInventory();
        Menus.Gui g=new Menus.Gui(6,"<dark_gray>Inventaire •</dark_gray> <yellow>"+target.getName());
        for(int i=9;i<36;i++)if(inv.getItem(i)!=null)g.set(i-9,inv.getItem(i).clone(),(Runnable)null);
        for(int i=0;i<9;i++)if(inv.getItem(i)!=null)g.set(27+i,inv.getItem(i).clone(),(Runnable)null);
        ItemStack[] armor={inv.getHelmet(),inv.getChestplate(),inv.getLeggings(),inv.getBoots()};
        for(int i=0;i<4;i++)g.set(45+i,armor[i]!=null?armor[i].clone():Menus.icon(Material.GRAY_STAINED_GLASS_PANE,"<dark_gray>Vide"),(Runnable)null);
        if(inv.getItemInOffHand().getType()!=Material.AIR)g.set(50,inv.getItemInOffHand().clone(),(Runnable)null);
        g.set(53,Menus.icon(Material.GOLDEN_APPLE,"<yellow>"+target.getName(),"<gray>Vie : <red>"+Math.round(target.getHealth())+"/20","<gray>Mode : <white>"+target.getGameMode().name().toLowerCase(Locale.ROOT),
            "<gray>Ping : <white>"+target.getPing()+" ms","","<gray>Lecture seule • clic : rafraîchir"),()->invsee(p,name));
        for(int i=36;i<45;i++)g.set(i,Menus.icon(Material.BLACK_STAINED_GLASS_PANE," "),(Runnable)null);
        g.open(p);
    }
    void goTo(Player p,String name) {
        if(!isStaff(p)){Text.error(p,"Staff uniquement");return;}
        if(plugin.arena!=null&&p.getGameMode()!=GameMode.SPECTATOR){Text.error(p,"Téléportation réservée aux spectateurs pendant une partie");return;}
        Player target=local(p,name);if(target==null)return;
        p.teleport(target.getLocation());Text.ok(p,"<gray>Téléporté sur</gray> <yellow>"+target.getName());
    }

    // ------------------------------------------------------------------ staff menu
    void menu(Player p) {
        plugin.api.command(p,"staff",new String[]{},r->{
            Menus.Gui g=new Menus.Gui(5,"<aqua><bold>Staff</bold></aqua>");
            boolean v=isVanished(p);
            g.set(4,v?Menus.glow(Menus.icon(Material.ENDER_EYE,"<aqua><bold>Vanish : activé","<gray>Invisible des joueurs.","","<yellow>Clic : désactiver")):Menus.icon(Material.ENDER_PEARL,"<gray><bold>Vanish : désactivé","","<yellow>Clic : activer"),
                ()->{plugin.menus.command(p,"vanish");Bukkit.getScheduler().runTaskLater(plugin,()->menu(p),25);});
            int games=r.getAsJsonArray("matches").size(),online=r.getAsJsonArray("players").size(),reports=r.getAsJsonArray("reports").size();
            g.set(20,Menus.count(Menus.icon(Material.IRON_SWORD,"<gold><bold>Parties en cours","<gray>"+games+" partie(s)","","<yellow>▶ Clic : regarder"),games),()->games(p,r));
            g.set(22,Menus.count(Menus.icon(Material.PLAYER_HEAD,"<green><bold>Joueurs connectés","<gray>"+online+" joueur(s)","","<yellow>▶ Clic : sanctionner, freeze, spec…"),online),()->players(p,r));
            g.set(24,Menus.count(Menus.icon(Material.WRITABLE_BOOK,"<red><bold>Signalements","<gray>"+reports+" récent(s)","","<yellow>▶ Clic : consulter"),reports),()->reports(p,r));
            g.set(31,Menus.icon(Material.OAK_SIGN,"<aqua>Chat staff","<gray>/sc <message>"),()->suggest(p,"/sc ","Clique puis écris ton message staff"));
            g.set(40,Menus.icon(Material.OAK_DOOR,"<gray>Fermer"),()->p.closeInventory());
            g.frame().open(p);
        });
    }
    private void suggest(Player p,String cmd,String hint) {
        p.closeInventory();
        Text.send(p,"<click:suggest_command:'"+cmd+"'><hover:show_text:\"<gray>Préremplir\"><yellow><u>"+hint+"</u></yellow></hover></click> <dark_gray>("+cmd.trim()+")</dark_gray>");
    }
    private void games(Player p,JsonObject r) {
        Menus.Gui g=new Menus.Gui(6,"<aqua>Staff •</aqua> <gold><bold>Parties</bold></gold>");
        int[] slots={10,11,12,13,14,15,16,19,20,21,22,23,24,25,28,29,30,31,32,33,34};int i=0;
        for(JsonElement e:r.getAsJsonArray("matches")) {
            if(i>=slots.length)break;JsonObject m=e.getAsJsonObject();String id=m.get("id").getAsString();
            JsonArray teams=m.getAsJsonArray("teams");
            g.set(slots[i++],Menus.icon(m.get("ranked").getAsBoolean()?Material.DIAMOND_SWORD:Material.IRON_SWORD,"<gold>"+m.get("mode").getAsString()+" <dark_gray>•</dark_gray> <white>"+m.get("map").getAsString(),
                "<dark_gray>"+id,"<red>Rouge : <white>"+String.join(", ",new Gson().fromJson(teams.get(0),String[].class)),"<blue>Bleu : <white>"+String.join(", ",new Gson().fromJson(teams.get(1),String[].class)),
                "<gray>Durée : <white>"+Text.clock(m.get("elapsed").getAsLong())+"</white> <dark_gray>|</dark_gray> <gray>Spectateurs : <white>"+m.get("spectators").getAsInt(),
                "<gray>"+(m.get("ranked").getAsBoolean()?"Classée":"Non classée")+" • "+m.get("state").getAsString(),"","<yellow>Clic : regarder (vanish)","<red>Maj + clic droit : annuler sans résultat"),
                click->{p.closeInventory();if(click==ClickType.SHIFT_RIGHT)plugin.menus.command(p,"admin","abort",id);else plugin.menus.command(p,"spec",id);});
        }
        if(i==0)g.set(22,Menus.icon(Material.STRUCTURE_VOID,"<gray>Aucune partie en cours"),()->{});
        g.set(49,Menus.back(),()->menu(p));
        g.frame().open(p);
    }
    private void players(Player p,JsonObject r) {
        Menus.Gui g=new Menus.Gui(6,"<aqua>Staff •</aqua> <green><bold>Joueurs</bold></green>");int i=0;
        for(JsonElement e:r.getAsJsonArray("players")) {
            if(i>=45)break;JsonObject row=e.getAsJsonObject();String name=row.get("name").getAsString();
            List<String> lore=new ArrayList<>(List.of("<gray>Rôle : "+Hud.roleName(row.get("role").getAsString()),"<gray>Serveur : <white>"+row.get("server").getAsString()));
            if(row.get("frozen").getAsBoolean())lore.add("<aqua>❄ Gelé");
            if(row.get("muted").getAsBoolean())lore.add("<red>✉ Muet");
            if(row.get("vanished").getAsBoolean())lore.add("<dark_aqua>● Vanish");
            lore.add("");lore.add("<yellow>▶ Clic : actions");
            g.set(i++,Menus.head(name,(row.get("frozen").getAsBoolean()?"<aqua>":"<white>")+name,lore.toArray(String[]::new)),()->actions(p,row));
        }
        g.set(49,Menus.back(),()->menu(p));
        g.open(p);
    }
    void actions(Player p,JsonObject row) {
        String name=row.get("name").getAsString();String server=row.get("server").getAsString();
        boolean here=Bukkit.getPlayerExact(name)!=null,frozenNow=row.get("frozen").getAsBoolean();
        Menus.Gui g=new Menus.Gui(5,"<aqua>Staff •</aqua> <yellow><bold>"+name);
        g.set(4,Menus.head(name,"<yellow>"+name,"<gray>Serveur : <white>"+server),()->{});
        g.set(10,server.startsWith("rush-")?Menus.icon(Material.ENDER_EYE,"<aqua>Regarder sa partie","<gray>Vanish automatique."):Menus.icon(Material.GRAY_DYE,"<dark_gray>Pas en partie"),
            ()->{if(server.startsWith("rush-")){p.closeInventory();plugin.menus.command(p,"spec",name);}});
        g.set(11,here?Menus.icon(Material.ENDER_PEARL,"<aqua>Se téléporter"):Menus.icon(Material.GRAY_DYE,"<dark_gray>Autre serveur"),()->{if(here){p.closeInventory();goTo(p,name);}});
        g.set(12,here?Menus.icon(Material.CHEST,"<gold>Voir l’inventaire","<gray>Lecture seule."):Menus.icon(Material.GRAY_DYE,"<dark_gray>Autre serveur","<gray>Utilise « Regarder sa partie »."),()->{if(here)invsee(p,name);});
        g.set(13,Menus.icon(frozenNow?Material.BLUE_ICE:Material.PACKED_ICE,frozenNow?"<green>Dégeler":"<aqua>Geler (freeze)","<gray>Bloque déplacements, combats, commandes."),()->{p.closeInventory();plugin.menus.command(p,"freeze",name);});
        g.set(14,Menus.icon(Material.BOOK,"<gray>Historique des sanctions"),()->{p.closeInventory();plugin.menus.command(p,"history",name);});
        g.set(19,Menus.icon(Material.PAPER,"<yellow>Avertir","<gray>Saisir la raison."),()->suggest(p,"/warn "+name+" ","Clique puis écris la raison de l’avertissement"));
        g.set(20,Menus.icon(Material.ORANGE_DYE,"<gold>Rendre muet","<yellow>Clic gauche : 15 min","<yellow>Clic droit : 1 h","<gray>Maj + clic : durée et raison libres"),
            click->{if(click.isShiftClick()){suggest(p,"/mute "+name+" ","Clique puis écris : durée raison");return;}p.closeInventory();plugin.menus.command(p,"mute",name,click.isRightClick()?"1h":"15m","Comportement dans le chat");});
        g.set(21,Menus.icon(Material.MILK_BUCKET,"<green>Lever le mute"),()->{p.closeInventory();plugin.menus.command(p,"unmute",name);});
        g.set(22,Menus.icon(Material.IRON_DOOR,"<red>Expulser (kick)","<yellow>Clic : raison « Décision du staff »","<gray>Maj + clic : raison libre"),
            click->{if(click.isShiftClick()){suggest(p,"/kick "+name+" ","Clique puis écris la raison");return;}p.closeInventory();plugin.menus.command(p,"kick",name);});
        g.set(23,Menus.icon(Material.BARRIER,"<dark_red><bold>Bannir","<yellow>Clic gauche : 1 jour","<yellow>Clic droit : 7 jours","<gray>Maj + clic : durée (perm : admin) et raison"),
            click->{if(click.isShiftClick()){suggest(p,"/ban "+name+" ","Clique puis écris : durée raison");return;}p.closeInventory();plugin.menus.command(p,"ban",name,click.isRightClick()?"7d":"1d","Décision du staff");});
        g.set(24,Menus.icon(Material.LIME_DYE,"<green>Débannir"),()->{p.closeInventory();plugin.menus.command(p,"unban",name);});
        g.set(40,Menus.back(),()->menu(p));
        g.frame().open(p);
    }
    private void reports(Player p,JsonObject r) {
        Menus.Gui g=new Menus.Gui(6,"<aqua>Staff •</aqua> <red><bold>Signalements");
        int[] slots={10,11,12,13,14,15,16,19,20,21,22,23,24,25};int i=0;
        for(JsonElement e:r.getAsJsonArray("reports")) {
            if(i>=slots.length)break;JsonObject row=e.getAsJsonObject();String target=row.get("target").getAsString();
            g.set(slots[i++],Menus.icon(Material.PAPER,"<yellow>"+target,"<gray>Par : <white>"+row.get("reporter").getAsString(),"<gray>Il y a "+row.get("ago").getAsString(),
                "<white>"+row.get("reason").getAsString().replace("<","‹"),"","<yellow>Clic gauche : regarder sa partie","<yellow>Clic droit : historique"),
                click->{p.closeInventory();plugin.menus.command(p,click.isRightClick()?"history":"spec",target);});
        }
        if(i==0)g.set(22,Menus.icon(Material.STRUCTURE_VOID,"<gray>Aucun signalement"),()->{});
        g.set(49,Menus.back(),()->menu(p));
        g.frame().open(p);
    }
}
