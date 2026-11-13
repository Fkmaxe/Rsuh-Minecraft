package fr.rush.paper;

import com.google.gson.*;
import org.bukkit.*;
import org.bukkit.entity.Player;
import org.bukkit.event.*;
import org.bukkit.event.inventory.*;
import org.bukkit.inventory.*;
import org.bukkit.inventory.meta.ItemMeta;
import org.bukkit.inventory.meta.SkullMeta;
import java.util.*;
import java.util.function.Consumer;

/** All inventory interfaces of the network (lobby menus, shop, map administration). */
final class Menus implements Listener {
    private final RushPlugin plugin;
    record Offer(String category,Material item,int count,Material currency,int price,String label) {}
    static final List<Offer> OFFERS=List.of(
        new Offer("blocs",Material.SANDSTONE,16,Material.BRICK,4,"16 blocs de grès"),
        new Offer("armes",Material.STONE_SWORD,1,Material.BRICK,10,"Épée en pierre"),
        new Offer("armes",Material.IRON_SWORD,1,Material.IRON_INGOT,8,"Épée en fer"),
        new Offer("armures",Material.LEATHER_CHESTPLATE,1,Material.BRICK,10,"Plastron en cuir"),
        new Offer("armures",Material.IRON_CHESTPLATE,1,Material.IRON_INGOT,12,"Plastron en fer"),
        new Offer("equipement",Material.WOODEN_PICKAXE,1,Material.BRICK,8,"Pioche en bois"),
        new Offer("equipement",Material.TNT,1,Material.IRON_INGOT,6,"TNT (à allumer au briquet)"),
        new Offer("equipement",Material.FLINT_AND_STEEL,1,Material.BRICK,8,"Briquet"),
        new Offer("equipement",Material.GOLDEN_APPLE,1,Material.GOLD_INGOT,4,"Pomme dorée"));

    /** Inventory with click actions; every interface of the plugin is one of these. */
    static final class Gui implements InventoryHolder {
        final Inventory inventory;
        final Map<Integer,Consumer<ClickType>> actions=new HashMap<>();
        Gui(int rows,String title){inventory=Bukkit.createInventory(this,rows*9,Text.mm(title));}
        public Inventory getInventory(){return inventory;}
        Gui set(int slot,ItemStack item,Consumer<ClickType> action){inventory.setItem(slot,item);if(action!=null)actions.put(slot,action);else actions.remove(slot);return this;}
        Gui set(int slot,ItemStack item,Runnable action){return set(slot,item,action==null?null:click->action.run());}
        /** Glass frame on the border; keeps content readable and the layout recognizable. */
        Gui frame(){
            int size=inventory.getSize();
            ItemStack pane=icon(Material.GRAY_STAINED_GLASS_PANE," ");
            for(int i=0;i<size;i++)if(i<9||i>=size-9||i%9==0||i%9==8)if(inventory.getItem(i)==null)inventory.setItem(i,pane);
            return this;
        }
        void open(Player p){p.openInventory(inventory);}
    }

    Menus(RushPlugin plugin){this.plugin=plugin;}

    static ItemStack icon(Material type,String name,String... lore) {
        ItemStack stack=new ItemStack(type);ItemMeta meta=stack.getItemMeta();
        meta.displayName(Text.item(name));
        List<net.kyori.adventure.text.Component> lines=new ArrayList<>();for(String l:lore)lines.add(Text.item(l));meta.lore(lines);
        meta.addItemFlags(ItemFlag.values());stack.setItemMeta(meta);return stack;
    }
    static ItemStack glow(ItemStack stack){ItemMeta meta=stack.getItemMeta();meta.setEnchantmentGlintOverride(true);stack.setItemMeta(meta);return stack;}
    static ItemStack count(ItemStack stack,int amount){stack.setAmount(Math.max(1,Math.min(64,amount)));return stack;}
    /** Head with the player's skin when they are on this server (no remote profile lookup). */
    static ItemStack head(String name,String title,String... lore) {
        ItemStack stack=icon(Material.PLAYER_HEAD,title,lore);
        Player online=Bukkit.getPlayerExact(name);
        if(online!=null){SkullMeta meta=(SkullMeta)stack.getItemMeta();meta.setOwningPlayer(online);stack.setItemMeta(meta);}
        return stack;
    }
    static ItemStack back(){return icon(Material.ARROW,"<gray>← Retour");}

    /** Runs a command through the API and shows its formatted answer in chat. */
    void command(Player p,String action,String... args) {
        plugin.api.command(p,action,args,r->{
            Text.ok(p,r.get("message").getAsString());
            if(r.has("lines"))for(JsonElement line:r.getAsJsonArray("lines"))p.sendMessage(Text.mm("  "+line.getAsString()));
        });
    }
    private void suggest(Player p,String command,String hint) {
        p.closeInventory();
        Text.send(p,"<click:suggest_command:'"+command+"'><hover:show_text:\"<gray>Préremplir la commande\"><yellow><u>"+hint+"</u></yellow></hover></click> <dark_gray>("+command+"…)</dark_gray>");
        Text.click(p);
    }

    // ------------------------------------------------------------------ main menu
    void main(Player p) {
        JsonObject pr=plugin.hud.profile(p);JsonObject net=plugin.hud.network();
        String queued=pr!=null&&pr.has("queue")&&!pr.get("queue").isJsonNull()?pr.getAsJsonObject("queue").get("mode").getAsString():null;
        Gui g=new Gui(6,"<dark_gray>Rush</dark_gray> <dark_gray>•</dark_gray> <gold><bold>Menu</bold></gold>");
        g.set(4,head(p.getName(),"<gold>"+p.getName(),profileLore(pr)),()->{});
        String[] modes={"1v1","2v2","5v5"};Material[] icons={Material.WOODEN_SWORD,Material.STONE_SWORD,Material.IRON_SWORD};int[] sizes={1,2,5};
        for(int i=0;i<3;i++) {
            String mode=modes[i];
            int waiting=net.has("queue")?net.getAsJsonObject("queue").get(mode).getAsInt():0;
            boolean in=mode.equals(queued);
            ItemStack item=count(icon(icons[i],"<yellow><bold>"+mode+"</bold></yellow> <gray>— Rush classé",
                "<gray>"+sizes[i]+" joueur(s) par équipe","<gray>En file : <yellow>"+waiting+"</yellow>/"+sizes[i]*2,"",
                in?"<red>■ Clic : quitter la file":"<green>▶ Clic : rejoindre la file"),Math.max(1,waiting));
            g.set(20+i*2,in?glow(item):item,()->{p.closeInventory();if(in)command(p,"leave");else command(p,"queue",mode);});
        }
        g.set(38,icon(Material.WHITE_BANNER,"<aqua>Mon équipe","<gray>Joue en groupe (5 max).",pr!=null&&pr.getAsJsonArray("party").size()>0?"<gray>Membres : <white>"+pr.getAsJsonArray("party").size():"<dark_gray>Aucune équipe","","<yellow>▶ Clic : gérer"),()->team(p));
        g.set(39,icon(Material.EMERALD,"<green>Mes amis","<gray>Demandes, présence, invitations.","","<yellow>▶ Clic : ouvrir"),()->friends(p));
        g.set(40,icon(Material.GOLD_INGOT,"<gold>Classements","<gray>Top 10 par Elo.","","<yellow>▶ Clic : ouvrir"),()->rankings(p,"global"));
        g.set(41,icon(Material.ENDER_EYE,"<light_purple>Parties privées","<gray>Entre amis, non classées.","","<yellow>▶ Clic : ouvrir"),()->privateMenu(p));
        g.set(42,icon(Material.BARRIER,"<red>Quitter la file / le salon","<gray>Annule ta recherche en cours."),()->{p.closeInventory();command(p,"leave");});
        if(pr!=null&&"admin".equals(pr.get("role").getAsString()))
            g.set(45,icon(Material.FILLED_MAP,"<red><bold>Maps</bold></red> <gray>(admin)","<gray>Créer, éditer, activer les maps.","","<yellow>▶ Clic : ouvrir"),()->maps(p));
        g.set(49,icon(Material.OAK_DOOR,"<gray>Fermer"),()->p.closeInventory());
        g.frame().open(p);
    }
    static String[] profileLore(JsonObject pr) {
        if(pr==null)return new String[]{"<gray>Chargement du profil…"};
        int w=pr.get("wins").getAsInt(),l=pr.get("losses").getAsInt(),k=pr.get("kills").getAsInt(),d=pr.get("deaths").getAsInt();
        String rank=pr.get("rank").isJsonNull()?"non classé":"#"+pr.get("rank").getAsInt();
        return new String[]{"<gray>Rôle : "+Hud.roleName(pr.get("role").getAsString()),
            "<gray>Elo : <gold>"+pr.get("rating").getAsInt()+"</gold> <dark_gray>("+rank+")",
            "<gray>Victoires : <green>"+w+"</green> <dark_gray>|</dark_gray> <gray>Défaites : <red>"+l,
            "<gray>K/D : <yellow>"+String.format(Locale.ROOT,"%.2f",d==0?k:(double)k/d)+"</yellow> <dark_gray>|</dark_gray> <gray>Lits : <aqua>"+pr.get("beds").getAsInt()};
    }

    // ------------------------------------------------------------------ team
    void team(Player p) {
        plugin.api.command(p,"team",new String[]{"list"},r->{
            Gui g=new Gui(6,"<dark_gray>Rush •</dark_gray> <aqua><bold>Équipe</bold></aqua>");
            boolean leader=r.get("leader").getAsBoolean();JsonArray members=r.getAsJsonArray("members");
            g.set(4,icon(Material.WHITE_BANNER,members.isEmpty()?"<gray>Aucune équipe":r.get("message").getAsString(),
                "<gray>Le chef inscrit toute l’équipe en file.","<gray>Invite jusqu’à 4 joueurs."),()->{});
            int slot=11;
            for(JsonElement e:members) {
                JsonObject m=e.getAsJsonObject();String name=m.get("name").getAsString();boolean self=name.equals(p.getName());
                List<String> lore=new ArrayList<>(List.of(m.get("owner").getAsBoolean()?"<gold>♛ Chef":"<gray>Membre",m.get("online").getAsBoolean()?"<green>● En ligne":"<red>● Hors ligne"));
                if(leader&&!self){lore.add("");lore.add("<red>Clic droit : retirer");}
                g.set(slot++,head(name,(m.get("owner").getAsBoolean()?"<gold>":"<white>")+name,lore.toArray(String[]::new)),
                    click->{if(leader&&!self&&click.isRightClick()){command(p,"team","kick",name);reopen(p,()->team(p));}});
            }
            slot=29;
            for(JsonElement e:r.getAsJsonArray("invites")) {
                if(slot>33)break;String name=e.getAsString();
                g.set(slot++,glow(head(name,"<yellow>Invitation de "+name,"<gray>Rejoindre son équipe ?","","<green>Clic gauche : accepter","<red>Clic droit : refuser")),
                    click->{command(p,"team",click.isRightClick()?"deny":"accept",name);reopen(p,()->team(p));});
            }
            if(leader||members.isEmpty())g.set(48,icon(Material.EMERALD,"<green>Inviter un joueur","<gray>Parmi les joueurs connectés."),()->players(p,"team"));
            g.set(49,back(),()->main(p));
            if(!members.isEmpty())g.set(50,icon(Material.BARRIER,leader?"<red>Dissoudre l’équipe":"<red>Quitter l’équipe"),()->{command(p,"team","leave");reopen(p,()->team(p));});
            g.frame().open(p);
        });
    }
    /** Re-open a menu after the API had time to apply the click. */
    private void reopen(Player p,Runnable menu){Bukkit.getScheduler().runTaskLater(plugin,()->{if(p.isOnline()&&p.getOpenInventory().getTopInventory().getHolder() instanceof Gui)menu.run();},6);}

    // ------------------------------------------------------------------ friends
    void friends(Player p) {
        plugin.api.command(p,"friend",new String[]{"list"},r->{
            Gui g=new Gui(6,"<dark_gray>Rush •</dark_gray> <green><bold>Amis</bold></green>");
            g.set(4,icon(Material.EMERALD,r.get("message").getAsString(),"<gray>Clic gauche sur un ami en ligne :","<gray>l’inviter dans ton équipe."),()->{});
            int[] slots={10,11,12,13,14,15,16,19,20,21,22,23,24,25,28,29,30,31,32,33,34};int i=0;
            JsonArray friends=r.getAsJsonArray("friends");
            for(JsonElement e:friends) {
                if(i>=slots.length)break;
                JsonObject f=e.getAsJsonObject();String name=f.get("name").getAsString();String status=f.get("status").getAsString();
                ItemStack item=switch(status){
                    case "online"->head(name,"<green>"+name,"<green>● En ligne","","<yellow>Clic gauche : inviter dans l’équipe","<red>Clic droit : retirer");
                    case "offline"->icon(Material.SKELETON_SKULL,"<gray>"+name,"<red>● Hors ligne","","<red>Clic droit : retirer");
                    case "incoming"->glow(head(name,"<aqua>"+name,"<aqua>Demande reçue","","<green>Clic gauche : accepter","<red>Clic droit : refuser"));
                    default->icon(Material.PAPER,"<gray>"+name,"<dark_gray>Demande envoyée","","<red>Clic droit : annuler");
                };
                g.set(slots[i++],item,click->{
                    if(status.equals("incoming"))command(p,"friend",click.isRightClick()?"deny":"accept",name);
                    else if(click.isRightClick())command(p,"friend","remove",name);
                    else if(status.equals("online"))command(p,"team","invite",name);
                    else return;
                    reopen(p,()->friends(p));
                });
            }
            if(friends.size()>slots.length)g.set(43,icon(Material.BOOK,"<gray>… et "+(friends.size()-slots.length)+" autres","<gray>/friend list pour tout voir"),()->{});
            g.set(48,icon(Material.LIME_DYE,"<green>Ajouter un ami","<gray>Parmi les joueurs connectés."),()->players(p,"friend"));
            g.set(49,back(),()->main(p));
            g.set(50,icon(Material.NAME_TAG,"<yellow>Ajouter par pseudo","<gray>Joueur absent ou sur un autre serveur."),()->suggest(p,"/friend add ","Clique ici puis saisis le pseudo"));
            g.frame().open(p);
        });
    }

    // ------------------------------------------------------------------ rankings
    void rankings(Player p,String mode) {
        plugin.api.command(p,"leaderboard",new String[]{mode},r->{
            Gui g=new Gui(6,"<dark_gray>Rush •</dark_gray> <gold><bold>Classement "+mode+"</bold></gold>");
            int[] slots={13,21,22,23,29,30,31,32,33,40};int i=0;
            for(JsonElement e:r.getAsJsonArray("rows")) {
                JsonObject row=e.getAsJsonObject();String name=row.get("name").getAsString();
                int w=row.get("wins").getAsInt(),l=row.get("losses").getAsInt();
                String place=i==0?"<gold><bold>#1</bold></gold>":i==1?"<white><bold>#2</bold></white>":i==2?"<#cd7f32><bold>#3</bold></#cd7f32>":"<gray>#"+(i+1);
                g.set(slots[i++],count(head(name,place+" <white>"+name,"<gray>Elo : <gold>"+row.get("rating").getAsInt(),
                    "<gray>V/D : <green>"+w+"</green>/<red>"+l+"</red> <dark_gray>("+(w+l==0?0:Math.round(100.0*w/(w+l)))+" %)",
                    "<gray>Kills : <yellow>"+row.get("kills").getAsInt()+"</yellow> <dark_gray>|</dark_gray> <gray>Lits : <aqua>"+row.get("beds").getAsInt()),i),()->{});
            }
            if(i==0)g.set(22,icon(Material.STRUCTURE_VOID,"<gray>Aucune partie classée","<gray>Sois le premier !"),()->{});
            String[] tabs={"global","1v1","2v2","5v5"};int[] tabSlots={46,47,51,52};
            for(int t=0;t<4;t++){String tab=tabs[t];ItemStack item=icon(tab.equals("global")?Material.NETHER_STAR:Material.PAPER,(tab.equals(mode)?"<gold><bold>":"<yellow>")+tab,tab.equals(mode)?"<green>● Affiché":"<gray>Clic : afficher");
                g.set(tabSlots[t],tab.equals(mode)?glow(item):item,()->rankings(p,tab));}
            g.set(49,back(),()->main(p));
            g.frame().open(p);
        });
    }

    // ------------------------------------------------------------------ private rooms
    void privateMenu(Player p) {
        plugin.api.query(p,"private",new String[]{"list"},r->room(p,r.getAsJsonObject("room")),missing->{
            Gui g=new Gui(5,"<dark_gray>Rush •</dark_gray> <light_purple><bold>Parties privées</bold></light_purple>");
            String[] modes={"1v1","2v2","5v5"};
            for(int i=0;i<3;i++){String mode=modes[i];
                g.set(11+i*2,icon(Material.ENDER_EYE,"<light_purple>Créer un salon "+mode,"<gray>Non classé, tu choisis les joueurs.","","<yellow>▶ Clic : créer"),()->{command(p,"private","create",mode);reopen(p,()->privateMenu(p));});}
            g.set(31,icon(Material.TRIPWIRE_HOOK,"<aqua>Rejoindre avec un code","<gray>Code donné par le créateur."),()->suggest(p,"/private join ","Clique ici puis saisis le code"));
            g.set(40,back(),()->main(p));
            g.frame().open(p);
        });
    }
    private void room(Player p,JsonObject room) {
        String code=room.get("code").getAsString();int size=room.get("size").getAsInt();JsonArray players=room.getAsJsonArray("players");
        boolean owner=room.get("owner").getAsString().equals(p.getName());boolean full=players.size()==size;
        Gui g=new Gui(6,"<dark_gray>Salon</dark_gray> <light_purple><bold>"+code+"</bold></light_purple> <dark_gray>• "+room.get("mode").getAsString()+"</dark_gray>");
        g.set(4,icon(Material.NAME_TAG,"<light_purple>Salon "+code,"<gray>Mode : <white>"+room.get("mode").getAsString(),"<gray>Joueurs : <white>"+players.size()+"/"+size,
            "<gray>Créateur : <yellow>"+room.get("owner").getAsString(),"","<gray>Ordre d’arrivée : rouge, bleu, rouge…"),()->{});
        for(int i=0;i<size;i++) {
            int slot=(i%2==0?19:23)+(i/2)%3+(i/6)*9; // Red on the left, blue on the right.
            if(i<players.size()){String name=players.get(i).getAsString();g.set(slot,head(name,(i%2==0?"<red>":"<blue>")+name,i%2==0?"<red>Équipe rouge":"<blue>Équipe bleue"),()->{});}
            else g.set(slot,icon(i%2==0?Material.RED_STAINED_GLASS:Material.BLUE_STAINED_GLASS,"<dark_gray>Place libre"),()->{});
        }
        g.set(48,icon(Material.EMERALD,"<green>Inviter un ami","<gray>Envoie un bouton « Rejoindre »."),()->players(p,"private"));
        g.set(49,back(),()->main(p));
        if(owner)g.set(50,full?glow(icon(Material.LIME_DYE,"<green><bold>Démarrer la partie","<gray>Partie non classée.")):icon(Material.GRAY_DYE,"<gray>En attente de joueurs","<gray>"+players.size()+"/"+size),
            ()->{if(full){p.closeInventory();command(p,"private","start");}});
        g.set(53,icon(Material.BARRIER,owner?"<red>Fermer le salon":"<red>Quitter le salon"),()->{p.closeInventory();command(p,"leave");});
        g.frame().open(p);
    }

    // ------------------------------------------------------------------ player picker
    void players(Player p,String kind) {
        plugin.api.command(p,"players",new String[]{},r->{
            String verb=switch(kind){case "team"->"Inviter dans l’équipe";case "friend"->"Demander en ami";default->"Inviter dans le salon";};
            Gui g=new Gui(6,"<dark_gray>Rush •</dark_gray> <yellow><bold>"+verb+"</bold></yellow>");int i=0;
            for(JsonElement value:r.getAsJsonArray("players")) {
                if(i>=45)break;String name=value.getAsString();
                g.set(i++,head(name,"<white>"+name,"","<yellow>▶ Clic : "+verb.toLowerCase(Locale.ROOT)),()->{
                    switch(kind){case "team"->command(p,"team","invite",name);case "friend"->command(p,"friend","add",name);default->command(p,"private","invite",name);}
                    p.closeInventory();});
            }
            if(i==0)g.set(22,icon(Material.STRUCTURE_VOID,"<gray>Personne d’autre n’est connecté"),()->{});
            g.set(49,back(),()->{switch(kind){case "team"->team(p);case "friend"->friends(p);default->privateMenu(p);}});
            String cmd=switch(kind){case "team"->"/team invite ";case "friend"->"/friend add ";default->"/private invite ";};
            g.set(53,icon(Material.NAME_TAG,"<yellow>Par pseudo"),()->suggest(p,cmd,"Clique ici puis saisis le pseudo"));
            g.open(p);
        });
    }

    // ------------------------------------------------------------------ shop
    void shop(Player p) {
        if(plugin.arena==null||!plugin.arena.canShop(p)){Text.error(p,"Boutique disponible près de ta base pendant la partie.");return;}
        Gui g=new Gui(3,"<dark_gray>Rush •</dark_gray> <gold><bold>Boutique</bold></gold>");
        Material[] icons={Material.SANDSTONE,Material.IRON_SWORD,Material.IRON_CHESTPLATE,Material.TNT};
        for(int i=0;i<4;i++){String shop=GameMap.SHOPS.get(i);
            g.set(10+i*2,icon(icons[i],"<yellow><bold>"+GameMap.shopLabel(shop),"<gray>"+OFFERS.stream().filter(o->o.category.equals(shop)).count()+" article(s)","","<yellow>▶ Clic : ouvrir"),()->category(p,shop,true));}
        g.set(22,wallet(p),()->{});
        g.frame().open(p);
    }
    void category(Player p,String shop,boolean fromHub) {
        if(plugin.arena==null||!plugin.arena.canShop(p))return;
        Gui g=new Gui(4,"<dark_gray>Boutique •</dark_gray> <gold><bold>"+GameMap.shopLabel(shop)+"</bold></gold>");
        int slot=10;
        for(Offer o:OFFERS) {
            if(!o.category.equals(shop))continue;
            int have=amount(p,o.currency);boolean can=have>=o.price;
            g.set(slot++,count(icon(o.item,(can?"<green>":"<red>")+o.label,"<gray>Prix : <gold>"+o.price+" "+currency(o.currency),"",
                can?"<green>✔ Clic : acheter":"<red>✘ Il te manque "+(o.price-have)+" "+currency(o.currency),can&&o.category.equals("blocs")?"<gray>Maj + clic : acheter ×4":""),o.count),
                click->{int times=click.isShiftClick()&&o.category.equals("blocs")?4:1;for(int t=0;t<times;t++)if(!buy(p,o,t==0))break;category(p,shop,fromHub);});
        }
        g.set(31,fromHub?back():icon(Material.OAK_DOOR,"<gray>Fermer"),()->{if(fromHub)shop(p);else p.closeInventory();});
        g.set(27,wallet(p),()->{});
        g.frame().open(p);
    }
    private static ItemStack wallet(Player p) {
        return icon(Material.CHEST,"<gold>Tes ressources","<gray>Bronze : <gold>"+amount(p,Material.BRICK),"<gray>Fer : <white>"+amount(p,Material.IRON_INGOT),"<gray>Or : <yellow>"+amount(p,Material.GOLD_INGOT));
    }
    private static int amount(Player p,Material type){int n=0;for(ItemStack s:p.getInventory().getContents())if(s!=null&&s.getType()==type&&!s.hasItemMeta())n+=s.getAmount();return n;}
    static String currency(Material m){return m==Material.BRICK?"Bronze":m==Material.IRON_INGOT?"Fer":"Or";}
    private boolean buy(Player p,Offer o,boolean feedback) {
        if(!plugin.arena.canShop(p))return false;
        if(p.getInventory().firstEmpty()==-1){if(feedback)Text.error(p,"Inventaire plein");return false;}
        if(!p.getInventory().containsAtLeast(new ItemStack(o.currency),o.price)){if(feedback)Text.error(p,"Ressources insuffisantes");return false;}
        p.getInventory().removeItem(new ItemStack(o.currency,o.price));
        p.getInventory().addItem(new ItemStack(o.item,o.count));Text.sound(p,Sound.ENTITY_EXPERIENCE_ORB_PICKUP,1.6f);
        return true;
    }

    // ------------------------------------------------------------------ maps (admin)
    void maps(Player p) {
        plugin.api.command(p,"map",new String[]{"list"},r->{
            Gui g=new Gui(6,"<dark_gray>Rush •</dark_gray> <red><bold>Maps</bold></red>");
            int[] slots={10,11,12,13,14,15,16,19,20,21,22,23,24,25,28,29,30,31,32,33,34};int i=0;
            for(JsonElement e:r.getAsJsonArray("maps")) {
                if(i>=slots.length)break;JsonObject m=e.getAsJsonObject();String name=m.get("name").getAsString();boolean on=m.get("enabled").getAsBoolean();
                List<String> lore=new ArrayList<>(List.of(on?"<green>✔ Active":"<red>✘ Inactive","<gray>Modes : <white>"+String.join(", ",new Gson().fromJson(m.get("modes"),String[].class)),
                    "<gray>PNJ : <white>"+m.get("npcs").getAsInt()+"</white> <dark_gray>|</dark_gray> <gray>Générateurs : <white>"+m.get("generators").getAsInt()));
                for(JsonElement problem:m.getAsJsonArray("problems"))lore.add("<red>⚠ "+problem.getAsString());
                lore.addAll(List.of("","<yellow>Clic gauche : éditer","<yellow>Clic droit : "+(on?"désactiver":"activer"),"<red>Maj + clic droit : supprimer"));
                ItemStack item=icon(on?Material.FILLED_MAP:Material.MAP,"<gold><bold>"+name,lore.toArray(String[]::new));
                g.set(slots[i++],on?glow(item):item,click->{
                    if(click==ClickType.SHIFT_RIGHT){p.closeInventory();command(p,"map","delete",name);}
                    else if(click.isRightClick()){command(p,"map",on?"disable":"enable",name);reopen(p,()->maps(p));}
                    else{p.closeInventory();command(p,"map","edit",name);}
                });
            }
            g.set(48,icon(Material.WRITABLE_BOOK,"<green>Créer une map","<gray>Modèles : classic, vide, ou copie d’une map."),()->suggest(p,"/map create ","Clique ici puis saisis : nom [classic|vide|map]"));
            g.set(49,back(),()->main(p));
            g.frame().open(p);
        });
    }

    @EventHandler public void click(InventoryClickEvent e) {
        if(!(e.getView().getTopInventory().getHolder() instanceof Gui g))return;
        e.setCancelled(true);
        if(e.getRawSlot()<0||e.getRawSlot()>=g.inventory.getSize())return;
        Consumer<ClickType> action=g.actions.get(e.getRawSlot());
        if(action!=null&&e.getWhoClicked() instanceof Player p){Text.click(p);action.accept(e.getClick());}
    }
    @EventHandler public void drag(InventoryDragEvent e){if(e.getView().getTopInventory().getHolder() instanceof Gui)e.setCancelled(true);}
}
