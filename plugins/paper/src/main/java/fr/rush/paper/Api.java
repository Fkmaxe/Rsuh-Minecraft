package fr.rush.paper;

import com.google.gson.*;
import org.bukkit.Bukkit;
import org.bukkit.entity.Player;
import java.net.URI;
import java.net.http.*;
import java.time.Duration;
import java.util.concurrent.*;
import java.util.function.Consumer;

final class Api implements AutoCloseable {
    private final RushPlugin plugin;
    private final HttpClient client=HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();
    private final ExecutorService executor=Executors.newSingleThreadExecutor();
    Api(RushPlugin plugin) { this.plugin=plugin; }
    void post(String path, JsonObject data, Consumer<JsonObject> success, Consumer<String> error) {
        executor.submit(()-> {
            try {
                HttpRequest req=HttpRequest.newBuilder(URI.create(plugin.config.get("api").getAsString()+path))
                    .timeout(Duration.ofSeconds(8)).header("Authorization","Bearer "+plugin.config.get("token").getAsString())
                    .header("Content-Type","application/json").POST(HttpRequest.BodyPublishers.ofString(data.toString())).build();
                HttpResponse<String> response=client.send(req,HttpResponse.BodyHandlers.ofString());
                JsonObject result=JsonParser.parseString(response.body()).getAsJsonObject();
                if(response.statusCode()!=200) throw new IllegalStateException(result.has("error")?result.get("error").getAsString():"API indisponible");
                if(plugin.isEnabled()) Bukkit.getScheduler().runTask(plugin,()->success.accept(result));
            } catch(Exception ex) {
                if(plugin.isEnabled()) Bukkit.getScheduler().runTask(plugin,()->error.accept(ex.getMessage()));
            }
        });
    }
    void command(Player player,String action,String[] args,Consumer<JsonObject> consumer) {
        JsonObject body=new JsonObject(); body.addProperty("player",player.getUniqueId().toString()); body.addProperty("name",player.getName());
        body.addProperty("action",action); body.add("args",new Gson().toJsonTree(args));
        post("/command",body,result->{if(player.isOnline()) consumer.accept(result);},message->{if(player.isOnline()) player.sendMessage("§c"+message);});
    }
    public void close() { executor.shutdownNow(); }
}
