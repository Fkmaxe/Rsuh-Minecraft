package fr.rush.paper;

import io.papermc.paper.scoreboard.numbers.NumberFormat;
import net.kyori.adventure.text.Component;
import org.bukkit.scoreboard.*;
import java.util.*;

/**
 * Flicker-free sidebar: only changed lines are replaced. Each entry ends with a unique invisible
 * color code so identical lines (blank separators) can coexist. Legacy text stays readable for
 * 1.9 clients through ViaBackwards (entries up to 40 characters, numbers hidden on modern clients).
 */
final class Sidebar {
    private final Scoreboard board;
    private final Objective objective;
    private final List<String> current=new ArrayList<>();

    Sidebar(Scoreboard board,String title) {
        this.board=board;
        objective=board.registerNewObjective("rush_side",Criteria.DUMMY,Text.mm(title));
        objective.setDisplaySlot(DisplaySlot.SIDEBAR);
        objective.numberFormat(NumberFormat.blank());
    }
    void title(String title) { objective.displayName(Text.mm(title)); }

    /** Lines are MiniMessage strings authored by the plugin. */
    void update(List<String> lines) {
        int size=Math.min(15,lines.size());
        for(int i=0;i<size;i++) {
            String entry=Text.legacy(lines.get(i))+"§"+Integer.toHexString(i)+"§r";
            if(i<current.size()&&current.get(i).equals(entry))continue;
            if(i<current.size())board.resetScores(current.get(i));
            objective.getScore(entry).setScore(size-i);
            if(i<current.size())current.set(i,entry);else current.add(entry);
        }
        while(current.size()>size)board.resetScores(current.remove(current.size()-1));
        // Scores encode the order: refresh them only when the line count changed.
        if(size!=lastSize)for(int i=0;i<current.size();i++)objective.getScore(current.get(i)).setScore(size-i);
        lastSize=size;
    }
    private int lastSize=-1;
    Component title() { return objective.displayName(); }
}
