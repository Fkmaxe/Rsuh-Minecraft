"""Staff tools: spectate games, vanish, freeze, sanctions (warn/mute/kick/ban), reports, staff chat.

State that must follow a player across servers (freeze, vanish, mutes, bans) lives here; each
Paper instance polls it every second and the proxy enforces bans and kicks.
"""
from __future__ import annotations

import re
import time
from datetime import datetime

from .core import RuleError, button

STAFF = ('moderator', 'admin')
ACTIONS = {'spec', 'vanish', 'freeze', 'kick', 'ban', 'unban', 'mute', 'unmute', 'warn', 'history', 'report', 'sc', 'staff'}
MODERATOR_MAX_BAN = 7 * 86400
REPORT_COOLDOWN = 60
CONSOLE = 'CONSOLE'


def esc(text, limit=200):
    """Player-typed text inside MiniMessage: no tags, no line breaks, bounded."""
    text = ' '.join(str(text).split())[:limit]
    return text.replace('\\', '\\\\').replace('<', '\\<')


def parse_duration(text):
    """'30m', '2h', '7d' / '7j' -> seconds; 'perm' -> None."""
    text = text.lower()
    if text in ('perm', 'permanent', 'def', 'definitif', 'définitif'):
        return None
    match = re.fullmatch(r'(\d{1,4})([mhdj])', text)
    if not match:
        raise RuleError("Durée : 30m, 2h, 7d ou perm")
    value = int(match.group(1)) * {'m': 60, 'h': 3600, 'd': 86400, 'j': 86400}[match.group(2)]
    if value <= 0:
        raise RuleError("Durée invalide")
    return value


def human(seconds):
    if seconds is None:
        return 'définitif'
    seconds = max(0, int(seconds))
    if seconds >= 86400:
        return f"{seconds // 86400} j {seconds % 86400 // 3600} h"
    if seconds >= 3600:
        return f"{seconds // 3600} h {seconds % 3600 // 60} min"
    return f"{max(1, seconds // 60)} min"


def stamp(ts):
    return datetime.fromtimestamp(ts).strftime('%d/%m/%Y %H:%M')


class Moderation:
    def __init__(self, core):
        self.core = core
        core.db.executescript("""
          CREATE TABLE IF NOT EXISTS sanctions(id INTEGER PRIMARY KEY AUTOINCREMENT, player TEXT NOT NULL, kind TEXT NOT NULL,
            reason TEXT NOT NULL, actor TEXT NOT NULL, created REAL NOT NULL, expires REAL, revoked INTEGER DEFAULT 0);
          CREATE INDEX IF NOT EXISTS sanctions_player ON sanctions(player, kind);
          CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY AUTOINCREMENT, reporter TEXT, target TEXT, reason TEXT, created REAL);
        """)
        core.db.commit()
        # Open freezes (no end yet) survive a supervisor restart.
        self.frozen = {r[0] for r in core.db.execute("SELECT player FROM sanctions WHERE kind='freeze' AND expires IS NULL AND revoked=0")}
        self.vanished = set()
        self.spectators = {}
        self.kicks = {}
        self.last_report = {}

    # ------------------------------------------------------------- helpers
    def is_staff(self, p):
        return self.core.role(p) in STAFF

    def staff_online(self):
        return [p for p in self.core.online if self.is_staff(p)]

    def notify_staff(self, text):
        for p in self.staff_online():
            self.core.notify(p, '<aqua>[Staff]</aqua> ' + text)

    def actor_name(self, actor):
        return 'Console' if actor == CONSOLE else self.core.name(actor)

    def require(self, p):
        if self.core.offline:
            raise RuleError("Outils staff désactivés en mode hors ligne (pseudos non vérifiés) : utiliser la CLI")
        if not self.is_staff(p):
            raise RuleError("Permission refusée : staff uniquement")

    def active(self, player, kind):
        return self.core.db.execute(
            "SELECT * FROM sanctions WHERE player=? AND kind=? AND revoked=0 AND (expires IS NULL OR expires>?) ORDER BY id DESC LIMIT 1",
            (player, kind, time.time())).fetchone()

    def sanction(self, actor, target, kind, reason, duration=None):
        now = time.time()
        with self.core.db:
            self.core.db.execute("INSERT INTO sanctions(player,kind,reason,actor,created,expires) VALUES(?,?,?,?,?,?)",
                                 (target, kind, reason, actor, now, None if duration is None else now + duration))
        self.core.log('sanction', kind=kind, player=target, actor=actor, reason=reason, duration=duration)

    def ban_message(self, row):
        end = 'définitif' if row['expires'] is None else 'jusqu’au ' + stamp(row['expires'])
        return (f"<red><bold>Tu es banni du serveur Rush</bold></red>\n\n<gray>Raison :</gray> <white>{esc(row['reason'])}</white>\n"
                f"<gray>Fin :</gray> <white>{end}</white>\n<gray>Par :</gray> <white>{self.actor_name(row['actor'])}</white>")

    # ------------------------------------------------------- server hooks
    def login(self, player, name):
        """Proxy login check (fails open on the proxy side if the API is unreachable)."""
        with self.core.lock:
            try:
                player = self.core.register(player, name)
            except RuleError:
                return {'allowed': False, 'reason': '<red>Pseudo invalide'}
            row = self.active(player, 'ban')
            return {'allowed': row is None, 'reason': self.ban_message(row) if row else ''}

    def admission(self, instance, player):
        with self.core.lock:
            return {'spectator': player in self.spectators.get(instance, set()) and self.core.routes.get(player) == instance}

    def state(self, players):
        """Polled every second by each Paper instance for the players it hosts."""
        with self.core.lock:
            muted = {}
            for p in players[:200]:
                row = self.active(p, 'mute')
                if row:
                    muted[p] = {'until': row['expires'], 'reason': row['reason']}
            return {'frozen': [p for p in players if p in self.frozen], 'vanished': sorted(self.vanished),
                    'staff': {p: self.core.role(p) for p in players if self.is_staff(p)}, 'muted': muted}

    def close_freeze(self, target):
        with self.core.db:
            self.core.db.execute("UPDATE sanctions SET expires=? WHERE player=? AND kind='freeze' AND expires IS NULL", (time.time(), target))

    def left(self, p):
        if p in self.frozen:
            name = self.core.name(p)
            self.core.record_note(p, 'deconnexion_pendant_freeze', joueur=name)
            self.notify_staff(f"<red>⚠ <yellow>{name}</yellow> s’est déconnecté pendant un freeze.</red> "
                              + button('Bannir 7 j', f'/ban {name} 7d Déconnexion pendant un freeze', 'red', 'Bannir 7 jours')
                              + ' ' + button('Historique', f'/history {name}', 'gray', 'Sanctions'))

    def release(self, mid):
        self.spectators.pop(mid, None)

    def stop_spectating(self, p):
        for viewers in self.spectators.values():
            viewers.discard(p)

    def pop_kicks(self):
        kicks, self.kicks = self.kicks, {}
        return kicks

    # ------------------------------------------------------------ commands
    def command(self, p, action, args):
        if action == 'report':
            return self.report(p, args)
        self.require(p)
        if action == 'staff':
            return self.overview()
        if action == 'vanish':
            if p in self.vanished:
                self.vanished.discard(p)
                return self.core.message('<gray>Vanish</gray> <red>désactivé</red> <gray>: les joueurs te voient.</gray>')
            self.vanished.add(p)
            return self.core.message('<gray>Vanish</gray> <green>activé</green> <gray>: invisible des joueurs (TAB compris).</gray>')
        if action == 'sc':
            if not args:
                raise RuleError("/sc <message>")
            self.notify_staff(f"<white>{self.core.name(p)}</white> <dark_gray>»</dark_gray> <aqua>{esc(' '.join(args))}</aqua>")
            return self.core.message('<gray>Message envoyé au staff.</gray>')
        if action == 'spec':
            return self.spectate(p, args)
        if not args:
            raise RuleError(f"/{action} <pseudo>")
        target = self.core.target(args[0])
        name = self.core.name(target)
        if action == 'history':
            return self.history(target)
        if target != p and self.is_staff(target) and self.core.role(p) != 'admin':
            raise RuleError("Seul un administrateur peut sanctionner un membre du staff")
        if action == 'freeze':
            return self.freeze(p, target)
        if action in ('unban', 'unmute'):
            kind = action[2:]
            with self.core.db:
                count = self.core.db.execute("UPDATE sanctions SET revoked=1 WHERE player=? AND kind=? AND revoked=0 AND (expires IS NULL OR expires>?)",
                                             (target, kind, time.time())).rowcount
            if not count:
                raise RuleError(f"{name} n’a pas de {kind} actif")
            self.core.log('sanction_revoked', kind=kind, player=target, actor=p)
            if kind == 'mute':
                self.core.notify(target, '<green>Tu peux de nouveau écrire dans le chat.</green>')
            self.notify_staff(f"<white>{self.core.name(p)}</white> <gray>a levé le {kind} de</gray> <yellow>{name}</yellow><gray>.</gray>")
            return self.core.message(f"<green>{kind.capitalize()} de {name} levé.</green>")
        return self.punish(p, action, target, args[1:])

    def punish(self, actor, kind, target, rest):
        name = self.core.name(target)
        duration = None
        if kind in ('ban', 'mute'):
            if not rest:
                raise RuleError(f"/{kind} <pseudo> <30m|2h|7d|perm> [raison]")
            duration = parse_duration(rest[0])
            rest = rest[1:]
            if kind == 'ban' and actor != CONSOLE and self.core.role(actor) != 'admin' and (duration is None or duration > MODERATOR_MAX_BAN):
                raise RuleError("Un modérateur peut bannir 7 jours au maximum")
        reason = ' '.join(rest).strip() or 'Décision du staff'
        if kind == 'warn' and not rest:
            raise RuleError("/warn <pseudo> <raison>")
        self.sanction(actor, target, kind, reason, duration)
        by = self.actor_name(actor)
        self.core.record_note(target, kind, joueur=name, par=by, raison=reason, duree=human(duration) if kind in ('ban', 'mute') else None)
        if kind == 'warn':
            self.core.notify(target, f"<red><bold>⚠ Avertissement du staff</bold></red> <gray>:</gray> <white>{esc(reason)}</white>")
        elif kind == 'mute':
            self.core.notify(target, f"<red>Tu es réduit au silence</red> <gray>({human(duration)}) :</gray> <white>{esc(reason)}</white>")
        elif kind == 'kick':
            self.kicks[target] = f"<red><bold>Expulsé du serveur</bold></red>\n\n<gray>Raison :</gray> <white>{esc(reason)}</white>\n<gray>Par :</gray> <white>{by}</white>"
        elif kind == 'ban':
            self.kicks[target] = self.ban_message(self.active(target, 'ban'))
        if kind in ('kick', 'ban') and target in self.frozen:
            self.frozen.discard(target)
            self.close_freeze(target)
        label = {'warn': 'averti', 'mute': 'muté', 'kick': 'expulsé', 'ban': 'banni'}[kind]
        span = f" <gray>({human(duration)})</gray>" if kind in ('ban', 'mute') else ''
        self.notify_staff(f"<white>{by}</white> <gray>a {label}</gray> <yellow>{name}</yellow>{span} <dark_gray>:</dark_gray> <white>{esc(reason)}</white>")
        return self.core.message(f"<green>{name} {label}.</green>")

    def freeze(self, actor, target):
        name = self.core.name(target)
        if target in self.frozen:
            self.frozen.discard(target)
            self.close_freeze(target)
            self.core.record_note(target, 'degel', joueur=name, par=self.core.name(actor))
            self.core.notify(target, '<green><bold>Tu n’es plus gelé.</bold></green> <gray>Merci de ta patience.</gray>')
            self.notify_staff(f"<white>{self.core.name(actor)}</white> <gray>a dégelé</gray> <yellow>{name}</yellow><gray>.</gray>")
            self.core.log('unfreeze', player=target, actor=actor)
            return self.core.message(f'<green>{name} dégelé.</green>')
        if target not in self.core.online:
            raise RuleError("Ce joueur n’est pas connecté")
        self.frozen.add(target)
        self.sanction(actor, target, 'freeze', 'Contrôle du staff')  # Open until the unfreeze (duration kept).
        self.core.record_note(target, 'freeze', joueur=name, par=self.core.name(actor))
        self.core.notify(target, '<aqua><bold>❄ Tu es gelé par le staff.</bold></aqua> <red>Ne te déconnecte pas</red> <gray>et attends les instructions (une déconnexion peut entraîner un bannissement).</gray>')
        self.notify_staff(f"<white>{self.core.name(actor)}</white> <gray>a gelé</gray> <yellow>{name}</yellow><gray>.</gray> "
                          + button('Dégeler', f'/freeze {name}', 'green', 'Dégeler') + ' ' + button('Spec', f'/spec {name}', 'aqua', 'Rejoindre sa partie'))
        self.core.log('freeze', player=target, actor=actor)
        return self.core.message(f"<aqua>{name} est gelé.</aqua> " + button('Dégeler', f'/freeze {name}', 'green', 'Dégeler'))

    def spectate(self, p, args):
        core = self.core
        if not args:
            raise RuleError("/spec <pseudo|id de partie>")
        mid = args[0] if args[0] in core.active else core.routes.get(core.target(args[0])) if not args[0].startswith('rush-') else None
        if mid not in core.active:
            raise RuleError("Aucune partie en cours pour cette cible")
        if core.active[mid]['state'] != 'ready':
            raise RuleError("La partie démarre, réessaie dans quelques secondes")
        if core.routes.get(p) != mid:
            if any(p in q['players'] for q in core.queue) or any(p in r['players'] for r in core.rooms.values()):
                raise RuleError("Quitte la file ou ton salon d’abord")
            if any(p in team for m in core.active.values() for team in m['teams']):
                raise RuleError("Tu joues une partie : impossible de la regarder en même temps")
        self.stop_spectating(p)
        self.spectators.setdefault(mid, set()).add(p)
        self.vanished.add(p)
        core.routes[p] = mid
        m = core.active[mid]
        core.log('spectate', player=p, match=mid)
        if m.get('record') and core.history:
            core.history.note(m['record'], 'spectateur_staff', joueur=core.name(p))
        return {'message': f"<aqua>Spectateur</aqua> <gray>de</gray> <white>{mid}</white> <gray>({m['mode']} • {m['map']['name']}) — vanish activé, transfert…</gray>",
                'lines': ['<gray>/rush leave pour revenir au lobby.</gray>']}

    def history(self, target):
        rows = self.core.db.execute("SELECT * FROM sanctions WHERE player=? ORDER BY id DESC LIMIT 15", (target,)).fetchall()
        now = time.time()
        lines = []
        for r in rows:
            live = not r['revoked'] and (r['expires'] is None or r['expires'] > now) and r['kind'] in ('ban', 'mute', 'freeze')
            if r['kind'] in ('warn', 'kick'):
                span = ''
            elif r['kind'] == 'freeze':
                span = f" <gray>({'en cours' if r['expires'] is None else human(r['expires'] - r['created'])})</gray>"
            else:
                span = f" <gray>({human(None if r['expires'] is None else r['expires'] - r['created'])})</gray>"
            state = ' <red>● actif</red>' if live else (' <dark_gray>levé</dark_gray>' if r['revoked'] else '')
            lines.append(f"<dark_gray>{stamp(r['created'])}</dark_gray> <yellow>{r['kind']}</yellow>{span}{state} <gray>par</gray> <white>{self.actor_name(r['actor'])}</white> "
                         f"<dark_gray>:</dark_gray> <white>{esc(r['reason'])}</white>")
        return {'message': f"<gold>Sanctions de</gold> <yellow>{self.core.name(target)}</yellow> <gray>({len(rows)})</gray>",
                'lines': lines or ['<gray>Aucune sanction.</gray>']}

    def report(self, p, args):
        if len(args) < 2:
            raise RuleError("/report <pseudo> <raison>")
        target = self.core.target(args[0])
        if target == p:
            raise RuleError("Impossible de te signaler toi-même")
        if time.time() - self.last_report.get(p, 0) < REPORT_COOLDOWN:
            raise RuleError("Un signalement par minute maximum")
        self.last_report[p] = time.time()
        reason = ' '.join(args[1:])
        with self.core.db:
            self.core.db.execute("INSERT INTO reports(reporter,target,reason,created) VALUES(?,?,?,?)", (p, target, reason, time.time()))
        name = self.core.name(target)
        where = self.core.routes.get(target, 'lobby')
        self.notify_staff(f"<red>Signalement</red> <gray>de</gray> <white>{self.core.name(p)}</white> <gray>contre</gray> <yellow>{name}</yellow> "
                          f"<dark_gray>({where})</dark_gray> <dark_gray>:</dark_gray> <white>{esc(reason)}</white> "
                          + button('Spec', f'/spec {name}', 'aqua', 'Rejoindre sa partie') + ' '
                          + button('Freeze', f'/freeze {name}', 'blue', 'Geler le joueur') + ' '
                          + button('Historique', f'/history {name}', 'gray', 'Sanctions'))
        self.core.log('report', reporter=p, player=target, reason=reason)
        staff = len(self.staff_online())
        return self.core.message(f"<green>Signalement envoyé.</green> <gray>{staff} membre(s) du staff en ligne.</gray>")

    def overview(self):
        core = self.core
        now = time.time()
        matches = [{'id': mid, 'mode': m['mode'], 'map': m['map']['name'], 'state': m['state'], 'ranked': m['ranked'],
                    'elapsed': int(now - m['created']), 'spectators': len(self.spectators.get(mid, ())),
                    'teams': [[core.name(u) for u in team] for team in m['teams']]} for mid, m in core.active.items()]
        players = [{'name': core.name(p), 'role': core.role(p), 'server': core.routes.get(p, 'lobby'), 'frozen': p in self.frozen,
                    'muted': self.active(p, 'mute') is not None, 'vanished': p in self.vanished} for p in core.online]
        players.sort(key=lambda r: (r['role'] == 'player', r['name'].lower()))
        reports = [{'reporter': core.name(r['reporter']), 'target': core.name(r['target']), 'reason': r['reason'][:80],
                    'ago': human(now - r['created'])} for r in core.db.execute("SELECT * FROM reports ORDER BY id DESC LIMIT 14")]
        return {'message': '<aqua><bold>Staff</bold></aqua>', 'lines': [], 'matches': matches, 'players': players, 'reports': reports}

    def console(self, action, name, args):
        """CLI moderation (works in offline mode and without any staff online)."""
        with self.core.lock:
            if action not in ('ban', 'unban', 'kick', 'mute', 'unmute', 'warn', 'history'):
                raise RuleError("Action : ban, unban, kick, mute, unmute, warn, history")
            target = self.core.target(name)
            if action == 'history':
                return self.history(target)
            if action in ('unban', 'unmute'):
                with self.core.db:
                    self.core.db.execute("UPDATE sanctions SET revoked=1 WHERE player=? AND kind=? AND revoked=0", (target, action[2:]))
                self.core.log('sanction_revoked', kind=action[2:], player=target, actor=CONSOLE)
                return self.core.message(f"{action[2:]} levé")
            return self.punish(CONSOLE, action, target, args)
