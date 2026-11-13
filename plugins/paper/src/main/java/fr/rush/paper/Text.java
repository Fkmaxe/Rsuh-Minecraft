package fr.rush.paper;

import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.format.NamedTextColor;
import net.kyori.adventure.text.format.TextDecoration;
import net.kyori.adventure.text.minimessage.MiniMessage;
import net.kyori.adventure.text.serializer.legacy.LegacyComponentSerializer;
import net.kyori.adventure.title.Title;
import org.bukkit.Sound;
import org.bukkit.entity.Player;
import java.time.Duration;

/** Single place for the network's visual identity: chat prefix, colors, sounds and titles. */
final class Text {
    static final MiniMessage MM = MiniMessage.miniMessage();
    static final Component PREFIX = MM.deserialize("<dark_gray>[</dark_gray><gold>Rush</gold><dark_gray>]</dark_gray> ");
    private Text() {}

    /** Server-authored MiniMessage; never pass player-typed text here. */
    static Component mm(String text) { return MM.deserialize(text); }
    /** Item names and lore: Minecraft italicizes custom names by default. */
    static Component item(String text) { return MM.deserialize(text).decoration(TextDecoration.ITALIC,false); }
    static String legacy(String text) { return LegacyComponentSerializer.legacySection().serialize(MM.deserialize(text)); }

    static void send(Player p,String text) { p.sendMessage(PREFIX.append(mm(text))); }
    static void ok(Player p,String text) { send(p,text); sound(p,Sound.ENTITY_EXPERIENCE_ORB_PICKUP,1.4f); }
    static void error(Player p,String plain) {
        p.sendMessage(PREFIX.append(Component.text("✘ ",NamedTextColor.RED)).append(Component.text(plain==null?"Erreur":plain,NamedTextColor.RED)));
        sound(p,Sound.ENTITY_VILLAGER_NO,1f);
    }
    static void sound(Player p,Sound sound,float pitch) { p.playSound(p.getLocation(),sound,0.7f,pitch); }
    static void click(Player p) { sound(p,Sound.UI_BUTTON_CLICK,1.2f); }
    static void title(Player p,String title,String subtitle,int fadeIn,int stay,int fadeOut) {
        p.showTitle(Title.title(mm(title),mm(subtitle),Title.Times.times(ticks(fadeIn),ticks(stay),ticks(fadeOut))));
    }
    static void actionBar(Player p,String text) { p.sendActionBar(mm(text)); }
    private static Duration ticks(int t) { return Duration.ofMillis(t*50L); }
    static String clock(long seconds) { return String.format("%02d:%02d",Math.max(0,seconds)/60,Math.max(0,seconds)%60); }
    static String side(int side) { return side==0?"<red>Rouge</red>":"<blue>Bleu</blue>"; }
    static String sideColor(int side) { return side==0?"red":"blue"; }
}
