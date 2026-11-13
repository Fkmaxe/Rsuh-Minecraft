"""One human-readable JSON file per game: runtime/history/<YYYY-MM>/<match id>.json.

Written at launch (statut "en_cours"), completed with the result, then with the timeline read from
the game server's event log once it has stopped. Moderation actions during the game are appended.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

SIDES = ('rouge', 'bleu')


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def clock(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class History:
    def __init__(self, folder):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, record):
        return self.dir / record['debut'][:7] / f"{record['partie']}.json"

    def find(self, mid):
        return next(self.dir.glob(f'*/{mid}.json'), None)

    def write(self, record):
        path = self.path(record)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(record, indent=2, ensure_ascii=False))
        temp.replace(path)

    def begin(self, match, name):
        record = {
            'partie': match['id'], 'mode': match['mode'], 'carte': match['map']['name'], 'classee': match['ranked'],
            'statut': 'en_cours', 'raison_annulation': None, 'debut': now_iso(), 'fin': None, 'duree_secondes': None, 'duree': None,
            'vainqueur': None,
            'equipes': {SIDES[s]: [{'pseudo': name(u), 'uuid': u, 'kills': None, 'morts': None, 'lits': None,
                                    'elo_avant': None, 'elo_apres': None, 'elo_variation': None} for u in team]
                        for s, team in enumerate(match['teams'])},
            'chronologie': [], 'moderation': [],
        }
        self.write(record)
        return record

    def _close(self, record, status):
        record['statut'] = status
        record['fin'] = now_iso()
        seconds = (datetime.fromisoformat(record['fin']) - datetime.fromisoformat(record['debut'])).total_seconds()
        record['duree_secondes'] = int(seconds)
        record['duree'] = clock(seconds)

    def finish(self, record, winner, stats, ratings):
        """ratings: uuid -> (before, after) for ranked games."""
        self._close(record, 'terminee')
        record['vainqueur'] = SIDES[winner]
        for team in record['equipes'].values():
            for row in team:
                s = stats.get(row['uuid'], {})
                row.update(kills=s.get('kills'), morts=s.get('deaths'), lits=s.get('beds'))
                if row['uuid'] in ratings:
                    before, after = ratings[row['uuid']]
                    row.update(elo_avant=before, elo_apres=after, elo_variation=after - before)
        self.write(record)

    def abort(self, record, reason):
        self._close(record, 'annulee')
        record['raison_annulation'] = reason
        self.write(record)

    def note(self, record, action, **fields):
        record['moderation'].append({'heure': now_iso(), 'action': action, **fields})
        self.write(record)

    def timeline(self, record, folder):
        """Read the game's event log (kept after the server stopped) into a readable timeline."""
        names = {row['uuid']: row['pseudo'] for team in record['equipes'].values() for row in team}
        name = lambda uid: names.get(uid, uid)
        logs = sorted(Path(folder).glob('plugins/Rush/events-*.jsonl'), key=lambda p: p.name, reverse=True)
        events = []
        for log in logs:  # events-1 is older than events-0 (java.util.logging rotation).
            for line in log.read_text(errors='replace').splitlines():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        start = next((datetime.fromisoformat(e['time'].replace('Z', '+00:00')) for e in events if e.get('event') == 'game_started'), None)
        timeline = []
        for e in events:
            kind = e.get('event')
            moment = datetime.fromisoformat(e['time'].replace('Z', '+00:00'))
            seconds = int((moment - start).total_seconds()) if start else 0
            entry = {'t': clock(seconds), 'secondes': seconds}
            if kind == 'game_started':
                entry['evenement'] = 'debut'
            elif kind == 'player_death':
                entry.update(evenement='mort', joueur=name(e.get('player')), par=name(e['killer']) if e.get('killer') else None,
                             vide=bool(e.get('void')), elimination=bool(e.get('eliminated')))
            elif kind == 'bed_destroyed':
                entry.update(evenement='lit_detruit', equipe=SIDES[e['side']], par=name(e.get('player')))
            elif kind == 'player_forfeit':
                entry.update(evenement='abandon', joueur=name(e.get('player')))
            elif kind == 'sudden_death':
                entry['evenement'] = 'mort_subite'
            elif kind == 'game_finished':
                entry.update(evenement='fin', vainqueur=SIDES[e['winner']])
            else:
                continue
            timeline.append(entry)
        record['chronologie'] = timeline
        self.write(record)

    def mark_interrupted(self, mids):
        """Games cut by a supervisor restart never got a result."""
        for mid in mids:
            path = self.find(mid)
            if path:
                record = json.loads(path.read_text())
                if record['statut'] == 'en_cours':
                    self.abort(record, 'redemarrage_superviseur')

    def recent(self, limit=20, player=None):
        files = sorted(self.dir.glob('*/*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        out = []
        for path in files:
            record = json.loads(path.read_text())
            if player and not any(row['pseudo'].lower() == player.lower() for team in record['equipes'].values() for row in team):
                continue
            out.append(record)
            if len(out) >= limit:
                break
        return out
