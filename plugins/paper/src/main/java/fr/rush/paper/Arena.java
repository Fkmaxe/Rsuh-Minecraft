package fr.rush.paper;

import com.google.gson.*;
import org.bukkit.*;
import org.bukkit.attribute.Attribute;
import org.bukkit.block.Block;
import org.bukkit.block.BlockFace;
import org.bukkit.block.data.type.Bed;
import org.bukkit.entity.*;
import org.bukkit.event.*;
import org.bukkit.event.block.*;
import org.bukkit.event.entity.*;
import org.bukkit.event.player.*;
import org.bukkit.inventory.ItemStack;
import org.bukkit.util.Vector;
import java.util.*;

final class Arena implements Listener {
    private final RushPlugin plugin;
    private final Map<UUID,Integer> sides=new HashMap<>();
    private final Map<UUID,int[]> stats=new HashMap<>();
    private final Set<UUID> eliminated=new HashSet<>();
    private final Set<String> placed=new HashSet<>();
    private final Map<UUID,UUID> lastAttacker=new HashMap<>();
    private final Map<UUID,Long> lastHit=new HashMap<>();
    private final Map<UUID,Long> protectedUntil=new HashMap<>();
    private final boolean[] beds={true,true};
    private String state="waiting";
    private int seconds, countdown=5, elapsed;
    private boolean sending;
    private JsonObject result;
    private String abortReason;

    Arena(RushPlugin plugin) {
        this.plugin=plugin;
        JsonArray teams=plugin.config.getAsJsonObject("match").getAsJsonArray("teams");
        for(int side=0;side<2;side++)for(JsonElement e:teams.get(side).getAsJsonArray()) {
            UUID id=UUID.fromString(e.getAsString());sides.put(id,side);stats.put(id,new int[3]);
        }
    }
    private int base(int side){return side==0?-32:32;}
    private Location spawn(int side){return new Location(plugin.world,base(side)+0.5,65,3.5,side==0?-90:90,0);}
    private String key(Block b){return b.getX()+":"+b.getY()+":"+b.getZ();}
    private double pvp(String key){return plugin.config.getAsJsonObject("pvp").get(key).getAsDouble();}
    private boolean playing(Player p){return state.equals("running")&&sides.containsKey(p.getUniqueId())&&!eliminated.contains(p.getUniqueId())&&p.getGameMode()==GameMode.SURVIVAL;}
    private boolean shielded(Player p){return System.currentTimeMillis()<protectedUntil.getOrDefault(p.getUniqueId(),0L);}

    void build() {
        for(int side=0;side<2;side++) {
            int x=base(side);
            for(int dx=-7;dx<=7;dx++)for(int z=-7;z<=7;z++) {
                plugin.world.getBlockAt(x+dx,63,z).setType(Material.BEDROCK,false);
                plugin.world.getBlockAt(x+dx,64,z).setType(side==0?Material.RED_TERRACOTTA:Material.BLUE_TERRACOTTA,false);
            }
            for(int part=0;part<2;part++) {
                Block b=plugin.world.getBlockAt(x,65,-3-part);
                Bed data=(Bed)Bukkit.createBlockData(side==0?Material.RED_BED:Material.BLUE_BED);
                data.setFacing(BlockFace.NORTH);data.setPart(part==0?Bed.Part.FOOT:Bed.Part.HEAD);b.setBlockData(data,false);
            }
            for(int i=0;i<4;i++) {
                Villager villager=plugin.world.spawn(new Location(plugin.world,x-5+i*3,65,6),Villager.class);
                villager.setAI(false);villager.setInvulnerable(true);villager.setSilent(true);villager.setCollidable(false);
                villager.setCustomName(new String[]{"§eBlocs","§eArmes","§eArmures","§eÉquipement"}[i]);villager.setCustomNameVisible(true);
            }
        }
        for(int x=-4;x<=4;x++)for(int z=-4;z<=4;z++)plugin.world.getBlockAt(x,64,z).setType(Material.SANDSTONE,false);
        plugin.world.setSpawnLocation(-32,65,3);
        Bukkit.getScheduler().runTaskTimer(plugin,this::tick,20,20);
        plugin.event("arena_created",Map.of("players",sides.size(),"pvp",plugin.config.getAsJsonObject("pvp")),false);
    }

    private void tick() {
        seconds++;
        if(state.equals("waiting")||state.equals("countdown")) {
            boolean full=sides.keySet().stream().allMatch(id->Bukkit.getPlayer(id)!=null);
            if(full) {
                state="countdown";
                if(countdown--<=0) {
                    state="running";
                    for(UUID id:sides.keySet()) {Player p=Bukkit.getPlayer(id);p.setGameMode(GameMode.SURVIVAL);equip(p);}
                    Bukkit.broadcastMessage("§aLa partie commence ! Détruisez le lit adverse. /shop à votre base.");
                    plugin.event("game_started",Map.of(),false);
                } else Bukkit.broadcastMessage("§eDépart dans "+(countdown+1)+"…");
            } else {state="waiting";countdown=5;}
            if(seconds>120)abort("roster_join_timeout");
        } else if(state.equals("running")) {
            elapsed++;
            for(int side=0;side<2;side++) {
                resource(new Location(plugin.world,base(side)+0.5,65.5,0.5),Material.BRICK);
                if(elapsed%5==0)resource(new Location(plugin.world,base(side)+2.5,65.5,0.5),Material.IRON_INGOT);
            }
            if(elapsed%10==0)resource(new Location(plugin.world,0.5,65.5,0.5),Material.GOLD_INGOT);
            if(elapsed==1200){destroyBed(0);destroyBed(1);Bukkit.broadcastMessage("§cMort subite : les lits sont détruits !");}
            if(elapsed>=1500)abort("draw_time_limit");
            if(elapsed%30==0)plugin.event("performance",Map.of("tps",Bukkit.getTPS()[0],"players",Bukkit.getOnlinePlayers().size()),true);
        } else if(state.equals("finished")&&seconds%5==0&&!sending)submit();
    }
    private void resource(Location loc,Material type) {
        int count=plugin.world.getNearbyEntities(loc,2,2,2).stream().filter(e->e instanceof Item i&&i.getItemStack().getType()==type).mapToInt(e->((Item)e).getItemStack().getAmount()).sum();
        if(count<64){Item drop=plugin.world.dropItem(loc,new ItemStack(type));drop.setVelocity(new Vector());}
    }
    private void equip(Player p) {
        p.getInventory().clear();p.getInventory().setArmorContents(new ItemStack[4]);
        p.getInventory().addItem(new ItemStack(Material.WOODEN_SWORD));
        p.setHealth(20);p.setFireTicks(0);p.setFallDistance(0);plugin.combat(p);
        protectedUntil.put(p.getUniqueId(),System.currentTimeMillis()+2000);
    }
    boolean canShop(Player p){return playing(p)&&p.getLocation().distanceSquared(spawn(sides.get(p.getUniqueId())))<144;}

    @EventHandler public void login(PlayerLoginEvent e) {
        if(!sides.containsKey(e.getPlayer().getUniqueId()))e.disallow(PlayerLoginEvent.Result.KICK_OTHER,"Vous n’êtes pas inscrit à cette partie.");
    }
    @EventHandler public void join(PlayerJoinEvent e) {
        Player p=e.getPlayer();Integer side=sides.get(p.getUniqueId());if(side==null)return;
        p.teleport(spawn(side));p.getInventory().clear();
        p.setGameMode(eliminated.contains(p.getUniqueId())?GameMode.SPECTATOR:GameMode.ADVENTURE);
        p.sendMessage(side==0?"§cÉquipe rouge":"§9Équipe bleue");
        if(state.equals("running")&&!eliminated.contains(p.getUniqueId())){p.setGameMode(GameMode.SURVIVAL);equip(p);}
    }
    @EventHandler public void quit(PlayerQuitEvent e){if(state.equals("running"))forfeit(e.getPlayer());}
    void forfeit(Player p) {
        if(!state.equals("running"))return;
        if(eliminated.add(p.getUniqueId())){plugin.event("player_forfeit",Map.of("player",p.getUniqueId().toString()),false);checkWin();}
    }
    @EventHandler public void move(PlayerMoveEvent e) {
        if(!sides.containsKey(e.getPlayer().getUniqueId()))return;
        if(state.equals("waiting")||state.equals("countdown")) {
            if(e.getTo().distanceSquared(spawn(sides.get(e.getPlayer().getUniqueId())))>9)e.setTo(spawn(sides.get(e.getPlayer().getUniqueId())));
        } else if(playing(e.getPlayer())&&e.getTo().getY()<40)e.getPlayer().setHealth(0);
    }
    @EventHandler public void interact(PlayerInteractEntityEvent e) {
        if(e.getRightClicked() instanceof Villager){e.setCancelled(true);plugin.menus.shop(e.getPlayer());}
    }
    @EventHandler public void interact(PlayerInteractEvent e) {
        if(e.getAction()==Action.RIGHT_CLICK_BLOCK&&e.getClickedBlock()!=null&&e.getClickedBlock().getBlockData() instanceof Bed)e.setCancelled(true);
    }
    @EventHandler public void place(BlockPlaceEvent e) {
        Block b=e.getBlock();
        boolean spawnBlocked=false;
        for(int i=0;i<2;i++)if(Math.abs(b.getX()-base(i))<=1&&b.getZ()>=2&&b.getZ()<=4&&b.getY()<=67)spawnBlocked=true;
        if(!playing(e.getPlayer())||b.getY()>110||b.getY()<60||spawnBlocked){e.setCancelled(true);return;}
        placed.add(key(b));
    }
    @EventHandler public void breakBlock(BlockBreakEvent e) {
        Player p=e.getPlayer();Block b=e.getBlock();
        if(!playing(p)){e.setCancelled(true);return;}
        if(b.getBlockData() instanceof Bed) {
            int side=b.getX()<0?0:1;
            if(side==sides.get(p.getUniqueId())||!beds[side]){e.setCancelled(true);return;}
            e.setDropItems(false);destroyBed(side);stats.get(p.getUniqueId())[2]++;
            Bukkit.broadcastMessage("§cLe lit "+(side==0?"rouge":"bleu")+" a été détruit !");
            plugin.event("bed_destroyed",Map.of("side",side,"player",p.getUniqueId().toString()),false);
        } else if(!placed.remove(key(b))) e.setCancelled(true);
    }
    private void destroyBed(int side) {
        beds[side]=false;
        for(int z=-4;z<=-3;z++)plugin.world.getBlockAt(base(side),65,z).setType(Material.AIR,false);
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
    @EventHandler public void death(PlayerDeathEvent e) {
        Player p=e.getEntity();if(!sides.containsKey(p.getUniqueId()))return;
        // Purchased gear and resources drop for the killer; the starter sword is given again on respawn.
        e.setDeathMessage(null);e.getDrops().removeIf(i->i.getType()==Material.WOODEN_SWORD);e.setDroppedExp(0);stats.get(p.getUniqueId())[1]++;
        UUID killer=lastAttacker.remove(p.getUniqueId());
        if(killer!=null&&System.currentTimeMillis()-lastHit.getOrDefault(p.getUniqueId(),0L)<10000)stats.get(killer)[0]++;
        if(!beds[sides.get(p.getUniqueId())])eliminated.add(p.getUniqueId());
        plugin.event("player_death",Map.of("player",p.getUniqueId().toString(),"eliminated",eliminated.contains(p.getUniqueId())),false);
        checkWin();
    }
    @EventHandler public void respawn(PlayerRespawnEvent e) {
        Player p=e.getPlayer();if(!sides.containsKey(p.getUniqueId()))return;
        e.setRespawnLocation(spawn(sides.get(p.getUniqueId())));
        Bukkit.getScheduler().runTask(plugin,()-> {
            p.setGameMode(GameMode.SPECTATOR);
            if(!eliminated.contains(p.getUniqueId())&&state.equals("running"))Bukkit.getScheduler().runTaskLater(plugin,()-> {
                if(p.isOnline()&&!eliminated.contains(p.getUniqueId())&&state.equals("running")){p.teleport(spawn(sides.get(p.getUniqueId())));p.setGameMode(GameMode.SURVIVAL);equip(p);}
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
        Bukkit.broadcastMessage("§6Victoire de l’équipe "+(winner==0?"rouge":"bleue")+" !");
        plugin.event("game_finished",Map.of("winner",winner,"seconds",elapsed),false);
        Bukkit.getScheduler().runTaskLater(plugin,this::submit,60);
    }
    private void abort(String reason){state="finished";abortReason=reason;submit();}
    private void submit() {
        if(sending)return;sending=true;
        JsonObject payload=result;
        if(abortReason!=null){payload=new JsonObject();payload.addProperty("match",plugin.config.get("id").getAsString());payload.addProperty("reason",abortReason);}
        plugin.api.post(abortReason==null?"/result":"/abort",payload,r->plugin.event("result_acknowledged",Map.of(),false),error->{sending=false;plugin.getLogger().warning("Résultat en attente : "+error);});
    }
}
