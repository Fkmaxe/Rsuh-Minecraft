package fr.rush.paper;

import com.google.gson.*;
import net.kyori.adventure.text.format.NamedTextColor;
import org.bukkit.*;
import org.bukkit.attribute.Attribute;
import org.bukkit.block.Block;
import org.bukkit.entity.*;
import org.bukkit.event.*;
import org.bukkit.event.block.*;
import org.bukkit.event.entity.*;
import org.bukkit.event.player.*;
import org.bukkit.inventory.ItemStack;
import org.bukkit.scoreboard.*;
import org.bukkit.util.Vector;
import java.util.*;

/** One Rush game on one map: rules, PvP, generators, shop NPCs and the in-game HUD. */
final class Arena implements Listener {
    private static final int SUDDEN_DEATH=1200, TIME_LIMIT=1500, JOIN_TIMEOUT=120;
    private final RushPlugin plugin;
    final GameMap map;
    private final String mode;
    private final Map<UUID,Integer> sides=new HashMap<>();
    private final Map<UUID,int[]> stats=new HashMap<>();
    private final Set<UUID> eliminated=new HashSet<>();
    private final Set<UUID> fellInVoid=new HashSet<>();
    private final Set<String> placed=new HashSet<>();
    private final Map<UUID,UUID> lastAttacker=new HashMap<>();
    private final Map<UUID,Long> lastHit=new HashMap<>();
    private final Map<UUID,Long> protectedUntil=new HashMap<>();
    private final Map<UUID,GameMap.Npc> npcEntities=new HashMap<>();
    private final boolean[] beds={true,true};
    private String state="waiting";
    private int seconds, countdown=5, elapsed;
    private boolean sending;
    private JsonObject result;
    private String abortReason;
    private Scoreboard board;
    private Sidebar sidebar;
    private final Team[] teams=new Team[2];

    Arena(RushPlugin plugin) {
        this.plugin=plugin;
        JsonObject match=plugin.config.getAsJsonObject("match");
        map=GameMap.parse(match.getAsJsonObject("map"));
        mode=match.get("mode").getAsString();
        JsonArray rosters=match.getAsJsonArray("teams");
        for(int side=0;side<2;side++)for(JsonElement e:rosters.get(side).getAsJsonArray()) {
            UUID id=UUID.fromString(e.getAsString());sides.put(id,side);stats.put(id,new int[3]);
        }
    }
    private Location spawn(int side){return map.spawn(plugin.world,side);}
    private String key(Block b){return b.getX()+":"+b.getY()+":"+b.getZ();}
    private double pvp(String key){return plugin.config.getAsJsonObject("pvp").get(key).getAsDouble();}
    private boolean playing(Player p){return state.equals("running")&&sides.containsKey(p.getUniqueId())&&!eliminated.contains(p.getUniqueId())&&p.getGameMode()==GameMode.SURVIVAL;}
    private boolean shielded(Player p){return System.currentTimeMillis()<protectedUntil.getOrDefault(p.getUniqueId(),0L);}
    private String colored(UUID id){return "<"+Text.sideColor(sides.getOrDefault(id,0))+">"+name(id)+"</"+Text.sideColor(sides.getOrDefault(id,0))+">";}
    private String name(UUID id){Player p=Bukkit.getPlayer(id);return p!=null?p.getName():Optional.ofNullable(Bukkit.getOfflinePlayer(id).getName()).orElse("?");}
    private void broadcast(String text){for(Player p:Bukkit.getOnlinePlayers())Text.send(p,text);}
    private void everyone(Sound sound,float pitch){for(Player p:Bukkit.getOnlinePlayers())Text.sound(p,sound,pitch);}

    void build() {
        Layout.generate(plugin.world,map);
        plugin.world.getWorldBorder().setCenter(map.borderX,map.borderZ);plugin.world.getWorldBorder().setSize(map.borderSize);
        plugin.world.setSpawnLocation(spawn(0));
        for(GameMap.Npc npc:map.npcs)spawnNpc(npc);
        board=Bukkit.getScoreboardManager().getNewScoreboard();
        for(int side=0;side<2;side++) {
            teams[side]=board.registerNewTeam(side+(side==0?"rouge":"bleu"));
            teams[side].color(side==0?NamedTextColor.RED:NamedTextColor.BLUE);
            teams[side].prefix(Text.mm(side==0?"<red>[R] ":"<blue>[B] "));
            teams[side].setAllowFriendlyFire(false);
        }
        sidebar=new Sidebar(board,"<gold><bold>RUSH</bold></gold> <yellow>"+mode);
        Bukkit.getScheduler().runTaskTimer(plugin,this::tick,20,20);
        // A saved map carries its beds as blocks: report them so a broken map is visible in the logs.
        List<Boolean> bedBlocks=new ArrayList<>();
        for(int side=0;side<2;side++){Block foot=map.bedFoot(plugin.world,side);bedBlocks.add(foot!=null&&map.bedSide(foot)==side);}
        if(bedBlocks.contains(false))plugin.getLogger().warning("Map "+map.name+" : lit introuvable "+bedBlocks);
        plugin.event("arena_created",Map.of("players",sides.size(),"map",map.name,"beds",bedBlocks,"npcs",npcEntities.size(),"pvp",plugin.config.getAsJsonObject("pvp")),false);
    }
    private void spawnNpc(GameMap.Npc npc) {
        Villager v=plugin.world.spawn(new Location(plugin.world,npc.x,npc.y,npc.z,(float)npc.yaw,0),Villager.class,villager->{
            villager.setAI(false);villager.setInvulnerable(true);villager.setSilent(true);villager.setCollidable(false);villager.setPersistent(false);
            villager.setProfession(profession(npc.shop));
            villager.customName(Text.mm("<"+Text.sideColor(npc.side)+"><bold>"+GameMap.shopLabel(npc.shop)+"</bold></"+Text.sideColor(npc.side)+"> <gray>• Boutique"));
            villager.setCustomNameVisible(true);
        });
        npcEntities.put(v.getUniqueId(),npc);
    }
    static Villager.Profession profession(String shop) {
        return switch(shop){case "blocs"->Villager.Profession.MASON;case "armes"->Villager.Profession.WEAPONSMITH;case "armures"->Villager.Profession.ARMORER;default->Villager.Profession.TOOLSMITH;};
    }

    // ------------------------------------------------------------------ clock
    private void tick() {
        seconds++;
        if(state.equals("waiting")||state.equals("countdown")) {
            boolean full=sides.keySet().stream().allMatch(id->Bukkit.getPlayer(id)!=null);
            if(full) {
                state="countdown";
                if(countdown--<=0) {
                    state="running";
                    for(UUID id:sides.keySet()) {Player p=Bukkit.getPlayer(id);p.setGameMode(GameMode.SURVIVAL);equip(p);
                        Text.title(p,"<green><bold>C’est parti !","<gray>Détruis le lit adverse",0,30,10);Text.sound(p,Sound.ENTITY_PLAYER_LEVELUP,1f);}
                    broadcast("<green><bold>La partie commence !</bold></green> <gray>Détruis le lit adverse. Boutique : PNJ ou</gray> <white>/shop</white> <gray>à ta base.</gray>");
                    plugin.event("game_started",Map.of(),false);
                } else for(Player p:Bukkit.getOnlinePlayers()){
                    String color=countdown>=3?"green":countdown>=1?"yellow":"red";
                    Text.title(p,"<"+color+"><bold>"+(countdown+1),"<gray>Préparez-vous",0,25,0);Text.sound(p,Sound.BLOCK_NOTE_BLOCK_PLING,0.8f+0.2f*(5-countdown));
                }
            } else {state="waiting";countdown=5;}
            if(seconds>JOIN_TIMEOUT)abort("roster_join_timeout");
        } else if(state.equals("running")) {
            elapsed++;
            for(GameMap.Gen gen:map.generators)
                if(elapsed%GameMap.generatorPeriod(gen.type)==0)resource(new Location(plugin.world,gen.x,gen.y,gen.z),GameMap.generatorItem(gen.type));
            if(elapsed==SUDDEN_DEATH-60)broadcast("<yellow>⚠ Mort subite dans 1 minute :</yellow> <gray>les lits seront détruits.</gray>");
            if(elapsed==SUDDEN_DEATH){destroyBed(0);destroyBed(1);broadcast("<red><bold>Mort subite !</bold></red> <gray>Les lits sont détruits.</gray>");
                for(Player p:Bukkit.getOnlinePlayers())Text.title(p,"<red><bold>MORT SUBITE","<gray>Plus aucune réapparition",5,40,10);everyone(Sound.ENTITY_ENDER_DRAGON_GROWL,1f);}
            if(elapsed>=TIME_LIMIT)abort("draw_time_limit");
            if(elapsed%30==0)plugin.event("performance",Map.of("tps",Bukkit.getTPS()[0],"players",Bukkit.getOnlinePlayers().size()),true);
        } else if(state.equals("finished")&&seconds%5==0&&!sending)submit();
        hud();
    }
    private void resource(Location loc,Material type) {
        int count=plugin.world.getNearbyEntities(loc,2,2,2).stream().filter(e->e instanceof Item i&&i.getItemStack().getType()==type).mapToInt(e->((Item)e).getItemStack().getAmount()).sum();
        if(count<64){Item drop=plugin.world.dropItem(loc,new ItemStack(type));drop.setVelocity(new Vector());}
    }

    // ------------------------------------------------------------------ HUD
    private int alive(int side){return (int)sides.entrySet().stream().filter(e->e.getValue()==side&&!eliminated.contains(e.getKey())&&Bukkit.getPlayer(e.getKey())!=null).count();}
    private void hud() {
        if(sidebar==null)return;
        List<String> lines=new ArrayList<>();
        lines.add("<gray>Carte : <white>"+map.name);
        if(state.equals("running"))lines.add("<gray>Temps : <white>"+Text.clock(elapsed));
        else if(state.equals("countdown"))lines.add("<gray>Départ dans <yellow>"+(countdown+1)+" s");
        else if(state.equals("waiting"))lines.add("<gray>Joueurs : <yellow>"+sides.keySet().stream().filter(id->Bukkit.getPlayer(id)!=null).count()+"/"+sides.size());
        else lines.add("<gray>Partie terminée");
        lines.add("");
        for(int side=0;side<2;side++)
            lines.add((side==0?"<red>■ Rouge":"<blue>■ Bleu")+"</"+Text.sideColor(side)+"> "+(beds[side]?"<green>✔ lit":"<red>✘ lit")+" <gray>"+alive(side)+" en vie");
        lines.add("");
        if(state.equals("running")){
            lines.add(elapsed<SUDDEN_DEATH?"<gray>Mort subite : <yellow>"+Text.clock(SUDDEN_DEATH-elapsed):"<red>Mort subite active");
            lines.add("<gray>Fin : <yellow>"+Text.clock(TIME_LIMIT-elapsed));
            lines.add("");
        }
        lines.add("<gold>Rush Training");
        sidebar.update(lines);
        for(Player p:Bukkit.getOnlinePlayers()) {
            Integer side=sides.get(p.getUniqueId());if(side==null)continue;
            int[] s=stats.get(p.getUniqueId());boolean out=eliminated.contains(p.getUniqueId());
            p.playerListName(Text.mm((out?"<dark_gray><st>":"<"+Text.sideColor(side)+">")+p.getName()+(out?"</st></dark_gray>":"</"+Text.sideColor(side)+">")+" <gray>"+s[0]+"⚔"));
            p.sendPlayerListHeaderAndFooter(Text.mm("\n<gold><bold>RUSH</bold></gold> <yellow>"+mode+"</yellow> <dark_gray>•</dark_gray> <white>"+map.name+"</white>\n"),
                Text.mm("\n<gray>Kills :</gray> <yellow>"+s[0]+"</yellow> <dark_gray>|</dark_gray> <gray>Morts :</gray> <red>"+s[1]+"</red> <dark_gray>|</dark_gray> <gray>Lits :</gray> <aqua>"+s[2]
                    +"</aqua>\n<gray>Ping :</gray> <white>"+p.getPing()+" ms</white>\n"));
        }
    }

    private void equip(Player p) {
        p.getInventory().clear();p.getInventory().setArmorContents(new ItemStack[4]);
        p.getInventory().addItem(new ItemStack(Material.WOODEN_SWORD));
        p.setHealth(20);p.setFireTicks(0);p.setFallDistance(0);plugin.combat(p);
        protectedUntil.put(p.getUniqueId(),System.currentTimeMillis()+2000);
    }
    boolean canShop(Player p) {
        if(!playing(p))return false;
        int side=sides.get(p.getUniqueId());
        if(p.getLocation().distanceSquared(spawn(side))<144)return true;
        for(GameMap.Npc npc:map.npcs)if(npc.side==side&&p.getLocation().distanceSquared(new Location(plugin.world,npc.x,npc.y,npc.z))<36)return true;
        return false;
    }

    // ------------------------------------------------------------------ connections
    @EventHandler public void login(PlayerLoginEvent e) {
        if(!sides.containsKey(e.getPlayer().getUniqueId()))e.disallow(PlayerLoginEvent.Result.KICK_OTHER,Text.mm("<red>Tu n’es pas inscrit à cette partie."));
    }
    @EventHandler public void join(PlayerJoinEvent e) {
        Player p=e.getPlayer();Integer side=sides.get(p.getUniqueId());if(side==null)return;
        p.setScoreboard(board);teams[side].addEntry(p.getName());
        p.teleport(spawn(side));p.getInventory().clear();
        p.setGameMode(eliminated.contains(p.getUniqueId())?GameMode.SPECTATOR:GameMode.ADVENTURE);
        Text.title(p,"<gold><bold>Rush "+mode,"<gray>Carte <white>"+map.name+"</white> • équipe "+Text.side(side),10,50,10);
        Text.send(p,"<gray>Tu joues dans l’équipe</gray> "+Text.side(side)+"<gray>. Protège ton lit, détruis celui d’en face !</gray>");
        if(state.equals("running")&&!eliminated.contains(p.getUniqueId())){p.setGameMode(GameMode.SURVIVAL);equip(p);}
        hud();
    }
    @EventHandler public void quit(PlayerQuitEvent e){if(state.equals("running")&&sides.containsKey(e.getPlayer().getUniqueId())&&!eliminated.contains(e.getPlayer().getUniqueId())){
        broadcast(colored(e.getPlayer().getUniqueId())+" <gray>a quitté la partie</gray> <dark_red>(abandon)</dark_red>");forfeit(e.getPlayer());}}
    void forfeit(Player p) {
        if(!state.equals("running"))return;
        if(eliminated.add(p.getUniqueId())){plugin.event("player_forfeit",Map.of("player",p.getUniqueId().toString()),false);checkWin();}
    }
    @EventHandler public void move(PlayerMoveEvent e) {
        Player p=e.getPlayer();if(!sides.containsKey(p.getUniqueId()))return;
        if(state.equals("waiting")||state.equals("countdown")) {
            if(e.getTo().distanceSquared(spawn(sides.get(p.getUniqueId())))>9)e.setTo(spawn(sides.get(p.getUniqueId())));
        } else if(playing(p)&&e.getTo().getY()<map.voidY){fellInVoid.add(p.getUniqueId());p.setHealth(0);}
    }

    // ------------------------------------------------------------------ world rules
    @EventHandler public void interact(PlayerInteractEntityEvent e) {
        GameMap.Npc npc=npcEntities.get(e.getRightClicked().getUniqueId());
        if(npc==null)return;
        e.setCancelled(true);Player p=e.getPlayer();
        if(!playing(p))return;
        if(npc.side!=sides.get(p.getUniqueId())){Text.error(p,"Boutique de l’équipe adverse");return;}
        plugin.menus.category(p,npc.shop,false);
    }
    @EventHandler public void protectNpc(EntityDamageByEntityEvent e){if(npcEntities.containsKey(e.getEntity().getUniqueId()))e.setCancelled(true);}
    @EventHandler public void interact(PlayerInteractEvent e) {
        if(e.getAction()==Action.RIGHT_CLICK_BLOCK&&e.getClickedBlock()!=null&&e.getClickedBlock().getBlockData() instanceof org.bukkit.block.data.type.Bed)e.setCancelled(true);
    }
    @EventHandler public void place(BlockPlaceEvent e) {
        Block b=e.getBlock();Player p=e.getPlayer();
        boolean spawnBlocked=false;
        for(int i=0;i<2;i++){Location s=spawn(i);if(Math.abs(b.getX()-s.getBlockX())<=1&&Math.abs(b.getZ()-s.getBlockZ())<=1&&b.getY()>=s.getBlockY()-1&&b.getY()<=s.getBlockY()+2)spawnBlocked=true;}
        if(!playing(p)){e.setCancelled(true);return;}
        if(b.getY()>map.maxY||b.getY()<map.minY){e.setCancelled(true);Text.actionBar(p,"<red>Limite de construction ("+map.minY+" à "+map.maxY+")");return;}
        if(spawnBlocked){e.setCancelled(true);Text.actionBar(p,"<red>Impossible de bloquer un point de réapparition");return;}
        placed.add(key(b));
    }
    @EventHandler public void breakBlock(BlockBreakEvent e) {
        Player p=e.getPlayer();Block b=e.getBlock();
        if(!playing(p)){e.setCancelled(true);return;}
        int side=map.bedSide(b);
        if(side>=0) {
            if(side==sides.get(p.getUniqueId())||!beds[side]){e.setCancelled(true);if(side==sides.get(p.getUniqueId()))Text.actionBar(p,"<red>C’est ton propre lit !");return;}
            e.setDropItems(false);destroyBed(side);stats.get(p.getUniqueId())[2]++;
            broadcast("<"+Text.sideColor(side)+"><bold>Le lit "+(side==0?"rouge":"bleu")+" a été détruit</bold></"+Text.sideColor(side)+"> <gray>par</gray> "+colored(p.getUniqueId())+" <gray>!</gray>");
            for(Player viewer:Bukkit.getOnlinePlayers()) {
                Integer s=sides.get(viewer.getUniqueId());
                if(s!=null&&s==side){Text.title(viewer,"<red><bold>LIT DÉTRUIT","<gray>Tu ne réapparaîtras plus",5,50,10);Text.sound(viewer,Sound.ENTITY_WITHER_DEATH,1f);}
                else Text.sound(viewer,Sound.ENTITY_ENDER_DRAGON_GROWL,1.2f);
            }
            plugin.event("bed_destroyed",Map.of("side",side,"player",p.getUniqueId().toString()),false);
        } else if(!placed.remove(key(b))) e.setCancelled(true);
    }
    private void destroyBed(int side) {
        if(!beds[side])return;
        beds[side]=false;
        Block foot=map.bedFoot(plugin.world,side);
        if(foot!=null){foot.getRelative(map.facing(side)).setType(Material.AIR,false);foot.setType(Material.AIR,false);}
    }
    @EventHandler public void fire(BlockIgniteEvent e){if(e.getCause()!=BlockIgniteEvent.IgniteCause.FLINT_AND_STEEL)e.setCancelled(true);}
    @EventHandler public void burn(BlockBurnEvent e){e.setCancelled(true);}
    @EventHandler public void creature(CreatureSpawnEvent e){if(!(e.getEntity() instanceof Villager))e.setCancelled(true);}
    @EventHandler public void explode(EntityExplodeEvent e) {
        e.blockList().removeIf(b->!placed.remove(key(b)));e.setYield(0);
        if(!(e.getEntity() instanceof TNTPrimed)||!state.equals("running"))return;
        Location origin=e.getLocation();
        for(Player p:Bukkit.getOnlinePlayers())if(playing(p)&&p.getLocation().distanceSquared(origin)<36) {
            Vector delta=p.getLocation().toVector().subtract(origin.toVector());delta.setY(0);
            if(delta.lengthSquared()<0.01)delta=p.getLocation().getDirection().setY(0);
            if(delta.lengthSquared()<0.01)delta=new Vector(1,0,0);
            double factor=1-Math.min(0.8,p.getLocation().distance(origin)/7);
            Vector velocity=delta.normalize().multiply(pvp("tnt_horizontal")*factor).setY(pvp("tnt_vertical"));
            p.setVelocity(velocity);p.setFallDistance(0);
            plugin.event("tnt_launch",Map.of("player",p.getUniqueId().toString(),"velocity",List.of(velocity.getX(),velocity.getY(),velocity.getZ())),true);
        }
    }

    // ------------------------------------------------------------------ PvP
    @EventHandler(priority=EventPriority.HIGH) public void damage(EntityDamageEvent e) {
        if(!(e.getEntity() instanceof Player p))return;
        if(!playing(p)||shielded(p)||e.getCause()==EntityDamageEvent.DamageCause.FALL||e.getCause()==EntityDamageEvent.DamageCause.ENTITY_EXPLOSION||e.getCause()==EntityDamageEvent.DamageCause.BLOCK_EXPLOSION||e.getCause()==EntityDamageEvent.DamageCause.ENTITY_SWEEP_ATTACK)e.setCancelled(true);
    }
    @EventHandler(priority=EventPriority.MONITOR) public void damageLog(EntityDamageEvent e) {
        if(e.getEntity() instanceof Player p)plugin.event("damage",Map.of("player",p.getUniqueId().toString(),"cause",e.getCause().name(),"damage",e.getFinalDamage(),"cancelled",e.isCancelled()),true);
    }
    @SuppressWarnings("deprecation") // DamageModifier is the only API that replaces Paper's armor formula.
    @EventHandler(priority=EventPriority.HIGHEST,ignoreCancelled=true) public void hit(EntityDamageByEntityEvent e) {
        if(!(e.getEntity() instanceof Player victim)||!(e.getDamager() instanceof Player attacker))return;
        if(!playing(attacker)||Objects.equals(sides.get(attacker.getUniqueId()),sides.get(victim.getUniqueId()))||shielded(attacker)){e.setCancelled(true);return;}
        // Fixed legacy-like weapon damage: removes cooldown scaling and modern axe/critical bonuses.
        double damage=switch(attacker.getInventory().getItemInMainHand().getType()) {
            case WOODEN_SWORD -> 4; case STONE_SWORD -> 5; case IRON_SWORD -> 6; default -> 1;
        };
        e.setDamage(damage);
        // 1.8 armor: 4 % per armor point, independent of the hit's damage (modern formula weakens armor against strong hits).
        var armorAttribute=victim.getAttribute(Attribute.ARMOR);
        double armor=armorAttribute==null?0:Math.min(20,armorAttribute.getValue());
        if(e.isApplicable(EntityDamageEvent.DamageModifier.ARMOR))e.setDamage(EntityDamageEvent.DamageModifier.ARMOR,-damage*armor*0.04);
        lastAttacker.put(victim.getUniqueId(),attacker.getUniqueId());lastHit.put(victim.getUniqueId(),System.currentTimeMillis());
        Vector delta=victim.getLocation().toVector().subtract(attacker.getLocation().toVector()).setY(0);
        if(delta.lengthSquared()<0.001)delta=attacker.getLocation().getDirection().setY(0);
        if(delta.lengthSquared()<0.001)delta=new Vector(1,0,0);
        Vector velocity=delta.normalize().multiply(pvp("knockback_horizontal")).setY(pvp("knockback_vertical"));
        // 1.8 sprint bonus along the attacker's facing; the hit ends the sprint, so chaining it needs a sprint reset (W-tap).
        boolean sprint=attacker.isSprinting();
        if(sprint) {
            Vector facing=attacker.getLocation().getDirection().setY(0);
            if(facing.lengthSquared()>0.001)velocity.add(facing.normalize().multiply(pvp("knockback_sprint_horizontal")));
            velocity.setY(velocity.getY()+pvp("knockback_sprint_vertical"));
            attacker.setSprinting(false);
        }
        Bukkit.getScheduler().runTask(plugin,()->{if(playing(victim)&&!victim.isDead())victim.setVelocity(velocity);});
        plugin.event("pvp_hit",Map.of("attacker",attacker.getUniqueId().toString(),"victim",victim.getUniqueId().toString(),
            "damage",e.getFinalDamage(),"armor",armor,"sprint",sprint,"distance",attacker.getLocation().distance(victim.getLocation()),"attacker_ping",attacker.getPing(),"victim_ping",victim.getPing(),
            "velocity",List.of(velocity.getX(),velocity.getY(),velocity.getZ()),"hurt_ticks",victim.getMaximumNoDamageTicks()),true);
    }
    @EventHandler public void knockback(EntityKnockbackEvent e){if(e.getEntity() instanceof Player)e.setCancelled(true);}

    // ------------------------------------------------------------------ death & respawn
    @EventHandler public void death(PlayerDeathEvent e) {
        Player p=e.getEntity();UUID id=p.getUniqueId();if(!sides.containsKey(id))return;
        // Purchased gear and resources drop for the killer; the starter sword is given again on respawn.
        e.deathMessage(null);e.getDrops().removeIf(i->i.getType()==Material.WOODEN_SWORD);e.setDroppedExp(0);stats.get(id)[1]++;
        UUID killer=lastAttacker.remove(id);
        if(killer!=null&&System.currentTimeMillis()-lastHit.getOrDefault(id,0L)>=10000)killer=null;
        if(killer!=null)stats.get(killer)[0]++;
        boolean finalKill=!beds[sides.get(id)];
        if(finalKill)eliminated.add(id);
        boolean void_=fellInVoid.remove(id);
        String how=killer==null?(void_?"<gray>est tombé dans le vide.</gray>":"<gray>est mort.</gray>")
            :(void_?"<gray>a été poussé dans le vide par</gray> ":"<gray>a été tué par</gray> ")+colored(killer)+"<gray>.</gray>";
        broadcast(colored(id)+" "+how+(finalKill?" <dark_red><bold>ÉLIMINÉ</bold></dark_red>":""));
        if(killer!=null){Player k=Bukkit.getPlayer(killer);if(k!=null){Text.sound(k,finalKill?Sound.ENTITY_PLAYER_LEVELUP:Sound.ENTITY_EXPERIENCE_ORB_PICKUP,1f);Text.actionBar(k,"<green>+1 kill</green> <gray>sur</gray> "+colored(id));}}
        plugin.event("player_death",Map.of("player",id.toString(),"eliminated",finalKill),false);
        checkWin();
    }
    @EventHandler public void respawn(PlayerRespawnEvent e) {
        Player p=e.getPlayer();if(!sides.containsKey(p.getUniqueId()))return;
        e.setRespawnLocation(spawn(sides.get(p.getUniqueId())));
        Bukkit.getScheduler().runTask(plugin,()-> {
            p.setGameMode(GameMode.SPECTATOR);
            if(eliminated.contains(p.getUniqueId())){if(state.equals("running"))Text.title(p,"<red><bold>ÉLIMINÉ","<gray>Tu peux observer la fin de la partie",5,60,10);return;}
            if(!state.equals("running"))return;
            for(int s=3;s>=1;s--){int left=s;Bukkit.getScheduler().runTaskLater(plugin,()->{if(p.isOnline()&&state.equals("running"))Text.title(p,"<red><bold>Mort !","<gray>Réapparition dans <yellow>"+left+"</yellow> s",0,25,0);},(3-s)*20L);}
            Bukkit.getScheduler().runTaskLater(plugin,()-> {
                if(p.isOnline()&&!eliminated.contains(p.getUniqueId())&&state.equals("running")){p.teleport(spawn(sides.get(p.getUniqueId())));p.setGameMode(GameMode.SURVIVAL);equip(p);p.clearTitle();}
            },60);
        });
    }
    private void checkWin() {
        if(!state.equals("running"))return;
        boolean[] alive={false,false};
        sides.forEach((id,side)->{if(!eliminated.contains(id))alive[side]=true;});
        if(alive[0]&&alive[1])return;
        if(!alive[0]&&!alive[1]){abort("draw");return;}
        int winner=alive[0]?0:1;state="finished";
        result=new JsonObject();result.addProperty("match",plugin.config.get("id").getAsString());result.addProperty("winner",winner);
        JsonObject scores=new JsonObject();stats.forEach((id,s)->{JsonObject score=new JsonObject();score.addProperty("kills",s[0]);score.addProperty("deaths",s[1]);score.addProperty("beds",s[2]);scores.add(id.toString(),score);});result.add("stats",scores);
        broadcast("<gold><bold>Victoire de l’équipe</bold></gold> "+Text.side(winner)+" <gold><bold>!</bold></gold> <gray>Retour au lobby dans quelques secondes.</gray>");
        for(Player p:Bukkit.getOnlinePlayers()) {
            Integer side=sides.get(p.getUniqueId());if(side==null)continue;
            if(side==winner){Text.title(p,"<gold><bold>VICTOIRE !","<gray>Bien joué",5,70,15);Text.sound(p,Sound.UI_TOAST_CHALLENGE_COMPLETE,1f);}
            else{Text.title(p,"<red><bold>DÉFAITE","<gray>Ce sera pour la prochaine",5,70,15);Text.sound(p,Sound.ENTITY_VILLAGER_NO,0.8f);}
        }
        plugin.event("game_finished",Map.of("winner",winner,"seconds",elapsed),false);
        Bukkit.getScheduler().runTaskLater(plugin,this::submit,60);
    }
    private void abort(String reason){state="finished";abortReason=reason;
        if(reason.startsWith("draw"))for(Player p:Bukkit.getOnlinePlayers())Text.title(p,"<yellow><bold>MATCH NUL","<gray>Aucun point attribué",5,60,10);
        submit();}
    private void submit() {
        if(sending)return;sending=true;
        JsonObject payload=result;
        if(abortReason!=null){payload=new JsonObject();payload.addProperty("match",plugin.config.get("id").getAsString());payload.addProperty("reason",abortReason);}
        plugin.api.post(abortReason==null?"/result":"/abort",payload,r->plugin.event("result_acknowledged",Map.of(),false),error->{sending=false;plugin.getLogger().warning("Résultat en attente : "+error);});
    }
    /** Chat prefix for a participant (team color, gray when eliminated). */
    String chatName(Player p) {
        Integer side=sides.get(p.getUniqueId());
        if(side==null)return "<gray>"+p.getName();
        return eliminated.contains(p.getUniqueId())?"<dark_gray>[Éliminé] <gray>"+p.getName():(side==0?"<red>[Rouge] ":"<blue>[Bleu] ")+p.getName();
    }
}
