package fr.rush.paper;

import com.google.gson.*;
import org.bukkit.*;
import org.bukkit.block.Block;
import org.bukkit.block.data.type.Bed;
import org.bukkit.entity.*;
import org.bukkit.event.*;
import org.bukkit.event.block.*;
import org.bukkit.event.entity.*;
import org.bukkit.event.inventory.ClickType;
import org.bukkit.event.player.*;
import org.bukkit.inventory.ItemStack;
import java.util.*;

/**
 * Map editor instance (admins only): creative building plus placement of spawns, beds, shop NPCs,
 * generators and limits. Nothing is written to the map catalogue until "Enregistrer".
 */
final class Editor implements Listener {
    private static final List<String> SIDES=List.of("rouge","bleu");
    private final RushPlugin plugin;
    final GameMap map;
    private final Map<UUID,GameMap.Npc> npcMarkers=new HashMap<>();
    private final List<Entity> markers=new ArrayList<>();
    private final Map<UUID,Sidebar> sidebars=new HashMap<>();
    private GameMap.Npc selected;
    private boolean dirty, saving;

    Editor(RushPlugin plugin) {
        this.plugin=plugin;
        map=GameMap.parse(plugin.config.getAsJsonObject("editor").getAsJsonObject("map"));
    }

    void build() {
        if(map.layout!=null)Layout.generate(plugin.world,map); // Never saved yet: start from its template.
        border();
        refresh();
        Bukkit.getScheduler().runTaskTimer(plugin,this::hud,20,20);
    }
    private void border(){plugin.world.getWorldBorder().setCenter(map.borderX,map.borderZ);plugin.world.getWorldBorder().setSize(map.borderSize);}
    private void changed(){dirty=true;refresh();}

    // ------------------------------------------------------------------ markers
    private void refresh() {
        markers.forEach(Entity::remove);markers.clear();npcMarkers.clear();
        for(GameMap.Npc npc:map.npcs) {
            Villager v=plugin.world.spawn(new Location(plugin.world,npc.x,npc.y,npc.z,(float)npc.yaw,0),Villager.class,villager->{
                villager.setAI(false);villager.setInvulnerable(true);villager.setSilent(true);villager.setPersistent(false);villager.setCollidable(false);
                villager.setProfession(Arena.profession(npc.shop));
                villager.customName(Text.mm("<"+Text.sideColor(npc.side)+"><bold>"+GameMap.shopLabel(npc.shop)+"</bold></"+Text.sideColor(npc.side)+"> <gray>• clic droit : sélectionner"));
                villager.setCustomNameVisible(true);villager.setGlowing(npc==selected);
            });
            markers.add(v);npcMarkers.put(v.getUniqueId(),npc);
        }
        for(GameMap.Gen gen:map.generators)
            label(new Location(plugin.world,gen.x,gen.y-0.5,gen.z),"<gold>⛃ Générateur "+GameMap.generatorLabel(gen.type),new ItemStack(switch(gen.type){case "bronze"->Material.BRICKS;case "fer"->Material.IRON_BLOCK;default->Material.GOLD_BLOCK;}));
        for(int side=0;side<2;side++) {
            if(map.spawns[side]!=null)label(map.spawn(plugin.world,side),"<"+Text.sideColor(side)+"><bold>⚑ Spawn "+SIDES.get(side),new ItemStack(side==0?Material.RED_WOOL:Material.BLUE_WOOL));
            Block foot=map.bedFoot(plugin.world,side);
            if(foot!=null)label(foot.getLocation().add(0.5,0.6,0.5),"<"+Text.sideColor(side)+"><bold>♥ Lit "+SIDES.get(side),null);
        }
    }
    private void label(Location at,String text,ItemStack helmet) {
        ArmorStand stand=plugin.world.spawn(at,ArmorStand.class,a->{
            a.setGravity(false);a.setInvulnerable(true);a.setPersistent(false);a.setBasePlate(false);a.setSmall(true);
            a.customName(Text.mm(text));a.setCustomNameVisible(true);
            if(helmet==null){a.setInvisible(true);a.setMarker(true);}else a.getEquipment().setHelmet(helmet);
        });
        markers.add(stand);
    }

    // ------------------------------------------------------------------ HUD
    private void hud() {
        for(Player p:Bukkit.getOnlinePlayers()) {
            Sidebar side=sidebars.get(p.getUniqueId());if(side==null)continue;
            List<String> lines=new ArrayList<>();
            lines.add("<gray>Map : <gold>"+map.name);
            lines.add(dirty?"<yellow>● Modifications non enregistrées":"<green>✔ Enregistrée");
            lines.add("");
            for(int s=0;s<2;s++){int side_=s;
                lines.add("<"+Text.sideColor(s)+">"+(s==0?"Rouge":"Bleu")+"</"+Text.sideColor(s)+"> <gray>spawn "+(map.spawns[s]!=null?"<green>✔":"<red>✘")+" <gray>lit "+(map.beds[s]!=null?"<green>✔":"<red>✘")
                    +" <gray>PNJ <white>"+map.npcs.stream().filter(n->n.side==side_).count());}
            for(String type:GameMap.GENERATORS)lines.add("<gray>Générateurs "+GameMap.generatorLabel(type).toLowerCase(Locale.ROOT)+" : <white>"+map.generators.stream().filter(g->g.type.equals(type)).count());
            lines.add("");
            lines.add("<gray>Construction : <white>"+map.minY+" → "+map.maxY);
            lines.add("<gray>Vide : <white>y < "+map.voidY+" <gray>Bordure : <white>"+(int)map.borderSize);
            lines.add("");
            lines.add("<aqua>/map <gray>ou l’étoile : menu");
            side.update(lines);
            if(selected!=null)Text.actionBar(p,"<yellow>PNJ sélectionné :</yellow> "+Text.side(selected.side)+" <white>"+GameMap.shopLabel(selected.shop)+"</white> <dark_gray>•</dark_gray> <gray>/map pnj deplacer | supprimer");
        }
    }

    // ------------------------------------------------------------------ players
    @EventHandler public void join(PlayerJoinEvent e) {
        Player p=e.getPlayer();
        var board=Bukkit.getScoreboardManager().getNewScoreboard();p.setScoreboard(board);
        sidebars.put(p.getUniqueId(),new Sidebar(board,"<aqua><bold>ÉDITEUR</bold></aqua>"));
        p.setGameMode(GameMode.CREATIVE);p.setAllowFlight(true);p.setFlying(true);
        JsonObject editor=plugin.config.getAsJsonObject("editor");
        if(editor.has("position")&&editor.get("position").isJsonArray()){JsonArray a=editor.getAsJsonArray("position"); // Back where the admin saved.
            p.teleport(new Location(plugin.world,a.get(0).getAsDouble(),a.get(1).getAsDouble(),a.get(2).getAsDouble(),a.get(3).getAsFloat(),a.get(4).getAsFloat()));}
        else p.teleport(map.spawns[0]!=null?map.spawn(plugin.world,0):new Location(plugin.world,0.5,66,0.5));
        p.getInventory().setItem(8,Menus.icon(Material.NETHER_STAR,"<aqua><bold>Menu éditeur","<gray>Clic droit : ouvrir"));
        p.playerListName(Text.mm("<red><bold>ADMIN</bold></red> <white>"+p.getName()+"</white> <dark_gray>• éditeur"));
        p.sendPlayerListHeaderAndFooter(Text.mm("\n<aqua><bold>ÉDITEUR DE MAP</bold></aqua>\n<gold>"+map.name+"</gold>\n"),Text.mm("\n<gray>Rien n’est conservé sans</gray> <green>Enregistrer</green><gray>.</gray>\n"));
        Text.title(p,"<aqua><bold>Éditeur","<gold>"+map.name,10,40,10);
        Text.send(p,"<aqua><bold>Éditeur de</bold></aqua> <gold>"+map.name+"</gold><gray> — construis librement en créatif.</gray>");
        p.sendMessage(Text.mm("  <gray>• Étoile du Nether ou</gray> <white>/map</white> <gray>: placer spawns, lits, PNJ, générateurs, limites.</gray>"));
        p.sendMessage(Text.mm("  <gray>• Clic droit sur un PNJ : le sélectionner (déplacer, supprimer, changer de boutique).</gray>"));
        p.sendMessage(Text.mm("  <gray>• Pose un lit : des boutons proposent de le déclarer rouge ou bleu.</gray>"));
        p.sendMessage(Text.mm("  "+button("Enregistrer","/map save","green","Enregistrer sans quitter")+" "+button("Quitter","/map quitter","red","Retour au lobby")));
    }
    @EventHandler public void quit(PlayerQuitEvent e){sidebars.remove(e.getPlayer().getUniqueId());}
    private static String button(String label,String command,String color,String hover) {
        return "<click:run_command:'"+command+"'><hover:show_text:\"<gray>"+hover+"\"><"+color+"><bold>["+label+"]</bold></"+color+"></hover></click>";
    }

    @EventHandler public void use(PlayerInteractEvent e) {
        if(e.getItem()!=null&&e.getItem().getType()==Material.NETHER_STAR&&e.getAction().isRightClick()){e.setCancelled(true);menu(e.getPlayer());}
    }
    @EventHandler public void selectNpc(PlayerInteractEntityEvent e) {
        GameMap.Npc npc=npcMarkers.get(e.getRightClicked().getUniqueId());
        if(npc==null){if(markers.contains(e.getRightClicked()))e.setCancelled(true);return;}
        e.setCancelled(true);select(e.getPlayer(),npc);
    }
    @EventHandler public void protect(EntityDamageByEntityEvent e) {
        if(!markers.contains(e.getEntity()))return;
        e.setCancelled(true);
        GameMap.Npc npc=npcMarkers.get(e.getEntity().getUniqueId());
        if(npc!=null&&e.getDamager() instanceof Player p)select(p,npc);
    }
    @EventHandler public void stand(PlayerArmorStandManipulateEvent e){if(markers.contains(e.getRightClicked()))e.setCancelled(true);}
    private void select(Player p,GameMap.Npc npc) {
        selected=npc;refresh();Text.click(p);
        Text.send(p,"<yellow>PNJ sélectionné :</yellow> "+Text.side(npc.side)+" <white>"+GameMap.shopLabel(npc.shop)+"</white> "
            +button("Déplacer ici","/map pnj deplacer","aqua","Vers ta position et ton orientation")+" "+button("Supprimer","/map pnj supprimer","red","Supprimer ce PNJ")
            +" "+button("Boutique suivante","/map pnj boutique "+GameMap.SHOPS.get((GameMap.SHOPS.indexOf(npc.shop)+1)%GameMap.SHOPS.size()),"gold","Changer la catégorie vendue"));
    }
    @EventHandler public void place(BlockPlaceEvent e) {
        dirty=true;
        if(e.getBlockPlaced().getBlockData() instanceof Bed)Bukkit.getScheduler().runTask(plugin,()->{
            Block b=e.getBlockPlaced();if(!(b.getBlockData() instanceof Bed bed))return;
            Block foot=bed.getPart()==Bed.Part.FOOT?b:b.getRelative(bed.getFacing().getOppositeFace());
            String at=foot.getX()+" "+foot.getY()+" "+foot.getZ();
            Text.send(e.getPlayer(),"<gray>Lit posé. Le déclarer comme :</gray> "+button("Lit rouge","/map lit rouge "+at,"red","Lit de l’équipe rouge")+" "+button("Lit bleu","/map lit bleu "+at,"blue","Lit de l’équipe bleue"));
        });
    }
    @EventHandler public void breakBlock(BlockBreakEvent e) {
        dirty=true;
        for(int side=0;side<2;side++)if(map.bedSide(e.getBlock())==side){map.beds[side]=null;changed();Text.send(e.getPlayer(),"<yellow>Lit "+SIDES.get(side)+" retiré de la map.");}
    }
    @EventHandler public void mobs(CreatureSpawnEvent e){if(e.getSpawnReason()!=CreatureSpawnEvent.SpawnReason.CUSTOM)e.setCancelled(true);}

    // ------------------------------------------------------------------ menu
    void menu(Player p) {
        Menus.Gui g=new Menus.Gui(6,"<dark_gray>Éditeur •</dark_gray> <gold><bold>"+map.name+"</bold></gold>");
        List<String> info=new ArrayList<>(List.of(dirty?"<yellow>● Non enregistrée":"<green>✔ Enregistrée","<gray>PNJ : <white>"+map.npcs.size()+"</white> <dark_gray>|</dark_gray> <gray>Générateurs : <white>"+map.generators.size()));
        info.addAll(problems().stream().map(s->"<red>⚠ "+s).toList());
        g.set(4,Menus.icon(Material.BOOK,"<gold><bold>"+map.name,info.toArray(String[]::new)),()->{});
        g.set(10,Menus.icon(Material.RED_WOOL,"<red>Spawn rouge ici","<gray>Ta position et ton orientation."),()->run(p,"spawn","rouge"));
        g.set(11,Menus.icon(Material.BLUE_WOOL,"<blue>Spawn bleu ici","<gray>Ta position et ton orientation."),()->run(p,"spawn","bleu"));
        g.set(13,Menus.icon(Material.RED_BED,"<red>Lit rouge = lit visé","<gray>Regarde un lit (6 blocs) puis clique."),()->run(p,"lit","rouge"));
        g.set(14,Menus.icon(Material.BLUE_BED,"<blue>Lit bleu = lit visé","<gray>Regarde un lit (6 blocs) puis clique."),()->run(p,"lit","bleu"));
        g.set(15,Menus.icon(Material.ENDER_PEARL,"<red>TP spawn rouge"),()->run(p,"tp","rouge"));
        g.set(16,Menus.icon(Material.ENDER_PEARL,"<blue>TP spawn bleu"),()->run(p,"tp","bleu"));
        Material[] shopIcons={Material.SANDSTONE,Material.IRON_SWORD,Material.IRON_CHESTPLATE,Material.TNT};
        for(int i=0;i<4;i++){String shop=GameMap.SHOPS.get(i);
            g.set(19+i,Menus.icon(shopIcons[i],"<yellow>PNJ "+GameMap.shopLabel(shop)+" ici","<red>Clic gauche : équipe rouge","<blue>Clic droit : équipe bleue"),
                click->run(p,"pnj",shop,click.isRightClick()?"bleu":"rouge"));}
        boolean has=selected!=null;
        g.set(24,Menus.icon(Material.LEAD,has?"<aqua>Déplacer le PNJ sélectionné ici":"<dark_gray>Aucun PNJ sélectionné","<gray>Clic droit sur un PNJ pour le sélectionner."),()->run(p,"pnj","deplacer"));
        g.set(25,Menus.icon(Material.LAVA_BUCKET,has?"<red>Supprimer le PNJ sélectionné":"<dark_gray>Aucun PNJ sélectionné"),()->run(p,"pnj","supprimer"));
        g.set(28,Menus.icon(Material.BRICK,"<gold>Générateur bronze ici","<gray>1 bronze / s"),()->run(p,"gen","bronze"));
        g.set(29,Menus.icon(Material.IRON_INGOT,"<white>Générateur fer ici","<gray>1 fer / 5 s"),()->run(p,"gen","fer"));
        g.set(30,Menus.icon(Material.GOLD_INGOT,"<yellow>Générateur or ici","<gray>1 or / 10 s"),()->run(p,"gen","or"));
        g.set(31,Menus.icon(Material.BARRIER,"<red>Supprimer le générateur proche","<gray>Dans un rayon de 3 blocs."),()->run(p,"gen","supprimer"));
        g.set(33,Menus.icon(Material.IRON_BARS,"<gray>Bordure : <white>"+(int)map.borderSize,"<yellow>Clic gauche : +20","<yellow>Clic droit : −20","<yellow>Maj + clic : centrer ici"),
            click->{if(click.isShiftClick())run(p,"bordure","centre");else run(p,"bordure",String.valueOf((int)map.borderSize+(click.isRightClick()?-20:20)));});
        g.set(34,Menus.icon(Material.SCAFFOLDING,"<gray>Construction : <white>"+map.minY+" → "+map.maxY,"<yellow>Clic gauche : max = ma hauteur","<yellow>Clic droit : min = ma hauteur"),
            click->{int y=p.getLocation().getBlockY();if(click.isRightClick())run(p,"hauteur",String.valueOf(y),String.valueOf(map.maxY));else run(p,"hauteur",String.valueOf(map.minY),String.valueOf(y));});
        g.set(35,Menus.icon(Material.BLACK_CONCRETE,"<gray>Vide : <white>y < "+map.voidY,"<yellow>Clic : vide = ma hauteur"),()->run(p,"vide",String.valueOf(p.getLocation().getBlockY())));
        g.set(48,Menus.icon(Material.EMERALD_BLOCK,"<green><bold>Enregistrer","<gray>Sauvegarde le monde et les réglages.","<gray>Tu restes dans l’éditeur."),()->{p.closeInventory();save(p,false);});
        g.set(49,Menus.icon(Material.OAK_DOOR,"<gray>Fermer le menu"),()->p.closeInventory());
        g.set(50,Menus.icon(Material.DARK_OAK_DOOR,"<red><bold>Quitter l’éditeur",dirty?"<yellow>Modifications non enregistrées !":"<gray>Retour au lobby."),()->{if(dirty)confirmQuit(p);else{p.closeInventory();close(p);}});
        g.frame().open(p);
    }
    private void confirmQuit(Player p) {
        Menus.Gui g=new Menus.Gui(3,"<red><bold>Quitter l’éditeur ?");
        g.set(11,Menus.icon(Material.EMERALD_BLOCK,"<green><bold>Enregistrer et quitter"),()->{p.closeInventory();save(p,true);});
        g.set(13,Menus.icon(Material.ARROW,"<gray>Annuler"),()->menu(p));
        g.set(15,Menus.icon(Material.TNT,"<red><bold>Quitter sans enregistrer","<gray>Les modifications sont perdues."),()->{p.closeInventory();close(p);});
        g.frame().open(p);
    }
    private void run(Player p,String... args){command(p,args);if(p.getOpenInventory().getTopInventory().getHolder() instanceof Menus.Gui)Bukkit.getScheduler().runTask(plugin,()->menu(p));}

    // ------------------------------------------------------------------ commands
    List<String> problems() {
        List<String> out=new ArrayList<>();
        for(int s=0;s<2;s++){if(map.spawns[s]==null)out.add("spawn "+SIDES.get(s)+" manquant");if(map.beds[s]==null)out.add("lit "+SIDES.get(s)+" manquant");}
        if(map.generators.stream().noneMatch(g->g.type.equals("bronze")))out.add("aucun générateur de bronze");
        return out;
    }
    private int side(String value){int s=SIDES.indexOf(value);if(s<0)throw new IllegalArgumentException("Équipe : rouge ou bleu");return s;}

    void command(Player p,String[] args) {
        try {
            if(args.length==0){menu(p);return;}
            Location at=p.getLocation();
            switch(args[0]) {
                case "spawn" -> {int s=side(args[1]);map.spawns[s]=new double[]{at.getX(),at.getY(),at.getZ(),at.getYaw(),0};changed();Text.ok(p,"Spawn "+Text.side(s)+" <green>placé ici.");}
                case "tp" -> {int s=side(args[1]);if(map.spawns[s]==null)throw new IllegalArgumentException("Spawn non défini");p.teleport(map.spawn(plugin.world,s));}
                case "lit" -> {
                    int s=side(args[1]);
                    Block b=args.length==5?plugin.world.getBlockAt(Integer.parseInt(args[2]),Integer.parseInt(args[3]),Integer.parseInt(args[4])):p.getTargetBlockExact(6);
                    if(b==null||!(b.getBlockData() instanceof Bed bed))throw new IllegalArgumentException("Regarde un lit (6 blocs maximum)");
                    Block foot=bed.getPart()==Bed.Part.FOOT?b:b.getRelative(bed.getFacing().getOppositeFace());
                    map.beds[s]=new int[]{foot.getX(),foot.getY(),foot.getZ()};map.facings[s]=bed.getFacing().name().toLowerCase(Locale.ROOT);
                    if(map.beds[1-s]!=null&&Arrays.equals(map.beds[1-s],map.beds[s]))map.beds[1-s]=null;
                    changed();Text.ok(p,"Lit "+Text.side(s)+" <green>défini.");
                }
                case "pnj" -> npcCommand(p,args,at);
                case "gen" -> {
                    if(args[1].equals("supprimer")) {
                        GameMap.Gen near=map.generators.stream().min(Comparator.comparingDouble(g->at.distanceSquared(new Location(plugin.world,g.x,g.y,g.z)))).orElse(null);
                        if(near==null||at.distanceSquared(new Location(plugin.world,near.x,near.y,near.z))>9)throw new IllegalArgumentException("Aucun générateur à moins de 3 blocs");
                        map.generators.remove(near);changed();Text.ok(p,"<green>Générateur "+GameMap.generatorLabel(near.type).toLowerCase(Locale.ROOT)+" supprimé.");
                    } else {
                        if(!GameMap.GENERATORS.contains(args[1]))throw new IllegalArgumentException("Type : bronze, fer ou or");
                        GameMap.Gen gen=new GameMap.Gen();gen.x=at.getBlockX()+0.5;gen.y=at.getBlockY()+0.5;gen.z=at.getBlockZ()+0.5;gen.type=args[1];
                        map.generators.add(gen);changed();Text.ok(p,"<green>Générateur "+GameMap.generatorLabel(gen.type).toLowerCase(Locale.ROOT)+" ajouté.");
                    }
                }
                case "bordure" -> {
                    if(args[1].equals("centre")){map.borderX=at.getBlockX()+0.5;map.borderZ=at.getBlockZ()+0.5;}
                    else map.borderSize=Math.max(16,Math.min(4000,Integer.parseInt(args[1])));
                    border();changed();Text.ok(p,"<green>Bordure : "+(int)map.borderSize+" blocs.");
                }
                case "hauteur" -> {
                    int min=Integer.parseInt(args[1]),max=Integer.parseInt(args[2]);
                    if(min>=max||min<-64||max>320)throw new IllegalArgumentException("Hauteurs invalides (min < max, entre -64 et 320)");
                    map.minY=min;map.maxY=max;changed();Text.ok(p,"<green>Construction entre "+min+" et "+max+".");
                }
                case "vide" -> {map.voidY=Integer.parseInt(args[1]);changed();Text.ok(p,"<green>Mort dans le vide sous y = "+map.voidY+".");}
                case "info" -> {Text.send(p,"<gold>"+map.name+"</gold> "+(dirty?"<yellow>(non enregistrée)":"<green>(enregistrée)"));
                    for(String issue:problems())p.sendMessage(Text.mm("  <red>⚠ "+issue));}
                case "save","sauver","enregistrer" -> save(p,args.length>1&&args[1].equals("quitter"));
                case "quitter","quit" -> {
                    if(dirty&&!(args.length>1&&args[1].equals("force"))){
                        Text.send(p,"<yellow>Modifications non enregistrées.</yellow> "+button("Enregistrer et quitter","/map save quitter","green","Enregistrer puis retourner au lobby")+" "
                            +button("Quitter sans enregistrer","/map quitter force","red","Les modifications seront perdues"));
                    } else close(p);
                }
                default -> throw new IllegalArgumentException("/map [spawn|lit|tp <rouge|bleu>] [pnj <boutique> <équipe>|deplacer|supprimer] [gen <type>|supprimer] [bordure|hauteur|vide|info|save|quitter]");
            }
        } catch(ArrayIndexOutOfBoundsException|NumberFormatException e) {
            Text.error(p,"Paramètres manquants ou invalides : /map pour le menu");
        } catch(IllegalArgumentException e) {
            Text.error(p,e.getMessage());
        }
    }
    private void npcCommand(Player p,String[] args,Location at) {
        switch(args[1]) {
            case "deplacer" -> {
                if(selected==null)throw new IllegalArgumentException("Clic droit sur un PNJ pour le sélectionner");
                selected.x=at.getX();selected.y=at.getY();selected.z=at.getZ();selected.yaw=at.getYaw();changed();Text.ok(p,"<green>PNJ déplacé.");
            }
            case "supprimer" -> {
                if(selected==null)throw new IllegalArgumentException("Clic droit sur un PNJ pour le sélectionner");
                map.npcs.remove(selected);selected=null;changed();Text.ok(p,"<green>PNJ supprimé.");
            }
            case "boutique" -> {
                if(selected==null)throw new IllegalArgumentException("Clic droit sur un PNJ pour le sélectionner");
                if(!GameMap.SHOPS.contains(args[2]))throw new IllegalArgumentException("Boutique : blocs, armes, armures, equipement");
                selected.shop=args[2];changed();Text.ok(p,"<green>Boutique : "+GameMap.shopLabel(args[2])+".");
            }
            default -> {
                if(!GameMap.SHOPS.contains(args[1]))throw new IllegalArgumentException("Boutique : blocs, armes, armures, equipement");
                GameMap.Npc npc=new GameMap.Npc();npc.x=at.getX();npc.y=at.getY();npc.z=at.getZ();npc.yaw=at.getYaw();npc.shop=args[1];npc.side=side(args[2]);
                map.npcs.add(npc);selected=npc;changed();Text.ok(p,"<green>PNJ "+GameMap.shopLabel(npc.shop)+" ajouté pour l’équipe</green> "+Text.side(npc.side)+"<green>.");
            }
        }
    }

    // ------------------------------------------------------------------ save / close
    /**
     * Saving stops this server gracefully: the orchestrator copies the world once Paper has closed
     * every file (copying live region files is unsafe), then reopens the editor where the admin stood.
     */
    void save(Player p,boolean quit) {
        if(saving){Text.error(p,"Enregistrement déjà en cours");return;}
        saving=true;
        Location at=p.getLocation();
        JsonObject body=new JsonObject();body.addProperty("instance",plugin.config.get("id").getAsString());body.add("map",map.toJson());
        body.addProperty("quit",quit);
        JsonArray position=new JsonArray();for(double v:new double[]{at.getX(),at.getY(),at.getZ(),at.getYaw(),at.getPitch()})position.add(v);body.add("position",position);
        plugin.api.post("/map/save",body,r->{
            Text.ok(p,"<green><bold>Enregistrement…</bold></green> <gray>"+(quit?"retour au lobby.":"l’éditeur redémarre sur la map enregistrée (quelques secondes).")+"</gray>");
            Text.title(p,"<green><bold>Enregistrement","<gray>"+(quit?"Retour au lobby":"Réouverture de l’éditeur…"),5,60,10);
            plugin.event("map_saving",Map.of("map",map.name,"quit",quit),false);
        },error->{saving=false;Text.error(p,"Enregistrement refusé : "+error);});
    }
    private void close(Player p) {
        Text.send(p,"<gray>Fermeture de l’éditeur, retour au lobby…</gray>");
        JsonObject body=new JsonObject();body.addProperty("instance",plugin.config.get("id").getAsString());
        plugin.api.post("/map/close",body,r->{},error->Text.error(p,error));
    }
}
