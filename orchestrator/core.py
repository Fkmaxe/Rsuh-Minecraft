"""Serialized matchmaking, social state, notifications and atomic ranked results.

Player-facing texts are MiniMessage strings, rendered by the Paper plugin and the proxy.
Only validated values (player names, map names, room codes, modes) are interpolated.
"""
from __future__ import annotations

import itertools
import json
import re
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from . import maps as mapdata

MODES = {"1v1": 1, "2v2": 2, "5v5": 5}
ROLES = ('player', 'moderator', 'admin')
TEAM_MAX = 5
EDITOR_IDLE_SECONDS = 600
ABORT_REASONS = {
    'roster_join_timeout': 'un joueur ne s’est pas connecté à temps',
    'draw': 'match nul', 'draw_time_limit': 'temps écoulé : match nul',
    'moderation': 'annulée par la modération', 'process_failed_or_timeout': 'erreur du serveur de jeu',
}


class RuleError(ValueError):
    pass


def button(label, command, color, hover):
    """Clickable chat button; label, command and hover never contain quotes or tags."""
    return (f"<click:run_command:'{command}'><hover:show_text:\"<gray>{hover}\">"
            f"<{color}><bold>[{label}]</bold></{color}></hover></click>")


def side_name(side):
    return '<red>Rouge</red>' if side == 0 else '<blue>Bleu</blue>'


class Core:
    def __init__(self, db: Path, deployer, log=lambda *a, **k: None, offline=False, maps=None, stop_delay=3.0, history=None):
        # Offline mode: names are not verified, so nothing may depend on identity
        # (no ranked results, no in-game administration).
        self.offline = offline
        self.maps = maps
        self.history = history
        self.lock = threading.RLock()
        self.db = sqlite3.connect(db, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS players(id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'player');
          CREATE TABLE IF NOT EXISTS friends(a TEXT, b TEXT, accepted INTEGER DEFAULT 0, PRIMARY KEY(a,b));
          CREATE TABLE IF NOT EXISTS teams(owner TEXT, member TEXT PRIMARY KEY);
          CREATE TABLE IF NOT EXISTS invites(owner TEXT, target TEXT, PRIMARY KEY(owner,target));
          CREATE TABLE IF NOT EXISTS matches(id TEXT PRIMARY KEY, mode TEXT, ranked INTEGER, roster TEXT, state TEXT, winner INTEGER);
          CREATE TABLE IF NOT EXISTS stats(player TEXT, mode TEXT, wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
            kills INTEGER DEFAULT 0, deaths INTEGER DEFAULT 0, beds INTEGER DEFAULT 0, rating INTEGER DEFAULT 1000,
            PRIMARY KEY(player,mode));
        """)
        # Children are stopped on supervisor exit; unfinished results never count.
        interrupted = [r[0] for r in self.db.execute("SELECT id FROM matches WHERE state NOT IN ('finished','aborted')")]
        self.db.execute("UPDATE matches SET state='aborted' WHERE state NOT IN ('finished','aborted')")
        if history:
            history.mark_interrupted(interrupted)
        self.db.commit()
        self.deployer, self.log = deployer, log
        self.online = set()
        self.presence_seen = False
        self.queue = []
        self.rooms = {}
        self.routes = {}
        self.active = {}
        self.editors = {}
        self.notes = {}
        # A stopping Paper server kicks its players off the whole network: their route is removed
        # first so the proxy (1 s poll) moves them to the lobby, then the instance stops.
        self.stop_delay = stop_delay
        self.stopping = {}
        self.last_presence = time.monotonic()
        self.deployment_retry_at = 0
        from .moderation import Moderation  # Imports this module: loaded once Core exists.
        self.moderation = Moderation(self)

    # ---------------------------------------------------------------- identity
    def register(self, player, name):
        try:
            player = str(uuid.UUID(player))
        except (ValueError, TypeError, AttributeError):
            raise RuleError("UUID invalide")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,16}", name):
            raise RuleError("Pseudo invalide")
        self.db.execute("INSERT INTO players(id,name) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name", (player, name))
        self.db.commit()
        return player

    def target(self, name):
        # A name can have an online and an offline UUID: prefer the account connected now.
        rows = [r[0] for r in self.db.execute("SELECT id FROM players WHERE lower(name)=lower(?)", (name,))]
        if not rows:
            raise RuleError("Joueur inconnu : il doit se connecter au moins une fois")
        return next((uid for uid in rows if uid in self.online), rows[0])

    def name(self, uid):
        row = self.db.execute("SELECT name FROM players WHERE id=?", (uid,)).fetchone()
        return row[0] if row else uid

    def role(self, uid):
        row = self.db.execute("SELECT role FROM players WHERE id=?", (uid,)).fetchone()
        return row[0] if row else 'player'

    def party(self, player):
        row = self.db.execute("SELECT owner FROM teams WHERE member=?", (player,)).fetchone()
        if not row:
            return player, [player]
        return row[0], [r[0] for r in self.db.execute("SELECT member FROM teams WHERE owner=? ORDER BY member", (row[0],))]

    def friends_of(self, p):
        return [r['b'] if r['a'] == p else r['a'] for r in self.db.execute("SELECT * FROM friends WHERE accepted=1 AND (a=? OR b=?)", (p, p))]

    def busy(self, p):
        return (p in self.routes or any(p in q['players'] for q in self.queue)
                or any(p in r['players'] for r in self.rooms.values())
                or any(e['owner'] == p for e in self.editors.values()))

    def free(self, players):
        if any(self.busy(p) for p in players):
            raise RuleError("Un joueur est déjà en file, en partie, dans un salon privé ou dans l’éditeur")

    # ---------------------------------------------------------- notifications
    def notify(self, p, text):
        """Queue a chat message for an online player; the proxy delivers it wherever they are."""
        if p in self.online:
            box = self.notes.setdefault(p, [])
            if len(box) < 50:
                box.append(text)

    def notify_all(self, players, text, skip=None):
        for p in players:
            if p != skip:
                self.notify(p, text)

    def invite_buttons(self, kind, name):
        return (button('Accepter', f'/{kind} accept {name}', 'green', 'Accepter')
                + ' ' + button('Refuser', f'/{kind} deny {name}', 'red', 'Refuser'))

    def welcome(self, p):
        """Pending invitations shown on every connection, with their buttons."""
        for (owner,) in self.db.execute("SELECT owner FROM invites WHERE target=?", (p,)).fetchall():
            self.notify(p, f"<gold>✉</gold> <yellow>{self.name(owner)}</yellow> <gray>t’invite dans son équipe.</gray> "
                           + self.invite_buttons('team', self.name(owner)))
        for (a,) in self.db.execute("SELECT a FROM friends WHERE b=? AND accepted=0", (p,)).fetchall():
            self.notify(p, f"<aqua>✚</aqua> <yellow>{self.name(a)}</yellow> <gray>veut être ton ami.</gray> "
                           + self.invite_buttons('friend', self.name(a)))

    def presence(self, players):
        with self.lock:
            before = self.online
            self.online = {self.register(p['id'], p['name']) for p in players}
            self.last_presence = time.monotonic()
            self.queue = [q for q in self.queue if set(q['players']) <= self.online]
            for code, room in list(self.rooms.items()):
                if room['owner'] not in self.online or time.time() - room['created'] > 1800:
                    self.notify_all(room['players'], "<red>✘</red> <gray>Le salon privé</gray> <yellow>"+code+"</yellow> <gray>a été fermé.</gray>", room['owner'])
                    del self.rooms[code]
                else:
                    room['players'] = [p for p in room['players'] if p in self.online]
            if not self.presence_seen:  # First report after (re)start: no join/leave spam.
                self.presence_seen = True
                return
            hidden = self.moderation.vanished
            for p in self.online - before:
                if p not in hidden:
                    for f in self.friends_of(p):
                        self.notify(f, f"<green>●</green> <yellow>{self.name(p)}</yellow> <gray>est en ligne.</gray>")
                self.welcome(p)
            for p in before - self.online:
                if p not in hidden:
                    for f in self.friends_of(p):
                        self.notify(f, f"<red>●</red> <yellow>{self.name(p)}</yellow> <gray>s’est déconnecté.</gray>")
                self.moderation.left(p)
                self.notes.pop(p, None)

    # ---------------------------------------------------------------- commands
    def command(self, player, name, action, args):
        with self.lock:
            p = self.register(player, name)
            args = [str(a) for a in args]
            if action in self.moderation_actions():
                return self.moderation.command(p, action, args)
            if action == 'queue':
                mode = args[0] if args else ''
                if mode not in MODES:
                    raise RuleError("Mode : 1v1, 2v2 ou 5v5")
                owner, members = self.party(p)
                if owner != p:
                    raise RuleError("Seul le chef peut inscrire l’équipe")
                if len(members) > MODES[mode]:
                    raise RuleError("Équipe trop grande pour ce mode")
                if not set(members) <= self.online:
                    raise RuleError("Tous les membres doivent être connectés")
                self.free(members)
                self.queue.append(dict(owner=p, players=members, mode=mode, time=time.time()))
                self.log('queue_join', player=p, mode=mode, size=len(members))
                self.notify_all(members, f"<yellow>⏳</yellow> <gray>Ton chef a inscrit l’équipe en</gray> <gold>{mode}</gold><gray>.</gray>", p)
                waiting = sum(len(q['players']) for q in self.queue if q['mode'] == mode)
                ranked = '<gray>non classé : mode hors ligne</gray>' if self.offline else '<gray>classé</gray>'
                return {'message': f"<green>Recherche d’une partie</green> <gold><bold>{mode}</bold></gold> <dark_gray>•</dark_gray> {ranked}",
                        'lines': [f"<gray>{waiting}/{MODES[mode]*2} joueurs en file.</gray> " + button('Quitter', '/rush leave', 'red', 'Quitter la file')]}
            if action == 'leave':
                left = [q for q in self.queue if p in q['players']]
                self.queue = [q for q in self.queue if p not in q['players']]
                for q in left:
                    self.notify_all(q['players'], f"<red>✘</red> <yellow>{self.name(p)}</yellow> <gray>a quitté la file : inscription annulée.</gray>", p)
                for code, room in list(self.rooms.items()):
                    if p == room['owner']:
                        self.notify_all(room['players'], "<red>✘</red> <gray>Le créateur a fermé le salon privé.</gray>", p)
                        del self.rooms[code]
                    elif p in room['players']:
                        room['players'].remove(p)
                        self.notify_all(room['players'], f"<red>-</red> <yellow>{self.name(p)}</yellow> <gray>a quitté le salon.</gray>")
                # During a match, proxy transfers to lobby and game treats this as forfeit.
                self.routes.pop(p, None)
                self.moderation.stop_spectating(p)
                return self.message("<gray>Retour au lobby.</gray>")
            if action == 'team':
                return self.team(p, args)
            if action == 'friend':
                return self.friend(p, args)
            if action == 'private':
                return self.private(p, args)
            if action == 'map':
                return self.map_command(p, args)
            if action == 'leaderboard':
                mode = args[0] if args else 'global'
                if mode not in (*MODES, 'global'):
                    raise RuleError("Classement : global, 1v1, 2v2, 5v5")
                rows = self.db.execute("SELECT p.name,s.* FROM stats s JOIN players p ON p.id=s.player WHERE mode=? ORDER BY rating DESC,wins DESC,player LIMIT 10", (mode,)).fetchall()
                medals = ['<gold>①</gold>', '<gray>②</gray>', '<#cd7f32>③</#cd7f32>']
                return {'message': f'<gold><bold>Classement {mode}</bold></gold>',
                        'lines': [f"{medals[i] if i < 3 else '<dark_gray>'+str(i+1)+'.</dark_gray>'} <white>{r['name']}</white> <dark_gray>—</dark_gray> "
                                  f"<gold>{r['rating']}</gold> <gray>Elo</gray> <dark_gray>|</dark_gray> <green>{r['wins']}V</green> <red>{r['losses']}D</red>"
                                  for i, r in enumerate(rows)] or ['<gray>Aucune partie classée pour l’instant.</gray>'],
                        'rows': [{'name': r['name'], 'rating': r['rating'], 'wins': r['wins'], 'losses': r['losses'],
                                  'kills': r['kills'], 'deaths': r['deaths'], 'beds': r['beds']} for r in rows]}
            if action == 'status':
                owner, members = self.party(p)
                role = self.role(p)
                return {'message': '<gold><bold>Rush</bold></gold>',
                        'lines': ['<gray>Rôle :</gray> <white>'+role+'</white>', '<gray>Équipe :</gray> <white>'+', '.join(map(self.name, members))+'</white>',
                                  f"<gray>File :</gray> <white>{sum(len(q['players']) for q in self.queue)} joueurs</white>", f"<gray>Parties :</gray> <white>{len(self.active)}</white>"]
                        + (['<red>Mode hors ligne : parties non classées</red>'] if self.offline else []), 'role': role}
            if action == 'players':
                staff = self.moderation.is_staff(p)
                names = sorted(self.name(uid) for uid in self.online if uid != p and (staff or uid not in self.moderation.vanished))
                return {'message': '<gray>Joueurs connectés</gray>', 'lines': names, 'players': names}
            if action == 'admin':
                if self.offline:
                    raise RuleError("Administration en jeu désactivée en mode hors ligne (pseudos non vérifiés) : utiliser la CLI")
                role = self.role(p)
                if role not in ('moderator', 'admin'):
                    raise RuleError("Permission refusée")
                if args and args[0] == 'abort' and len(args) == 2:
                    self.abort(args[1], 'moderation')
                    return self.message("<green>Partie annulée.</green>")
                if role == 'admin' and len(args) == 3 and args[0] == 'role':
                    self.set_role(self.target(args[1]), args[2])
                    return self.message("<green>Rôle modifié.</green>")
                if args:
                    raise RuleError("Permission refusée ou syntaxe invalide : /rush admin [abort <id>|role <pseudo> <rôle>]")
                return {'message': '<gold>Administration</gold>',
                        'lines': [f"<white>{mid}</white> <gray>{m['mode']} • {m['map']['name']} • {m['state']}</gray> "
                                  + button('Annuler', f'/rush admin abort {mid}', 'red', 'Annuler sans résultat') for mid, m in self.active.items()]
                        or ['<gray>Aucune partie en cours.</gray>']}
            raise RuleError("Commande inconnue")

    @staticmethod
    def moderation_actions():
        from .moderation import ACTIONS
        return ACTIONS

    @staticmethod
    def message(text):
        return {'message': text, 'lines': []}

    def set_role(self, p, role):
        if role not in ROLES:
            raise RuleError("Rôle : player, moderator, admin")
        with self.lock, self.db:
            self.db.execute("UPDATE players SET role=? WHERE id=?", (role, p))
            self.log('role_changed', player=p, role=role)

    # -------------------------------------------------------------------- team
    def team(self, p, args):
        sub = args[0] if args else 'list'
        owner, members = self.party(p)
        has_team = self.db.execute("SELECT 1 FROM teams WHERE member=?", (p,)).fetchone() is not None
        if sub == 'list':
            invites = [self.name(r[0]) for r in self.db.execute("SELECT owner FROM invites WHERE target=?", (p,))]
            lines = [f"{'<gold>♛</gold>' if uid == owner else '<gray>•</gray>'} <white>{self.name(uid)}</white> "
                     f"{'<green>en ligne</green>' if uid in self.online else '<dark_gray>hors ligne</dark_gray>'}" for uid in members] if has_team else []
            lines += [f"<gold>✉</gold> <yellow>{n}</yellow> <gray>t’invite.</gray> " + self.invite_buttons('team', n) for n in invites]
            return {'message': ('<gold>Équipe de</gold> <yellow>'+self.name(owner)+'</yellow> <gray>('+str(len(members))+'/5)</gray>') if has_team
                    else '<gray>Tu n’es dans aucune équipe.</gray> <dark_gray>/team invite \\<pseudo></dark_gray>',
                    'lines': lines,
                    'members': [{'name': self.name(uid), 'owner': uid == owner, 'online': uid in self.online} for uid in members] if has_team else [],
                    'invites': invites, 'leader': owner == p,
                    'actions': [{'name': n, 'label': 'Accepter '+n, 'action': 'accept'} for n in invites]
                    + ([{'name': self.name(uid), 'label': 'Retirer '+self.name(uid), 'action': 'kick'} for uid in members if uid != p] if owner == p and has_team else [])}
        if sub == 'deny' and len(args) == 2:
            target = self.target(args[1])
            with self.db:
                if not self.db.execute("DELETE FROM invites WHERE owner=? AND target=?", (target, p)).rowcount:
                    raise RuleError("Invitation introuvable")
            self.notify(target, f"<red>✘</red> <yellow>{self.name(p)}</yellow> <gray>a refusé ton invitation d’équipe.</gray>")
            return self.message("<gray>Invitation refusée.</gray>")
        self.free(members)
        if sub == 'invite' and len(args) == 2:
            if owner != p:
                raise RuleError("Seul le chef peut inviter")
            target = self.target(args[1])
            if target == p or target in members or len(members) >= TEAM_MAX:
                raise RuleError("Invitation impossible (5 membres maximum)")
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO teams VALUES(?,?)", (p, p))
                self.db.execute("INSERT OR IGNORE INTO invites VALUES(?,?)", (p, target))
            self.notify(target, f"<gold>✉</gold> <yellow>{self.name(p)}</yellow> <gray>t’invite dans son équipe.</gray> " + self.invite_buttons('team', self.name(p)))
            status = '' if target in self.online else ' <dark_gray>(hors ligne : il la verra à sa connexion)</dark_gray>'
            return self.message(f"<green>Invitation envoyée à</green> <yellow>{self.name(target)}</yellow><green>.</green>{status}")
        if sub == 'accept' and len(args) == 2:
            target = self.target(args[1])
            if has_team:
                raise RuleError("Quittez votre équipe actuelle")
            if not self.db.execute("SELECT 1 FROM invites WHERE owner=? AND target=?", (target, p)).fetchone():
                raise RuleError("Invitation introuvable")
            other_owner, other = self.party(target)
            self.free(other)
            if other_owner != target or len(other) >= TEAM_MAX:
                raise RuleError("Équipe indisponible")
            with self.db:
                self.db.execute("INSERT INTO teams VALUES(?,?)", (target, p))
                self.db.execute("DELETE FROM invites WHERE target=?", (p,))
            self.notify_all(other, f"<green>+</green> <yellow>{self.name(p)}</yellow> <gray>a rejoint l’équipe ({len(other)+1}/5).</gray>")
            return self.message(f"<green>Tu as rejoint l’équipe de</green> <yellow>{self.name(target)}</yellow><green>.</green>")
        if sub == 'leave':
            if not has_team:
                raise RuleError("Tu n’es dans aucune équipe")
            with self.db:
                if owner == p:
                    self.db.execute("DELETE FROM teams WHERE owner=?", (p,))
                    self.db.execute("DELETE FROM invites WHERE owner=?", (p,))
                else:
                    self.db.execute("DELETE FROM teams WHERE member=?", (p,))
            if owner == p:
                self.notify_all(members, f"<red>✘</red> <yellow>{self.name(p)}</yellow> <gray>a dissous l’équipe.</gray>", p)
            else:
                self.notify_all(members, f"<red>-</red> <yellow>{self.name(p)}</yellow> <gray>a quitté l’équipe.</gray>", p)
            return self.message("<gray>Équipe quittée.</gray>")
        if sub == 'kick' and len(args) == 2 and owner == p:
            target = self.target(args[1])
            if target == p:
                raise RuleError("Utilisez /team leave")
            with self.db:
                if not self.db.execute("DELETE FROM teams WHERE owner=? AND member=?", (p, target)).rowcount:
                    raise RuleError("Ce joueur n’est pas dans ton équipe")
            self.notify(target, f"<red>✘</red> <gray>Tu as été retiré de l’équipe de</gray> <yellow>{self.name(p)}</yellow><gray>.</gray>")
            self.notify_all([m for m in members if m != target], f"<red>-</red> <yellow>{self.name(target)}</yellow> <gray>a été retiré de l’équipe.</gray>", p)
            return self.message("<gray>Membre retiré.</gray>")
        raise RuleError("/team invite|accept|deny|kick <pseudo>, /team leave, /team list")

    # ------------------------------------------------------------------ friend
    def friend(self, p, args):
        sub = args[0] if args else 'list'
        if sub == 'list':
            rows = self.db.execute("SELECT * FROM friends WHERE a=? OR b=?", (p, p)).fetchall()
            lines, actions, friends = [], [], []
            for r in rows:
                other = r['b'] if r['a'] == p else r['a']
                n = self.name(other)
                if r['accepted']:
                    status = 'online' if other in self.online else 'offline'
                    lines.append(f"{'<green>●</green>' if status == 'online' else '<red>●</red>'} <white>{n}</white> "
                                 f"{'<green>connecté</green>' if status == 'online' else '<dark_gray>hors ligne</dark_gray>'}")
                    actions.append({'name': n, 'label': 'Retirer '+n, 'action': 'remove'})
                elif r['b'] == p:
                    status = 'incoming'
                    lines.append(f"<aqua>✚</aqua> <yellow>{n}</yellow> <gray>demande reçue</gray> " + self.invite_buttons('friend', n))
                    actions.append({'name': n, 'label': 'Accepter '+n, 'action': 'accept'})
                else:
                    status = 'outgoing'
                    lines.append(f"<gray>…</gray> <white>{n}</white> <dark_gray>demande envoyée</dark_gray>")
                    actions.append({'name': n, 'label': 'Retirer '+n, 'action': 'remove'})
                friends.append({'name': n, 'status': status})
            order = {'incoming': 0, 'online': 1, 'offline': 2, 'outgoing': 3}
            friends.sort(key=lambda f: (order[f['status']], f['name'].lower()))
            online = sum(f['status'] == 'online' for f in friends)
            return {'message': f"<aqua><bold>Amis</bold></aqua> <gray>({online} en ligne)</gray>", 'lines': lines, 'actions': actions, 'friends': friends}
        if len(args) != 2:
            raise RuleError("/friend add|accept|deny|remove <pseudo>")
        target = self.target(args[1])
        if target == p:
            raise RuleError("Choisissez un autre joueur")
        with self.db:
            if sub == 'add':
                reverse = self.db.execute("SELECT accepted FROM friends WHERE a=? AND b=?", (target, p)).fetchone()
                if reverse and not reverse[0]:  # They already asked: adding back accepts.
                    self.db.execute("UPDATE friends SET accepted=1 WHERE a=? AND b=?", (target, p))
                    self.notify(target, f"<green>✔</green> <yellow>{self.name(p)}</yellow> <gray>a accepté ta demande d’ami.</gray>")
                    return self.message(f"<green>Tu es maintenant ami avec</green> <yellow>{self.name(target)}</yellow><green>.</green>")
                if self.db.execute("SELECT 1 FROM friends WHERE (a=? AND b=?) OR (a=? AND b=?)", (p, target, target, p)).fetchone():
                    raise RuleError("Demande ou amitié existante")
                self.db.execute("INSERT INTO friends(a,b) VALUES(?,?)", (p, target))
                self.notify(target, f"<aqua>✚</aqua> <yellow>{self.name(p)}</yellow> <gray>veut être ton ami.</gray> " + self.invite_buttons('friend', self.name(p)))
                return self.message(f"<green>Demande d’ami envoyée à</green> <yellow>{self.name(target)}</yellow><green>.</green>")
            if sub == 'accept':
                result = self.db.execute("UPDATE friends SET accepted=1 WHERE a=? AND b=? AND accepted=0", (target, p))
                if not result.rowcount:
                    raise RuleError("Aucune demande reçue de ce joueur")
                self.notify(target, f"<green>✔</green> <yellow>{self.name(p)}</yellow> <gray>a accepté ta demande d’ami.</gray>")
                return self.message(f"<green>Tu es maintenant ami avec</green> <yellow>{self.name(target)}</yellow><green>.</green>")
            if sub == 'deny':
                if not self.db.execute("DELETE FROM friends WHERE a=? AND b=? AND accepted=0", (target, p)).rowcount:
                    raise RuleError("Aucune demande reçue de ce joueur")
                return self.message("<gray>Demande refusée.</gray>")
            if sub == 'remove':
                if not self.db.execute("DELETE FROM friends WHERE (a=? AND b=?) OR (a=? AND b=?)", (p, target, target, p)).rowcount:
                    raise RuleError("Ce joueur n’est pas dans ta liste")
                return self.message(f"<gray>{self.name(target)} a été retiré de tes amis.</gray>")
        raise RuleError("Action ami inconnue")

    # ----------------------------------------------------------------- private
    def room_view(self, code, room):
        return {'code': code, 'mode': room['mode'], 'owner': self.name(room['owner']), 'size': MODES[room['mode']] * 2,
                'players': [self.name(v) for v in room['players']]}

    def private(self, p, args):
        sub = args[0] if args else 'list'
        if sub == 'create' and len(args) == 2:
            mode = args[1]
            if mode not in MODES:
                raise RuleError("Mode invalide")
            self.free([p])
            code = secrets.token_hex(3).upper()
            self.rooms[code] = dict(owner=p, players=[p], mode=mode, created=time.time())
            return {'message': f"<light_purple>Salon privé</light_purple> <white><bold>{code}</bold></white> <gray>créé ({mode}).</gray>",
                    'lines': ["<gray>Invite tes amis :</gray> <white>/private invite \\<pseudo></white>"], 'room': self.room_view(code, self.rooms[code])}
        if sub == 'join' and len(args) == 2:
            self.free([p])
            code = args[1].upper()
            room = self.rooms.get(code)
            if not room or len(room['players']) >= MODES[room['mode']] * 2:
                raise RuleError("Salon inexistant ou complet")
            room['players'].append(p)
            size = MODES[room['mode']] * 2
            self.notify_all(room['players'], f"<green>+</green> <yellow>{self.name(p)}</yellow> <gray>a rejoint le salon ({len(room['players'])}/{size}).</gray>", p)
            if len(room['players']) == size:
                self.notify(room['owner'], "<green>Le salon est complet !</green> " + button('Démarrer', '/private start', 'green', 'Lancer la partie privée'))
            side = side_name((len(room['players']) - 1) % 2)
            return {'message': f"<green>Salon</green> <white>{code}</white> <green>rejoint</green> <gray>— équipe</gray> {side}<gray>.</gray>", 'lines': [],
                    'room': self.room_view(code, room)}
        room_entry = next(((code, r) for code, r in self.rooms.items() if p in r['players']), None)
        if not room_entry:
            raise RuleError("/private create <mode>, /private join <code>")
        code, room = room_entry
        if sub == 'invite' and len(args) == 2:
            target = self.target(args[1])
            if target not in self.online:
                raise RuleError("Ce joueur n’est pas connecté")
            if target in room['players']:
                raise RuleError("Ce joueur est déjà dans le salon")
            self.notify(target, f"<light_purple>✉</light_purple> <yellow>{self.name(p)}</yellow> <gray>t’invite dans son salon privé</gray> <gold>{room['mode']}</gold><gray>.</gray> "
                        + button('Rejoindre', f'/private join {code}', 'green', 'Rejoindre le salon ' + code))
            return self.message(f"<green>Invitation envoyée à</green> <yellow>{self.name(target)}</yellow><green>.</green>")
        if sub == 'start':
            if p != room['owner']:
                raise RuleError("Seul le créateur peut démarrer")
            if len(room['players']) != MODES[room['mode']] * 2 or not set(room['players']) <= self.online:
                raise RuleError("Le salon doit être complet et connecté")
            self.launch(room['mode'], [room['players'][::2], room['players'][1::2]], False)
            del self.rooms[code]
            return self.message("<green>Partie privée en préparation</green> <gray>(non classée).</gray>")
        view = self.room_view(code, room)
        return {'message': f"<light_purple>Salon</light_purple> <white><bold>{code}</bold></white> <gray>{room['mode']} ({len(room['players'])}/{view['size']})</gray>",
                'lines': [f"{side_name(i % 2)} <dark_gray>»</dark_gray> <white>{self.name(v)}</white>" for i, v in enumerate(room['players'])],
                'room': view}

    # -------------------------------------------------------------------- maps
    def map_command(self, p, args):
        if self.offline:
            raise RuleError("Édition des maps désactivée en mode hors ligne (pseudos non vérifiés)")
        if self.role(p) != 'admin':
            raise RuleError("Permission refusée : administrateurs uniquement")
        if not self.maps:
            raise RuleError("Catalogue de maps indisponible")
        sub = args[0] if args else 'list'
        try:
            if sub == 'list':
                rows = self.maps.summary()
                lines = []
                for r in rows:
                    state = '<green>✔ active</green>' if r['enabled'] else '<red>✘ inactive</red>'
                    issue = ' <dark_gray>(' + ', '.join(r['problems']) + ')</dark_gray>' if r['problems'] else ''
                    lines.append(f"<gold>{r['name']}</gold> {state} <gray>{','.join(r['modes'])}</gray>{issue} "
                                 + button('Éditer', '/map edit ' + r['name'], 'aqua', 'Ouvrir dans l’éditeur'))
                return {'message': '<gold><bold>Maps</bold></gold> <gray>(' + str(len(rows)) + ')</gray>', 'lines': lines, 'maps': rows}
            if sub == 'create' and len(args) in (2, 3):
                self.maps.create(args[1], args[2] if len(args) == 3 else 'classic')
                self.log('map_created', player=p, map=args[1])
                return {'message': f"<green>Map</green> <gold>{args[1]}</gold> <green>créée (inactive).</green>",
                        'lines': [button('Éditer', '/map edit ' + args[1], 'aqua', 'Ouvrir dans l’éditeur')]}
            if sub == 'edit' and len(args) == 2:
                return self.open_editor(p, args[1])
            if sub == 'delete' and len(args) in (2, 3):
                if any(e['map'] == args[1] for e in self.editors.values()):
                    raise RuleError("Map en cours d’édition")
                if len(args) == 2:
                    self.maps.load(args[1])
                    return self.message(f"<red>Supprimer définitivement</red> <gold>{args[1]}</gold> <red>?</red> "
                                        + button('Confirmer', f'/map delete {args[1]} confirm', 'red', 'Suppression définitive'))
                if args[2] != 'confirm':
                    raise RuleError("/map delete <nom> confirm")
                self.maps.delete(args[1])
                self.log('map_deleted', player=p, map=args[1])
                return self.message(f"<gray>Map {args[1]} supprimée.</gray>")
            if sub in ('enable', 'disable') and len(args) == 2:
                self.maps.set_enabled(args[1], sub == 'enable')
                return self.message(f"<gold>{args[1]}</gold> " + ('<green>activée.</green>' if sub == 'enable' else '<gray>désactivée.</gray>'))
            if sub == 'modes' and len(args) == 3:
                self.maps.set_modes(args[1], args[2].split(','))
                return self.message(f"<gold>{args[1]}</gold> <gray>: modes {args[2]}.</gray>")
        except ValueError as exc:
            raise RuleError(str(exc)) from exc
        raise RuleError("/map list|create <nom> [classic|vide|<map>]|edit <nom>|enable|disable <nom>|modes <nom> 1v1,2v2|delete <nom>")

    def open_editor(self, p, name, position=None):
        self.free([p])
        m = self.maps.load(name)
        if any(e['map'] == name for e in self.editors.values()):
            raise RuleError("Cette map est déjà ouverte dans l’éditeur")
        if not self.deployer.available():
            raise RuleError("Capacité atteinte, réessayez dans quelques instants")
        eid = 'edit-' + secrets.token_hex(5)
        try:
            self.deployer.start({'id': eid, 'editor': m, 'owner': p, 'world': self.maps.world(name), 'position': position})
        except (OSError, RuntimeError, StopIteration) as exc:
            self.deployer.stop(eid)
            raise RuleError("Éditeur indisponible : " + str(exc)) from exc
        self.editors[eid] = {'map': name, 'owner': p, 'seen': time.monotonic()}
        self.routes[p] = eid
        self.log('editor_opened', player=p, map=name, instance=eid)
        return self.message(f"<aqua>Ouverture de l’éditeur</aqua> <gold>{name}</gold><aqua>…</aqua> <gray>transfert automatique.</gray>")

    def editor_save(self, eid, data, quit=False, position=None):
        """Save = graceful stop of the editor, then copy its world once Paper has closed every file.

        Copying a running server's region files is unsafe (Paper keeps writing in the background),
        so the editor restarts on the saved map unless the admin is leaving.
        """
        with self.lock:
            editor = self.editors.get(eid)
            if not editor or editor.get('saving'):
                raise RuleError("Éditeur inconnu ou enregistrement déjà en cours")
            try:
                mapdata.sanitize(editor['map'], data)
            except ValueError as exc:
                raise RuleError(str(exc)) from exc
            editor['saving'] = True
            self.routes.pop(editor['owner'], None)
            name, owner = editor['map'], editor['owner']
            if not (isinstance(position, list) and len(position) == 5 and all(type(v) in (int, float) for v in position)):
                position = None

            def finished(folder, clean):
                with self.lock:
                    self.editors.pop(eid, None)
                    if not clean:
                        self.log('map_save_failed', map=name, reason='unclean_shutdown')
                        self.notify(owner, f"<red>✘ Enregistrement de</red> <gold>{name}</gold> <red>impossible : l’éditeur ne s’est pas arrêté proprement. La map précédente est conservée.</red>")
                        return
                    try:
                        saved = self.maps.commit(name, data, folder / 'world')
                    except (ValueError, OSError) as exc:
                        self.log('map_save_failed', map=name, error=str(exc))
                        self.notify(owner, f"<red>✘ Enregistrement impossible :</red> <gray>{type(exc).__name__}</gray>")
                        return
                    issues = mapdata.problems(saved)
                    self.log('map_saved', map=name, player=owner, world=saved['layout'] is None)
                    state = '<green>active en jeu</green>' if saved['enabled'] else '<gray>inactive</gray> ' + button('Activer', f'/map enable {name}', 'green', 'Mettre la map en rotation')
                    self.notify(owner, f"<green><bold>Map</bold></green> <gold>{name}</gold> <green><bold>enregistrée</bold></green> <dark_gray>•</dark_gray> {state}"
                                + (" <red>⚠ " + ', '.join(issues) + "</red>" if issues else ""))
                    if not quit:
                        try:
                            self.open_editor(owner, name, position)
                        except RuleError as exc:
                            self.notify(owner, f"<red>✘ Réouverture de l’éditeur impossible :</red> <gray>{exc}</gray>")

            self.stop_later(eid, finished)
            self.log('map_saving', map=name, player=owner, quit=quit)
            return {'ok': True}

    def editor_close(self, eid):
        with self.lock:
            editor = self.editors.pop(eid, None)
            if not editor:
                return {'ok': True}
            if self.routes.get(editor['owner']) == eid:
                del self.routes[editor['owner']]
            if not editor.get('saving'):
                self.stop_later(eid)
            self.log('editor_closed', map=editor['map'], instance=eid)
            return {'ok': True}

    # ----------------------------------------------------------------- matches
    def record_note(self, p, action, **fields):
        """Moderation action about a player currently in a game: kept in that game's history file."""
        match = self.active.get(self.routes.get(p))
        if match and match.get('record') and self.history:
            self.history.note(match['record'], action, **fields)

    def launch(self, mode, teams, ranked, record=True):
        if not self.deployer.available():
            raise RuleError("Capacité atteinte, réessayez dans quelques instants")
        ranked = ranked and not self.offline
        game_map, world = self.maps.choose(mode) if self.maps else (mapdata.CLASSIC, None)
        mid = 'rush-' + secrets.token_hex(6)
        match = dict(id=mid, mode=mode, teams=teams, ranked=ranked, state='starting', created=time.time(), map=game_map, world=world)
        # Reserve roster before spawning to make admission deterministic.
        try:
            self.deployer.start(match)
        except (OSError, RuntimeError, StopIteration) as exc:
            self.deployer.stop(mid)
            self.deployment_retry_at = time.monotonic() + 15
            self.log('deployment_failed', match=mid, error=str(exc))
            raise RuleError("Déploiement indisponible ; la file est conservée") from exc
        self.active[mid] = match
        if self.history and record:
            match['record'] = self.history.begin(match, self.name)
        for p in itertools.chain.from_iterable(teams):
            self.routes[p] = mid
        with self.db:
            self.db.execute("INSERT INTO matches VALUES(?,?,?,?,?,NULL)", (mid, mode, int(ranked), json.dumps(teams), 'starting'))
        self.log('match_starting', match=mid, mode=mode, ranked=ranked, teams=teams, map=game_map['name'])
        kind = 'classée' if ranked else 'non classée'
        for side, team in enumerate(teams):
            mates = ', '.join(self.name(u) for u in team)
            self.notify_all(team, f"<green><bold>Partie trouvée !</bold></green> <gold>{mode}</gold> <dark_gray>•</dark_gray> <gray>{kind} • carte</gray> "
                                  f"<white>{game_map['name']}</white> <dark_gray>•</dark_gray> <gray>équipe</gray> {side_name(side)} <dark_gray>({mates})</dark_gray>")
        return mid

    @staticmethod
    def packing(entries, size):
        # Bounded dynamic programming: keep groups intact, fill two exact teams.
        states = {(0, 0): ([], [])}
        for index, q in enumerate(entries):
            n = len(q['players'])
            for (a, b), (left, right) in list(states.items()):
                if a + n <= size:
                    states.setdefault((a + n, b), (left + [index], right))
                if b + n <= size:
                    states.setdefault((a, b + n), (left, right + [index]))
            if (size, size) in states:
                return states[(size, size)]
        return None

    def tick(self):
        with self.lock:
            if time.monotonic() - self.last_presence > 15:
                self.online.clear()
                self.queue.clear()
                self.presence_seen = False
            for mid, m in list(self.active.items()):
                status = self.deployer.status(mid)
                if status == 'ready' and m['state'] == 'starting':
                    m['state'] = 'ready'
                    with self.db:
                        self.db.execute("UPDATE matches SET state='ready' WHERE id=?", (mid,))
                    self.log('match_ready', match=mid)
                elif status == 'failed':
                    self.abort(mid, 'process_failed_or_timeout')
            now = time.monotonic()
            for name, (at, on_exit) in list(self.stopping.items()):
                if now >= at:
                    del self.stopping[name]
                    self.deployer.stop(name, on_exit=on_exit) if on_exit else self.deployer.stop(name)
            for eid, editor in list(self.editors.items()):
                if editor.get('saving'):
                    continue  # The exit callback finishes it.
                if editor['owner'] in self.online:
                    editor['seen'] = now
                if self.deployer.status(eid) == 'failed' or now - editor['seen'] > EDITOR_IDLE_SECONDS:
                    self.notify(editor['owner'], f"<red>✘</red> <gray>Éditeur de</gray> <gold>{editor['map']}</gold> <gray>fermé (inactivité ou erreur). Les modifications non enregistrées sont perdues.</gray>")
                    self.editor_close(eid)
            if time.monotonic() < self.deployment_retry_at:
                return
            for mode, size in MODES.items():
                while self.deployer.available():
                    entries = [q for q in self.queue if q['mode'] == mode]
                    packed = self.packing(entries, size)
                    if not packed:
                        break
                    left, right = packed
                    teams = [[p for i in side for p in entries[i]['players']] for side in (left, right)]
                    try:
                        self.launch(mode, teams, True)
                    except RuleError:
                        return
                    chosen = {id(entries[i]) for i in left + right}
                    self.queue = [q for q in self.queue if id(q) not in chosen]

    def routing(self):
        with self.lock:
            routes = {p: mid for p, mid in self.routes.items()
                      if (mid in self.active and self.active[mid]['state'] == 'ready')
                      or (mid in self.editors and not self.editors[mid].get('saving') and self.deployer.status(mid) == 'ready')}
            messages, self.notes = self.notes, {}
            return {'servers': self.deployer.servers(), 'routes': routes, 'messages': messages, 'kicks': self.moderation.pop_kicks()}

    def profiles(self, ids):
        """Data for the lobby TAB, scoreboard and menus."""
        with self.lock:
            order = [r[0] for r in self.db.execute("SELECT player FROM stats WHERE mode='global' ORDER BY rating DESC, wins DESC, player")]
            rank = {p: i + 1 for i, p in enumerate(order)}
            out = {}
            for raw in ids[:200]:
                try:
                    p = str(uuid.UUID(raw))
                except (ValueError, TypeError, AttributeError):
                    continue
                s = self.db.execute("SELECT * FROM stats WHERE player=? AND mode='global'", (p,)).fetchone()
                owner, members = self.party(p)
                queued = next((q for q in self.queue if p in q['players']), None)
                room = next((code for code, r in self.rooms.items() if p in r['players']), None)
                out[p] = {'role': self.role(p), 'rating': s['rating'] if s else 1000, 'rank': rank.get(p),
                          'wins': s['wins'] if s else 0, 'losses': s['losses'] if s else 0, 'kills': s['kills'] if s else 0,
                          'deaths': s['deaths'] if s else 0, 'beds': s['beds'] if s else 0,
                          'party': [self.name(m) for m in members] if len(members) > 1 else [], 'leader': owner == p,
                          'queue': {'mode': queued['mode'], 'since': queued['time']} if queued else None, 'room': room}
            queue = {mode: sum(len(q['players']) for q in self.queue if q['mode'] == mode) for mode in MODES}
            return {'network': {'online': len(self.online), 'matches': len(self.active), 'queue': queue,
                                'offline': self.offline, 'now': time.time()}, 'players': out}

    def finish(self, mid, winner, stats):
        with self.lock:
            row = self.db.execute("SELECT * FROM matches WHERE id=?", (mid,)).fetchone()
            if not row:
                raise RuleError("Partie inconnue")
            if row['state'] == 'finished':
                return self.message("Résultat déjà enregistré")
            if row['state'] != 'ready' or type(winner) is not int or winner not in (0, 1):
                raise RuleError("Résultat invalide")
            teams = json.loads(row['roster'])
            roster = set(teams[0] + teams[1])
            if not isinstance(stats, dict) or set(stats) != roster:
                raise RuleError("Statistiques incomplètes")
            for data in stats.values():
                if not isinstance(data, dict) or any(type(data.get(k)) is not int or not 0 <= data[k] <= 10000 for k in ('kills', 'deaths', 'beds')):
                    raise RuleError("Statistiques invalides")
            changes, before_after = {}, {}
            with self.db:
                if row['ranked']:
                    for mode in (row['mode'], 'global'):
                        for p in roster:
                            self.db.execute("INSERT OR IGNORE INTO stats(player,mode) VALUES(?,?)", (p, mode))
                        ratings = {p: self.db.execute("SELECT rating FROM stats WHERE player=? AND mode=?", (p, mode)).fetchone()[0] for p in roster}
                        avg = [sum(ratings[p] for p in team) / len(team) for team in teams]
                        for side, team in enumerate(teams):
                            expected = 1 / (1 + 10 ** (max(-4000, min(4000, avg[1 - side] - avg[side])) / 400))
                            delta = round(32 * ((side == winner) - expected))
                            for p in team:
                                s = stats[p]
                                self.db.execute("UPDATE stats SET wins=wins+?,losses=losses+?,kills=kills+?,deaths=deaths+?,beds=beds+?,rating=max(0,rating+?) WHERE player=? AND mode=?",
                                                (int(side == winner), int(side != winner), s['kills'], s['deaths'], s['beds'], delta, p, mode))
                                if mode == row['mode']:
                                    changes[p] = (delta, max(0, ratings[p] + delta))
                                    before_after[p] = (ratings[p], max(0, ratings[p] + delta))
                self.db.execute("UPDATE matches SET state='finished',winner=? WHERE id=?", (winner, mid))
            record = self.active.get(mid, {}).get('record')
            if record:
                self.history.finish(record, winner, stats, before_after)
            self.release(mid)
            self.log('match_finished', match=mid, winner=winner, ranked=bool(row['ranked']))
            for side, team in enumerate(teams):
                for p in team:
                    head = '<gold><bold>Victoire !</bold></gold>' if side == winner else '<red><bold>Défaite.</bold></red>'
                    s = stats[p]
                    detail = f"<gray>{s['kills']} kill(s), {s['deaths']} mort(s), {s['beds']} lit(s)</gray>"
                    if p in changes:
                        delta, rating = changes[p]
                        color = 'green' if delta >= 0 else 'red'
                        elo = f"<{color}>{delta:+d} Elo</{color}> <gray>({row['mode']} : {rating})</gray>"
                    else:
                        elo = '<gray>non classée</gray>'
                    self.notify(p, f"{head} <dark_gray>•</dark_gray> {elo} <dark_gray>•</dark_gray> {detail}")
            return self.message("Résultat enregistré")

    def stop_later(self, name, on_exit=None):
        if self.stop_delay <= 0:
            self.deployer.stop(name, on_exit=on_exit) if on_exit else self.deployer.stop(name)
        else:
            self.stopping[name] = (time.monotonic() + self.stop_delay, on_exit)

    def release(self, mid):
        match = self.active.pop(mid, None) or {}
        self.routes = {p: m for p, m in self.routes.items() if m != mid}
        self.moderation.release(mid)
        record = match.get('record')
        # The timeline comes from the game's own event log, complete once the server has stopped.
        self.stop_later(mid, (lambda folder, clean: self.history.timeline(record, folder)) if record else None)

    def abort(self, mid, reason):
        with self.lock:
            if mid not in self.active:
                row = self.db.execute("SELECT state FROM matches WHERE id=?", (mid,)).fetchone()
                if row and row['state'] in ('finished', 'aborted'):
                    return  # Retried report from a game server: already settled.
                raise RuleError("Partie inactive")
            match = self.active[mid]
            with self.db:
                self.db.execute("UPDATE matches SET state='aborted' WHERE id=?", (mid,))
            if match.get('record'):
                self.history.abort(match['record'], reason)
            self.release(mid)
            self.log('match_aborted', match=mid, reason=reason)
            if reason in ABORT_REASONS:
                self.notify_all(itertools.chain.from_iterable(match['teams']),
                                f"<yellow>⚠</yellow> <gray>Partie annulée : {ABORT_REASONS[reason]}. Aucun point attribué.</gray>")
