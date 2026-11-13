import hmac
import json
import logging
from logging.handlers import RotatingFileHandler
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .core import RuleError


def make_logger(path):
    logger = logging.getLogger('rush')
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(path, maxBytes=10_000_000, backupCount=5, encoding='utf8')
    logger.addHandler(handler)
    def emit(event, **fields):
        import datetime
        logger.info(json.dumps(dict(time=datetime.datetime.now(datetime.timezone.utc).isoformat(), event=event, **fields)))
    return emit


def serve(core, settings, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never log bearer tokens or raw player payloads.

        def respond(self, code, payload):
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.dispatch(False)

        def do_POST(self):
            self.dispatch(True)

        def dispatch(self, post):
            if not hmac.compare_digest(self.headers.get('Authorization',''), 'Bearer '+token):
                return self.respond(401, {'error':'Authentification requise'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 <= length <= 65536:
                    return self.respond(413, {'error':'Requête trop grande'})
                body = json.loads(self.rfile.read(length)) if post else {}
                if not isinstance(body, dict):
                    raise RuleError('Objet JSON requis')
                if self.path == '/health' and not post:
                    result = {'ok':True, 'instances':len(core.active), 'servers':core.deployer.servers()}
                elif self.path == '/routes' and not post:
                    result = core.routing()
                elif self.path == '/presence' and post:
                    core.presence(body['players'])
                    result = {'ok':True}
                elif self.path == '/command' and post:
                    result = core.command(body['player'],body['name'],body['action'],body.get('args',[]))
                elif self.path == '/result' and post:
                    result = core.finish(body['match'],body['winner'],body['stats'])
                elif self.path == '/abort' and post:
                    core.abort(body['match'], body.get('reason','game_abort'))
                    result = {'ok':True}
                elif self.path == '/role' and post:
                    core.set_role(core.target(body['name']), body['role'])
                    result = {'ok':True}
                elif self.path == '/deploy-test' and post:
                    # Real isolated Minecraft process; synthetic players never get ratings.
                    import uuid
                    mode = body.get('mode', '1v1')
                    from .core import MODES
                    if mode not in MODES:
                        raise RuleError('Mode invalide')
                    with core.lock:
                        mid = core.launch(mode, [[str(uuid.uuid4()) for _ in range(MODES[mode])] for _ in range(2)], False)
                    result = {'match':mid}
                else:
                    return self.respond(404, {'error':'Route inconnue'})
                self.respond(200, result)
            except (RuleError, KeyError, ValueError, TypeError) as exc:
                self.respond(400, {'error':str(exc)})
            except Exception:
                logging.getLogger('rush').exception('api_error')
                self.respond(500, {'error':'Erreur interne ; consulter les logs'})

    server = ThreadingHTTPServer((settings['host'],settings['api_port']), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
