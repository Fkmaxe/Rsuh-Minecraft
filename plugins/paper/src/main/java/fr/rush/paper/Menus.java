package fr.rush.paper;

import com.google.gson.*;
import org.bukkit.*;
import org.bukkit.entity.Player;
import org.bukkit.event.*;
import org.bukkit.event.inventory.*;
import org.bukkit.inventory.*;
import org.bukkit.inventory.meta.ItemMeta;
import java.util.*;

final class Menus implements Listener {
    private final RushPlugin plugin;
    private record Offer(Material item,int count,Material currency,int price,String label) {}
    private final List<Offer> offers=List.of(
        new Offer(Material.SANDSTONE,16,Material.BRICK,4,"16 blocs"),
        new Offer(Material.STONE_SWORD,1,Material.BRICK,10,"Épée en pierre"),
        new Offer(Material.IRON_SWORD,1,Material.IRON_INGOT,8,"Épée en fer"),
        new Offer(Material.LEATHER_CHESTPLATE,1,Material.BRICK,10,"Plastron"),
        new Offer(Material.IRON_CHESTPLATE,1,Material.IRON_INGOT,12,"Plastron en fer"),
        new Offer(Material.WOODEN_PICKAXE,1,Material.BRICK,8,"Pioche"),
        new Offer(Material.TNT,1,Material.IRON_INGOT,6,"TNT (allumer au briquet)"),
        new Offer(Material.FLINT_AND_STEEL,1,Material.BRICK,8,"Briquet"),
        new Offer(Material.GOLDEN_APPLE,1,Material.GOLD_INGOT,4,"Pomme dorée"));
    private static final class Menu implements InventoryHolder {
        Inventory inventory;
        Map<Integer,Runnable> actions=new HashMap<>();
        public Inventory getInventory(){return inventory;}
    }
    Menus(RushPlugin plugin){this.plugin=plugin;}
    static ItemStack item(Material type,String label,String... lore) {
        ItemStack stack=new ItemStack(type);ItemMeta meta=stack.getItemMeta();meta.setDisplayName(label);meta.setLore(Arrays.asList(lore));stack.setItemMeta(meta);return stack;
    }
    private Menu menu(String title){Menu m=new Menu();m.inventory=Bukkit.createInventory(m,54,title);return m;}
    private void add(Menu m,int slot,Material icon,String label,Runnable action,String... lore){m.inventory.setItem(slot,item(icon,label,lore));m.actions.put(slot,action);}
    private void command(Player p,String action,String... args){plugin.api.command(p,action,args,r->{p.sendMessage("§a"+r.get("message").getAsString());for(JsonElement line:r.getAsJsonArray("lines"))p.sendMessage("§7"+line.getAsString());});}
    void main(Player p) {
        Menu m=menu("Rush • Entraînement");
        add(m,11,Material.WOODEN_SWORD,"§e1v1",()->command(p,"queue","1v1"));
        add(m,13,Material.STONE_SWORD,"§e2v2",()->command(p,"queue","2v2"));
        add(m,15,Material.IRON_SWORD,"§e5v5",()->command(p,"queue","5v5"));
        add(m,29,Material.PLAYER_HEAD,"§aMon équipe",()->social(p,"team"));
        add(m,30,Material.EMERALD,"§aMes amis",()->social(p,"friend"));
        add(m,31,Material.GOLD_INGOT,"§6Classements",()->rankings(p));
        add(m,32,Material.RED_BED,"§dParties privées",()->privateMenu(p));
        add(m,33,Material.BARRIER,"§cQuitter file / partie",()->command(p,"leave"));
        p.openInventory(m.inventory);
    }
    void rankings(Player p) {
        Menu m=menu("Rush • Classements");int slot=10;
        for(String mode:List.of("global","1v1","2v2","5v5"))add(m,slot++,Material.GOLD_INGOT,mode,()->plugin.api.command(p,"leaderboard",new String[]{mode},r->display(p,r)));
        p.openInventory(m.inventory);
    }
    void display(Player p,JsonObject result) {
        Menu m=menu("Rush • "+result.get("message").getAsString());int i=0;
        for(JsonElement line:result.getAsJsonArray("lines")){if(i>=45)break;m.inventory.setItem(i++,item(Material.PAPER,"§f"+line.getAsString()));}
        add(m,49,Material.ARROW,"Retour",()->main(p));p.openInventory(m.inventory);
    }
    void social(Player p,String kind) {
        plugin.api.command(p,kind,new String[]{"list"},r->{
            Menu m=menu("Rush • "+(kind.equals("team")?"Équipe":"Amis"));int slot=0;
            for(JsonElement line:r.getAsJsonArray("lines")){if(slot>=27)break;m.inventory.setItem(slot++,item(Material.PAPER,"§f"+line.getAsString()));}
            slot=27;
            if(r.has("actions"))for(JsonElement entry:r.getAsJsonArray("actions")) {
                if(slot>=36)break;JsonObject a=entry.getAsJsonObject();
                add(m,slot++,Material.PLAYER_HEAD,"§e"+a.get("label").getAsString(),()->command(p,kind,a.get("action").getAsString(),a.get("name").getAsString()));
            }
            add(m,36,Material.EMERALD,"Inviter / ajouter un joueur connecté",()->players(p,kind));
            add(m,38,Material.WRITABLE_BOOK,"Accepter une invitation",()->{p.closeInventory();p.sendMessage("§e/"+kind+" accept <pseudo>");});
            add(m,40,Material.BARRIER,kind.equals("team")?"Quitter l’équipe":"Retirer un ami",()->{if(kind.equals("team"))command(p,kind,"leave");else {p.closeInventory();p.sendMessage("§e/friend remove <pseudo>");}});
            add(m,49,Material.ARROW,"Retour",()->main(p));p.openInventory(m.inventory);
        });
    }
    void players(Player p,String kind) {
        plugin.api.command(p,"players",new String[]{},r->{
            Menu m=menu("Rush • Choisir un joueur");int i=0;
            for(JsonElement value:r.getAsJsonArray("lines")){if(i>=45)break;String name=value.getAsString();add(m,i++,Material.PLAYER_HEAD,name,()->command(p,kind,kind.equals("team")?"invite":"add",name));}
            add(m,49,Material.PAPER,"Inviter par pseudo",()->{p.closeInventory();p.sendMessage("§e/"+kind+" "+(kind.equals("team")?"invite":"add")+" <pseudo>");});p.openInventory(m.inventory);
        });
    }
    void privateMenu(Player p) {
        Menu m=menu("Rush • Privé");int i=11;
        for(String mode:List.of("1v1","2v2","5v5"))add(m,i++,Material.RED_BED,"Créer "+mode,()->command(p,"private","create",mode));
        add(m,29,Material.PAPER,"Rejoindre avec un code",()->{p.closeInventory();p.sendMessage("§e/private join <code>");});
        add(m,31,Material.LIME_DYE,"Démarrer mon salon",()->command(p,"private","start"));
        add(m,33,Material.PLAYER_HEAD,"Composition du salon",()->command(p,"private","list"));p.openInventory(m.inventory);
    }
    void shop(Player p) {
        if(plugin.arena==null||!plugin.arena.canShop(p)){p.sendMessage("§cBoutique disponible près de votre base pendant la partie.");return;}
        Menu m=menu("Rush • Boutique");
        for(int i=0;i<offers.size();i++){Offer o=offers.get(i);add(m,i,o.item,"§e"+o.label,()->buy(p,o),"§7Coût : "+o.price+" "+o.currency);}
        p.openInventory(m.inventory);
    }
    private void buy(Player p,Offer o) {
        if(!plugin.arena.canShop(p))return;
        if(p.getInventory().firstEmpty()==-1){p.sendMessage("§cInventaire plein");return;}
        if(!p.getInventory().containsAtLeast(new ItemStack(o.currency),o.price)){p.sendMessage("§cRessources insuffisantes");return;}
        p.getInventory().removeItem(new ItemStack(o.currency,o.price));
        p.getInventory().addItem(new ItemStack(o.item,o.count));p.playSound(p.getLocation(),Sound.ENTITY_EXPERIENCE_ORB_PICKUP,0.5f,1f);
    }
    @EventHandler public void click(InventoryClickEvent e) {
        if(!(e.getView().getTopInventory().getHolder() instanceof Menu m))return;
        e.setCancelled(true);
        if(e.getRawSlot()<0||e.getRawSlot()>=m.inventory.getSize())return;
        Runnable action=m.actions.get(e.getRawSlot());if(action!=null)action.run();
    }
    @EventHandler public void drag(InventoryDragEvent e) {if(e.getView().getTopInventory().getHolder() instanceof Menu)e.setCancelled(true);}
}
