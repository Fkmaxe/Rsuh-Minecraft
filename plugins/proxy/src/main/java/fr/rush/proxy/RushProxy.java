package fr.rush.proxy;

import com.google.gson.*;
import com.google.inject.Inject;
import com.velocitypowered.api.event.Subscribe;
import com.velocitypowered.api.event.proxy.ProxyInitializeEvent;
import com.velocitypowered.api.event.player.ServerPreConnectEvent;
import com.velocitypowered.api.plugin.Plugin;
import com.velocitypowered.api.proxy.*;
import com.velocitypowered.api.proxy.server.*;
import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.minimessage.MiniMessage;
import org.slf4j.Logger;
import java.net.*;
import java.net.http.*;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.*;

@Plugin(id="rush-proxy", name="RushProxy", version="1.1.0", authors={"Rush"})
public final class RushProxy {
    private static final Component PREFIX = MiniMessage.miniMessage().deserialize("<dark_gray>[</dark_gray><gold>Rush</gold><dark_gray>]</dark_gray> ");
    private final ProxyServer proxy;
    private final Logger log;
    private final HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();
    private final String api = System.getProperty("rush.api", "http://127.0.0.1:8787");
    private final String token = System.getenv("RUSH_TOKEN");
    private volatile Map<UUID,String> routes = Map.of();
    private final Set<UUID> connecting = ConcurrentHashMap.newKeySet();
    private int failures;

    @Inject public RushProxy(ProxyServer proxy, Logger log) { this.proxy=proxy; this.log=log; }

    @Subscribe public void start(ProxyInitializeEvent event) {
        // 1 s: invitations and transfers feel immediate; two small loopback requests per second.
        proxy.getScheduler().buildTask(this, this::poll).repeat(Duration.ofSeconds(1)).schedule();
        log.info("RUSH_PROXY_READY");
    }

    private JsonObject call(String path, JsonObject body) throws Exception {
        HttpRequest.Builder builder = HttpRequest.newBuilder(URI.create(api+path)).timeout(Duration.ofSeconds(5))
            .header("Authorization", "Bearer "+token).header("Content-Type","application/json");
        if (body != null) builder.POST(HttpRequest.BodyPublishers.ofString(body.toString()));
        HttpResponse<String> result = http.send(builder.build(), HttpResponse.BodyHandlers.ofString());
        if (result.statusCode()!=200) throw new IllegalStateException("API status "+result.statusCode());
        return JsonParser.parseString(result.body()).getAsJsonObject();
    }

    private static boolean dynamic(String server) { return !server.equals("lobby"); }

    private void poll() {
        try {
            JsonArray online = new JsonArray();
            for (Player p : proxy.getAllPlayers()) {
                JsonObject row = new JsonObject(); row.addProperty("id",p.getUniqueId().toString()); row.addProperty("name",p.getUsername()); online.add(row);
            }
            JsonObject presence = new JsonObject(); presence.add("players",online); call("/presence",presence);
            JsonObject data = call("/routes",null);
            Set<String> live = new HashSet<>();
            for (JsonElement element : data.getAsJsonArray("servers")) {
                JsonObject s=element.getAsJsonObject(); String name=s.get("name").getAsString(); live.add(name);
                if (proxy.getServer(name).isEmpty()) proxy.registerServer(new ServerInfo(name,new InetSocketAddress(s.get("host").getAsString(),s.get("port").getAsInt())));
            }
            Map<UUID,String> next = new HashMap<>();
            data.getAsJsonObject("routes").entrySet().forEach(e -> next.put(UUID.fromString(e.getKey()),e.getValue().getAsString()));
            routes = Map.copyOf(next);
            for (Player p : proxy.getAllPlayers()) {
                String target = routes.getOrDefault(p.getUniqueId(),"lobby");
                String current=p.getCurrentServer().map(s->s.getServerInfo().getName()).orElse("");
                if (!current.equals(target) && connecting.add(p.getUniqueId())) {
                    Optional<RegisteredServer> server=proxy.getServer(target);
                    if (server.isPresent()) p.createConnectionRequest(server.get()).connect()
                        .whenComplete((r,e)->connecting.remove(p.getUniqueId()));
                    else connecting.remove(p.getUniqueId());
                }
            }
            if (data.has("messages")) deliver(data.getAsJsonObject("messages"));
            for (RegisteredServer s : proxy.getAllServers()) {
                String name=s.getServerInfo().getName();
                if (dynamic(name) && !live.contains(name) && s.getPlayersConnected().isEmpty()) proxy.unregisterServer(s.getServerInfo());
            }
            failures=0;
        } catch (Exception e) {
            if (++failures==1 || failures%30==0) log.warn("Control plane unavailable: {}", e.toString());
        }
    }

    /** Notifications are delivered by the proxy so they reach players in the lobby, a game or the editor. */
    private void deliver(JsonObject messages) {
        for (Map.Entry<String,JsonElement> entry : messages.entrySet()) {
            Optional<Player> player = proxy.getPlayer(UUID.fromString(entry.getKey()));
            if (player.isEmpty()) continue;
            for (JsonElement text : entry.getValue().getAsJsonArray()) {
                try { player.get().sendMessage(PREFIX.append(MiniMessage.miniMessage().deserialize(text.getAsString()))); }
                catch (RuntimeException e) { log.warn("Invalid notification: {}", e.toString()); }
            }
        }
    }

    @Subscribe public void admission(ServerPreConnectEvent event) {
        String target=event.getOriginalServer().getServerInfo().getName();
        // Game and editor instances only admit their routed players (no /server shortcuts).
        if (dynamic(target) && !target.equals(routes.get(event.getPlayer().getUniqueId())))
            event.setResult(ServerPreConnectEvent.ServerResult.denied());
    }
}
