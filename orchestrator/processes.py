"""Single-host process supervisor. Backends are reachable on loopback only."""
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

# Aikar's G1 flags for Paper. -Xms equals -Xmx, but without AlwaysPreTouch: memory is only
# taken when an instance really needs it, so short matches stay small.
G1_FLAGS = ['-XX:+UseG1GC', '-XX:+ParallelRefProcEnabled', '-XX:MaxGCPauseMillis=200', '-XX:+UnlockExperimentalVMOptions',
            '-XX:+DisableExplicitGC', '-XX:G1NewSizePercent=30', '-XX:G1MaxNewSizePercent=40', '-XX:G1HeapRegionSize=8M',
            '-XX:G1ReservePercent=20', '-XX:G1HeapWastePercent=5', '-XX:G1MixedGCCountTarget=4',
            '-XX:InitiatingHeapOccupancyPercent=15', '-XX:G1MixedGCLiveThresholdPercent=90',
            '-XX:G1RSetUpdatingPauseTimePercent=5', '-XX:SurvivorRatio=32', '-XX:+PerfDisableSharedMem',
            '-XX:MaxTenuringThreshold=1']


def prune_match(folder):
    """Keep a finished match's console and Rush events; delete its world, jars and secrets."""
    folder = Path(folder)
    for entry in folder.iterdir():
        if entry.name in ('console.log', 'logs', 'plugins'):
            continue
        if entry.is_symlink() or not entry.is_dir():
            entry.unlink()  # Shared lobby folders are symlinks: unlink, never follow.
        else:
            shutil.rmtree(entry)
    plugins = folder / 'plugins'
    if plugins.is_dir():
        for entry in plugins.iterdir():
            if entry.name == 'Rush':
                continue
            if entry.is_symlink() or not entry.is_dir():
                entry.unlink()
            else:
                shutil.rmtree(entry)
        (plugins / 'Rush' / 'instance.json').unlink(missing_ok=True)  # Holds the API token.


class Processes:
    def __init__(self, root, settings, token, forwarding_secret, log):
        self.root = Path(root).resolve()
        self.settings, self.token, self.secret, self.log = settings, token, forwarding_secret, log
        self.children = {}

    def available(self):
        return sum(n.startswith('rush-') for n in list(self.children)) < self.settings['max_instances']

    def start(self, match=None):
        if not self.settings['eula']:
            raise RuntimeError("EULA requise : python3 -m orchestrator.cli init --accept-eula")
        name = match['id'] if match else 'lobby'
        used = {c['port'] for c in list(self.children.values())}
        port = self.settings['lobby_port'] if not match else next(p for p in range(self.settings['first_match_port'], self.settings['first_match_port']+self.settings['max_instances']) if p not in used)
        folder = self.root / 'runtime' / 'servers' / name
        (folder / 'plugins' / 'Rush').mkdir(parents=True, exist_ok=True)
        # Paper's immutable jars and its remapping cache (about 3 s of every boot) are reused
        # after the lobby has bootstrapped them; instances share the same plugin jars.
        if match:
            for dirname in ('libraries','versions','cache','plugins/.paper-remapped'):
                source = self.root / 'runtime' / 'servers' / 'lobby' / dirname
                dest = folder / dirname
                if source.is_dir() and not dest.exists():
                    dest.symlink_to(source, target_is_directory=True)
        (folder / 'config').mkdir(exist_ok=True)
        shutil.copy2(self.root / 'artifacts' / 'rush-paper.jar', folder / 'plugins' / 'rush-paper.jar')
        shutil.copy2(self.root / 'artifacts' / 'BungeeGuard.jar', folder / 'plugins' / 'BungeeGuard.jar')
        (folder / 'plugins' / 'BungeeGuard').mkdir(exist_ok=True)
        guard = folder / 'plugins' / 'BungeeGuard' / 'config.yml'
        guard.write_text(f'allowed-tokens:\n  - "{self.secret}"\n')
        guard.chmod(0o600)
        (folder / 'bukkit.yml').write_text('settings:\n  allow-end: false\nworlds:\n  world:\n    generator: Rush\n')
        (folder / 'spigot.yml').write_text('settings:\n  bungeecord: true\n')
        (folder / 'eula.txt').write_text('eula=true\n')
        (folder / 'server.properties').write_text(f'''server-ip=127.0.0.1
server-port={port}
online-mode=false
enforce-secure-profile=false
spawn-protection=0
max-players={len(match['teams'][0])*2 if match else 100}
view-distance=4
simulation-distance=4
level-type=minecraft:flat
generator-settings={{"layers":[],"biome":"minecraft:the_void"}}
generate-structures=false
network-compression-threshold=-1
sync-chunk-writes=false
allow-flight=true
difficulty=normal
enable-rcon=false
pause-when-empty-seconds=-1
motd=Rush {name}
''')
        (folder / 'config' / 'paper-global.yml').write_text(f'''_version: 31
proxies:
  bungee-cord:
    online-mode: {str(not self.settings.get('test_offline',False)).lower()}
  velocity:
    enabled: false
    online-mode: {str(not self.settings.get('test_offline',False)).lower()}
    secret: "{self.secret}"
misc:
  enable-nether: false
''')
        payload = dict(api=f"http://127.0.0.1:{self.settings['api_port']}", token=self.token, id=name,
                       diagnostic=self.settings['diagnostic'], match=match,
                       pvp=self.settings['pvp'][match['mode'] if match else '1v1'])
        cfg = folder / 'plugins' / 'Rush' / 'instance.json'
        cfg.write_text(json.dumps(payload, indent=2))
        cfg.chmod(0o600)
        jar = self.root / 'artifacts' / 'paper.jar'
        self.spawn(name, folder, port, [self.settings['java'], '-XX:ActiveProcessorCount=2', f"-Xms{self.settings['memory_mb']}M", f"-Xmx{self.settings['memory_mb']}M", *G1_FLAGS, '-jar', str(jar), '--nogui'])

    def start_proxy(self):
        folder = self.root / 'runtime' / 'proxy'
        (folder / 'plugins').mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.root / 'artifacts' / 'rush-proxy.jar', folder / 'plugins' / 'rush-proxy.jar')
        # Translation and BungeeGuard forwarding support legacy 1.9.4 clients.
        for jar in ('ViaVersion.jar','ViaBackwards.jar'):
            shutil.copy2(self.root / 'artifacts' / jar, folder / 'plugins' / jar)
        (folder / 'plugins' / 'viaversion').mkdir(exist_ok=True)
        (folder / 'plugins' / 'viaversion' / 'config.yml').write_text('velocity-ping-interval: -1\nvelocity-ping-save: false\nvelocity-servers:\n  default: 774\n')
        (folder / 'forwarding.secret').write_text(self.secret)
        (folder / 'forwarding.secret').chmod(0o600)
        (folder / 'velocity.toml').write_text(f'''config-version = "2.7"
bind = "{'127.0.0.1' if self.settings.get('test_offline') else '0.0.0.0'}:{self.settings['proxy_port']}"
motd = "Rush Training | 1v1 - 2v2 - 5v5"
show-max-players = 100
online-mode = {str(not self.settings.get('test_offline',False)).lower()}
force-key-authentication = false
player-info-forwarding-mode = "bungeeguard"
forwarding-secret-file = "forwarding.secret"
announce-forge = false
kick-existing-players = false
ping-passthrough = "DISABLED"
[servers]
lobby = "127.0.0.1:{self.settings['lobby_port']}"
try = ["lobby"]
[forced-hosts]
"localhost" = ["lobby"]
[advanced]
compression-threshold = 256
compression-level = -1
login-ratelimit = {0 if self.settings.get('test_offline') else 3000}
connection-timeout = 5000
read-timeout = 30000
haproxy-protocol = false
tcp-fast-open = false
bungee-plugin-message-channel = false
failover-on-unexpected-server-disconnect = true
[query]
enabled = false
''')
        self.spawn('proxy', folder, self.settings['proxy_port'], [self.settings['java'], '-Xms512M', '-Xmx512M', '-XX:+UseG1GC',
                   f"-Drush.api=http://127.0.0.1:{self.settings['api_port']}", '-jar', str(self.root / 'artifacts' / 'velocity.jar')], env={'RUSH_TOKEN':self.token})

    def spawn(self, name, folder, port, command, env=None):
        logfile = folder / 'console.log'
        handle = logfile.open('wb')
        try:
            process = subprocess.Popen(command, cwd=folder, stdin=subprocess.PIPE, stdout=handle, stderr=subprocess.STDOUT,
                                       env={**os.environ, **(env or {})}, start_new_session=True)
        finally:
            handle.close()
        self.children[name] = dict(process=process, port=port, started=time.monotonic(), logfile=logfile, ready=False)
        self.log('process_spawned', instance=name, pid=process.pid, port=port)

    def status(self, name):
        c = self.children.get(name)
        if not c or c['process'].poll() is not None:
            return 'failed'
        age = time.monotonic()-c['started']
        if not c['ready']:
            # A TCP listener alone is insufficient: wait for plugin initialization.
            text = c['logfile'].read_text(errors='replace')[-100000:]
            marker = 'RUSH_READY' if name != 'proxy' else 'RUSH_PROXY_READY'
            c['ready'] = marker in text
        if not c['ready'] and age > self.settings['startup_timeout']:
            return 'failed'
        if name.startswith('rush-') and age > self.settings['match_timeout']:
            return 'failed'
        return 'ready' if c['ready'] else 'starting'

    def servers(self):
        return [{'name':name,'host':'127.0.0.1','port':c['port']} for name,c in list(self.children.items())
                if name != 'proxy' and not c.get('stopping') and self.status(name) == 'ready']

    def stop(self, name):
        c = self.children.get(name)
        if not c or c.get('stopping'):
            return
        c['stopping'] = True
        p = c['process']
        # Stop asynchronously so result callbacks can return before the game exits.
        if p.poll() is None:
            try:
                p.stdin.write(b'stop\n' if name != 'proxy' else b'shutdown\n')
                p.stdin.flush()
            except (OSError, BrokenPipeError):
                pass
            import threading
            def reap():
                try:
                    p.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGTERM)
                    try:
                        p.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(p.pid, signal.SIGKILL)
                        p.wait()
                p.stdin.close()
                self.archive(name, c)
                self.children.pop(name, None)
            threading.Thread(target=reap, daemon=False).start()
        else:
            if p.stdin:
                p.stdin.close()
            self.archive(name, c)
            self.children.pop(name, None)
        self.log('process_stopping', instance=name, pid=p.pid)

    def archive(self, name, child):
        if not name.startswith('rush-'):
            return
        try:
            prune_match(child['logfile'].parent)
        except OSError as exc:
            self.log('prune_failed', instance=name, error=str(exc))

    def close(self):
        for name in list(self.children):
            self.stop(name)
