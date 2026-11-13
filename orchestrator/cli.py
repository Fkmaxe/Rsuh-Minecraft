"""python3 -m orchestrator.cli --help"""
import argparse
import json
import os
import secrets
import signal
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_settings():
    settings = json.loads((ROOT/'config/settings.json').read_text())
    local = ROOT/'runtime/settings.json'
    if local.exists():
        settings.update(json.loads(local.read_text()))
    if settings['host'] != '127.0.0.1':
        raise ValueError('L’API de contrôle doit rester sur 127.0.0.1')
    if settings.get('test_offline'):
        raise ValueError('Le mode offline est réservé au réseau isolé de scripts/smoke_network.py')
    if not 1 <= settings['max_instances'] <= 64:
        raise ValueError('max_instances doit être entre 1 et 64')
    return settings


def request(path, data=None):
    settings = read_settings()
    token = (ROOT/'runtime/api.token').read_text().strip()
    req = urllib.request.Request(f"http://127.0.0.1:{settings['api_port']}{path}", data=json.dumps(data).encode() if data is not None else None,
                                 headers={'Authorization':'Bearer '+token, 'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def main():
    parser = argparse.ArgumentParser(description='Administration du réseau Rush')
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init')
    init.add_argument('--accept-eula', action='store_true')
    start = sub.add_parser('start')
    start.add_argument('--diagnostic', action='store_true')
    sub.add_parser('status')
    sub.add_parser('stop')
    test = sub.add_parser('deploy-test')
    test.add_argument('--mode', choices=['1v1','2v2','5v5'], default='1v1')
    test.add_argument('--timeout', type=int, default=240)
    role = sub.add_parser('role')
    role.add_argument('name')
    role.add_argument('role', choices=['player','moderator','admin'])
    args = parser.parse_args()
    runtime = ROOT/'runtime'
    if args.command == 'init':
        runtime.mkdir(exist_ok=True)
        runtime.chmod(0o700)
        for name in ('api.token','forwarding.secret'):
            path = runtime/name
            if not path.exists():
                path.write_text(secrets.token_hex(32))
            path.chmod(0o600)
        if args.accept_eula:
            local = runtime/'settings.json'
            overrides = json.loads(local.read_text()) if local.exists() else {}
            overrides['eula'] = True
            local.write_text(json.dumps(overrides, indent=2))
        print('Initialisé. EULA :', read_settings()['eula'])
    elif args.command == 'status':
        print(json.dumps(request('/health'), indent=2))
    elif args.command == 'role':
        print(request('/role', {'name':args.name,'role':args.role}))
    elif args.command == 'stop':
        import fcntl
        lock = (runtime/'supervisor.lock').open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            lock.close()
            raise RuntimeError('Le réseau n’est pas démarré')
        pid = int((runtime/'supervisor.pid').read_text())
        os.kill(pid, signal.SIGTERM)
        lock.close()
        print('Arrêt demandé')
    elif args.command == 'deploy-test':
        mid = request('/deploy-test', {'mode':args.mode})['match']
        try:
            deadline = time.monotonic()+args.timeout
            while time.monotonic() < deadline:
                if any(s['name'] == mid for s in request('/health')['servers']):
                    # Actual Minecraft status protocol validates a functioning game server.
                    from .protocol import ping
                    server = next(s for s in request('/health')['servers'] if s['name']==mid)
                    result = ping(server['host'],server['port'])
                    print(json.dumps({'match':mid,'deployment':'PASS','minecraft':result['version']}, indent=2))
                    return
                time.sleep(2)
            raise RuntimeError('Délai dépassé ; consulter runtime/servers/'+mid+'/console.log')
        finally:
            try:
                request('/abort', {'match':mid,'reason':'deployment_test_complete'})
            except Exception as exc:
                print('Nettoyage :', exc, file=sys.stderr)
    elif args.command == 'start':
        import fcntl
        from .core import Core
        from .processes import Processes
        from .server import serve, make_logger
        settings = read_settings()
        settings['diagnostic'] |= args.diagnostic
        lock = (runtime/'supervisor.lock').open('w')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        log = make_logger(runtime/'events.jsonl')
        token = (runtime/'api.token').read_text().strip()
        processes = Processes(ROOT,settings,token,(runtime/'forwarding.secret').read_text().strip(),log)
        core = Core(runtime/'rush.sqlite3',processes,log)
        server = serve(core, settings, token)
        (runtime/'supervisor.pid').write_text(str(os.getpid()))
        stopping = False
        def stop(*_):
            nonlocal stopping
            stopping = True
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        try:
            processes.start()
            processes.start_proxy()
            print('Réseau en démarrage sur :'+str(settings['proxy_port']), flush=True)
            while not stopping:
                core.tick()
                if processes.status('lobby') == 'failed' or processes.status('proxy') == 'failed':
                    raise RuntimeError('Lobby ou proxy indisponible : voir les console.log')
                time.sleep(1)
        finally:
            server.shutdown()
            server.server_close()
            processes.close()
            core.db.close()
            (runtime/'supervisor.pid').unlink(missing_ok=True)
            lock.close()


if __name__ == '__main__':
    main()
