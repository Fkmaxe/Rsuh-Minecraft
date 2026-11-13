"""Serialized matchmaking, social state and atomic ranked results."""
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

MODES = {"1v1": 1, "2v2": 2, "5v5": 5}


class RuleError(ValueError):
    pass


class Core:
    def __init__(self, db: Path, deployer, log=lambda *a, **k: None, offline=False):
        # Offline mode: names are not verified, so nothing may depend on identity
        # (no ranked results, no in-game administration).
        self.offline = offline
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
        self.db.execute("UPDATE matches SET state='aborted' WHERE state NOT IN ('finished','aborted')")
        self.db.commit()
        self.deployer, self.log = deployer, log
        self.online = set()
        self.queue = []
        self.rooms = {}
        self.routes = {}
        self.active = {}
        self.last_presence = time.monotonic()
        self.deployment_retry_at = 0

    def register(self, player, name):
        try:
            player = str(uuid.UUID(player))
        except (ValueError, TypeError, AttributeError):
            raise RuleError("UUID invalide")
        if not re.fullmatch(r"[A-Za-z0-9_]{1,16}", name):
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

    def party(self, player):
        row = self.db.execute("SELECT owner FROM teams WHERE member=?", (player,)).fetchone()
        if not row:
            return player, [player]
        return row[0], [r[0] for r in self.db.execute("SELECT member FROM teams WHERE owner=? ORDER BY member", (row[0],))]

    def busy(self, p):
        return (p in self.routes or any(p in q['players'] for q in self.queue)
                or any(p in r['players'] for r in self.rooms.values()))

    def free(self, players):
        if any(self.busy(p) for p in players):
            raise RuleError("Un joueur est déjà en file, en partie ou dans un salon privé")

    def presence(self, players):
        with self.lock:
            self.online = {self.register(p['id'], p['name']) for p in players}
            self.last_presence = time.monotonic()
            self.queue = [q for q in self.queue if set(q['players']) <= self.online]
            for code, room in list(self.rooms.items()):
                if room['owner'] not in self.online or time.time() - room['created'] > 1800:
                    del self.rooms[code]
                else:
                    room['players'] = [p for p in room['players'] if p in self.online]

    def command(self, player, name, action, args):
        with self.lock:
            p = self.register(player, name)
            args = [str(a) for a in args]
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
                return self.message("Équipe inscrite en " + mode + (" (non classé : mode hors ligne)" if self.offline else ""))
            if action == 'leave':
                # During a match, proxy transfers to lobby and game treats this as forfeit.
                self.queue = [q for q in self.queue if p not in q['players']]
                for code, room in list(self.rooms.items()):
                    if p == room['owner']:
                        del self.rooms[code]
                    elif p in room['players']:
                        room['players'].remove(p)
                self.routes.pop(p, None)
                return self.message("Retour au lobby")
            if action == 'team':
                return self.team(p, args)
            if action == 'friend':
                return self.friend(p, args)
            if action == 'private':
                return self.private(p, args)
            if action == 'leaderboard':
                mode = args[0] if args else 'global'
                if mode not in (*MODES, 'global'):
                    raise RuleError("Classement : global, 1v1, 2v2, 5v5")
                rows = self.db.execute("SELECT p.name,s.* FROM stats s JOIN players p ON p.id=s.player WHERE mode=? ORDER BY rating DESC,wins DESC,player LIMIT 10", (mode,)).fetchall()
                return {'message': 'Classement ' + mode, 'lines': [f"{i+1}. {r['name']} — {r['rating']} Elo | {r['wins']} V / {r['losses']} D" for i, r in enumerate(rows)]}
            if action == 'status':
                owner, members = self.party(p)
                role = self.db.execute("SELECT role FROM players WHERE id=?", (p,)).fetchone()[0]
                return {'message': 'Rush', 'lines': ['Rôle : '+role, 'Équipe : '+', '.join(map(self.name, members)),
                        f"File : {sum(len(q['players']) for q in self.queue)} joueurs", f"Parties : {len(self.active)}"]
                        + (['Mode hors ligne : parties non classées'] if self.offline else []), 'role': role}
            if action == 'players':
                return {'message':'Joueurs connectés', 'lines':sorted(self.name(uid) for uid in self.online if uid != p)}
            if action == 'admin':
                if self.offline:
                    raise RuleError("Administration en jeu désactivée en mode hors ligne (pseudos non vérifiés) : utiliser la CLI")
                role = self.db.execute("SELECT role FROM players WHERE id=?", (p,)).fetchone()[0]
                if role not in ('moderator', 'admin'):
                    raise RuleError("Permission refusée")
                if args and args[0] == 'abort' and len(args) == 2:
                    self.abort(args[1], 'moderation')
                    return self.message("Partie annulée")
                if role == 'admin' and len(args) == 3 and args[0] == 'role':
                    self.set_role(self.target(args[1]), args[2])
                    return self.message("Rôle modifié")
                if args:
                    raise RuleError("Permission refusée ou syntaxe invalide : /rush admin [abort <id>|role <pseudo> <rôle>]")
                return {'message': 'Administration', 'lines': [f"{mid} {m['mode']} {m['state']}" for mid,m in self.active.items()]}
            raise RuleError("Commande inconnue")

    @staticmethod
    def message(text):
        return {'message': text, 'lines': []}

    def set_role(self, p, role):
        if role not in ('player', 'moderator', 'admin'):
            raise RuleError("Rôle : player, moderator, admin")
        with self.lock, self.db:
            self.db.execute("UPDATE players SET role=? WHERE id=?", (role, p))
            self.log('role_changed', player=p, role=role)

    def team(self, p, args):
        sub = args[0] if args else 'list'
        owner, members = self.party(p)
        if sub == 'list':
            invites = [self.name(r[0]) for r in self.db.execute("SELECT owner FROM invites WHERE target=?", (p,))]
            return {'message': 'Chef : '+self.name(owner), 'lines': list(map(self.name, members))+['Invitations : '+', '.join(invites)],
                    'actions':[{'name':name,'label':'Accepter '+name,'action':'accept'} for name in invites]
                    + ([{'name':self.name(uid),'label':'Retirer '+self.name(uid),'action':'kick'} for uid in members if uid!=p] if owner==p else [])}
        self.free(members)
        if sub == 'invite' and len(args) == 2:
            if owner != p:
                raise RuleError("Seul le chef peut inviter")
            target = self.target(args[1])
            if target == p or target in members or len(members) >= 5:
                raise RuleError("Invitation impossible (5 membres maximum)")
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO teams VALUES(?,?)", (p,p))
                self.db.execute("INSERT OR IGNORE INTO invites VALUES(?,?)", (p,target))
            return self.message("Invitation envoyée ; /team accept " + self.name(p))
        if sub == 'accept' and len(args) == 2:
            target = self.target(args[1])
            if self.db.execute("SELECT 1 FROM teams WHERE member=?", (p,)).fetchone():
                raise RuleError("Quittez votre équipe actuelle")
            if not self.db.execute("SELECT 1 FROM invites WHERE owner=? AND target=?", (target,p)).fetchone():
                raise RuleError("Invitation introuvable")
            other_owner, other = self.party(target)
            self.free(other)
            if other_owner != target or len(other) >= 5:
                raise RuleError("Équipe indisponible")
            with self.db:
                self.db.execute("INSERT INTO teams VALUES(?,?)", (target,p))
                self.db.execute("DELETE FROM invites WHERE target=?", (p,))
            return self.message("Équipe rejointe")
        if sub == 'leave':
            with self.db:
                if owner == p:
                    self.db.execute("DELETE FROM teams WHERE owner=?", (p,))
                    self.db.execute("DELETE FROM invites WHERE owner=?", (p,))
                else:
                    self.db.execute("DELETE FROM teams WHERE member=?", (p,))
            return self.message("Équipe quittée")
        if sub == 'kick' and len(args) == 2 and owner == p:
            target = self.target(args[1])
            if target == p:
                raise RuleError("Utilisez /team leave")
            with self.db:
                self.db.execute("DELETE FROM teams WHERE owner=? AND member=?", (p,target))
            return self.message("Membre retiré")
        raise RuleError("/team invite|accept|kick <pseudo>, /team leave, /team list")

    def friend(self, p, args):
        sub = args[0] if args else 'list'
        if sub == 'list':
            rows = self.db.execute("SELECT * FROM friends WHERE a=? OR b=?", (p,p)).fetchall()
            lines = []
            actions = []
            for r in rows:
                other = r['b'] if r['a'] == p else r['a']
                status = ('connecté' if other in self.online else 'hors ligne') if r['accepted'] else ('demande reçue' if r['b'] == p else 'demande envoyée')
                lines.append(self.name(other)+' — '+status)
                subaction = 'accept' if not r['accepted'] and r['b']==p else 'remove'
                actions.append({'name':self.name(other),'label':('Accepter ' if subaction=='accept' else 'Retirer ')+self.name(other),'action':subaction})
            return {'message': 'Amis', 'lines': lines, 'actions':actions}
        if len(args) != 2:
            raise RuleError("/friend add|accept|remove <pseudo>")
        target = self.target(args[1])
        if target == p:
            raise RuleError("Choisissez un autre joueur")
        with self.db:
            if sub == 'add':
                if self.db.execute("SELECT 1 FROM friends WHERE (a=? AND b=?) OR (a=? AND b=?)", (p,target,target,p)).fetchone():
                    raise RuleError("Demande ou amitié existante")
                self.db.execute("INSERT INTO friends(a,b) VALUES(?,?)", (p,target))
            elif sub == 'accept':
                result = self.db.execute("UPDATE friends SET accepted=1 WHERE a=? AND b=? AND accepted=0", (target,p))
                if not result.rowcount:
                    raise RuleError("Aucune demande reçue de ce joueur")
            elif sub == 'remove':
                self.db.execute("DELETE FROM friends WHERE (a=? AND b=?) OR (a=? AND b=?)", (p,target,target,p))
            else:
                raise RuleError("Action ami inconnue")
        return self.message("Liste d’amis mise à jour")

    def private(self, p, args):
        sub = args[0] if args else 'list'
        if sub == 'create' and len(args) == 2:
            mode = args[1]
            if mode not in MODES:
                raise RuleError("Mode invalide")
            self.free([p])
            code = secrets.token_hex(4).upper()
            self.rooms[code] = dict(owner=p, players=[p], mode=mode, created=time.time())
            return self.message("Salon privé : " + code + " — /private join " + code)
        if sub == 'join' and len(args) == 2:
            self.free([p])
            room = self.rooms.get(args[1].upper())
            if not room or len(room['players']) >= MODES[room['mode']]*2:
                raise RuleError("Salon inexistant ou complet")
            room['players'].append(p)
            return self.message("Salon rejoint. Ordre d’arrivée : rouge, bleu, rouge, bleu…")
        room_entry = next(((code,r) for code,r in self.rooms.items() if p in r['players']), None)
        if not room_entry:
            raise RuleError("/private create <mode>, /private join <code>")
        code, room = room_entry
        if sub == 'start':
            if p != room['owner']:
                raise RuleError("Seul le créateur peut démarrer")
            if len(room['players']) != MODES[room['mode']]*2 or not set(room['players']) <= self.online:
                raise RuleError("Le salon doit être complet et connecté")
            self.launch(room['mode'], [room['players'][::2],room['players'][1::2]], False)
            del self.rooms[code]
            return self.message("Partie privée en démarrage (non classée)")
        return {'message': 'Salon '+code, 'lines': [f"{'Rouge' if i%2 == 0 else 'Bleu'} : {self.name(v)}" for i,v in enumerate(room['players'])]}

    def launch(self, mode, teams, ranked):
        if not self.deployer.available():
            raise RuleError("Capacité atteinte, réessayez dans quelques instants")
        ranked = ranked and not self.offline
        mid = 'rush-' + secrets.token_hex(6)
        match = dict(id=mid, mode=mode, teams=teams, ranked=ranked, state='starting', created=time.time())
        # Reserve roster before spawning to make admission deterministic.
        try:
            self.deployer.start(match)
        except (OSError, RuntimeError, StopIteration) as exc:
            self.deployer.stop(mid)
            self.deployment_retry_at = time.monotonic() + 15
            self.log('deployment_failed', match=mid, error=str(exc))
            raise RuleError("Déploiement indisponible ; la file est conservée") from exc
        self.active[mid] = match
        for p in itertools.chain.from_iterable(teams):
            self.routes[p] = mid
        with self.db:
            self.db.execute("INSERT INTO matches VALUES(?,?,?,?,?,NULL)", (mid,mode,int(ranked),json.dumps(teams),'starting'))
        self.log('match_starting', match=mid, mode=mode, ranked=ranked, teams=teams)
        return mid

    @staticmethod
    def packing(entries, size):
        # Bounded dynamic programming: keep groups intact, fill two exact teams.
        states = {(0,0): ([],[])}
        for index, q in enumerate(entries):
            n = len(q['players'])
            for (a,b), (left,right) in list(states.items()):
                if a+n <= size:
                    states.setdefault((a+n,b), (left+[index],right))
                if b+n <= size:
                    states.setdefault((a,b+n), (left,right+[index]))
            if (size,size) in states:
                return states[(size,size)]
        return None

    def tick(self):
        with self.lock:
            if time.monotonic() - self.last_presence > 15:
                self.online.clear()
                self.queue.clear()
            for mid, m in list(self.active.items()):
                status = self.deployer.status(mid)
                if status == 'ready' and m['state'] == 'starting':
                    m['state'] = 'ready'
                    with self.db:
                        self.db.execute("UPDATE matches SET state='ready' WHERE id=?", (mid,))
                    self.log('match_ready', match=mid)
                elif status == 'failed':
                    self.abort(mid, 'process_failed_or_timeout')
            if time.monotonic() < self.deployment_retry_at:
                return
            for mode, size in MODES.items():
                while self.deployer.available():
                    entries = [q for q in self.queue if q['mode'] == mode]
                    packed = self.packing(entries, size)
                    if not packed:
                        break
                    left, right = packed
                    teams = [[p for i in side for p in entries[i]['players']] for side in (left,right)]
                    try:
                        self.launch(mode, teams, True)
                    except RuleError:
                        return
                    chosen = {id(entries[i]) for i in left+right}
                    self.queue = [q for q in self.queue if id(q) not in chosen]

    def routing(self):
        with self.lock:
            return {'servers': self.deployer.servers(), 'routes': {p:mid for p,mid in self.routes.items() if mid in self.active and self.active[mid]['state']=='ready'}}

    def finish(self, mid, winner, stats):
        with self.lock:
            row = self.db.execute("SELECT * FROM matches WHERE id=?", (mid,)).fetchone()
            if not row:
                raise RuleError("Partie inconnue")
            if row['state'] == 'finished':
                return self.message("Résultat déjà enregistré")
            if row['state'] != 'ready' or type(winner) is not int or winner not in (0,1):
                raise RuleError("Résultat invalide")
            teams = json.loads(row['roster'])
            roster = set(teams[0]+teams[1])
            if not isinstance(stats, dict) or set(stats) != roster:
                raise RuleError("Statistiques incomplètes")
            for data in stats.values():
                if not isinstance(data, dict) or any(type(data.get(k)) is not int or not 0 <= data[k] <= 10000 for k in ('kills','deaths','beds')):
                    raise RuleError("Statistiques invalides")
            with self.db:
                if row['ranked']:
                    for mode in (row['mode'],'global'):
                        for p in roster:
                            self.db.execute("INSERT OR IGNORE INTO stats(player,mode) VALUES(?,?)", (p,mode))
                        ratings = {p:self.db.execute("SELECT rating FROM stats WHERE player=? AND mode=?", (p,mode)).fetchone()[0] for p in roster}
                        avg = [sum(ratings[p] for p in team)/len(team) for team in teams]
                        for side, team in enumerate(teams):
                            expected = 1/(1+10**(max(-4000,min(4000,avg[1-side]-avg[side]))/400))
                            delta = round(32*((side == winner)-expected))
                            for p in team:
                                s = stats[p]
                                self.db.execute("UPDATE stats SET wins=wins+?,losses=losses+?,kills=kills+?,deaths=deaths+?,beds=beds+?,rating=max(0,rating+?) WHERE player=? AND mode=?",
                                                (int(side==winner),int(side!=winner),s['kills'],s['deaths'],s['beds'],delta,p,mode))
                self.db.execute("UPDATE matches SET state='finished',winner=? WHERE id=?", (winner,mid))
            self.release(mid)
            self.log('match_finished', match=mid, winner=winner, ranked=bool(row['ranked']))
            return self.message("Résultat enregistré")

    def release(self, mid):
        self.active.pop(mid, None)
        self.routes = {p:m for p,m in self.routes.items() if m != mid}
        self.deployer.stop(mid)

    def abort(self, mid, reason):
        with self.lock:
            if mid not in self.active:
                row = self.db.execute("SELECT state FROM matches WHERE id=?", (mid,)).fetchone()
                if row and row['state'] in ('finished', 'aborted'):
                    return  # Retried report from a game server: already settled.
                raise RuleError("Partie inactive")
            with self.db:
                self.db.execute("UPDATE matches SET state='aborted' WHERE id=?", (mid,))
            self.release(mid)
            self.log('match_aborted', match=mid, reason=reason)
