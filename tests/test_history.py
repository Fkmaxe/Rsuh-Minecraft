import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from orchestrator.core import Core
from orchestrator.history import History
from tests.test_social_maps import Deployer


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.deployer = Deployer(self.root / 'servers')
        self.history = History(self.root / 'history')
        self.core = self.make_core()
        self.ids = [str(uuid.uuid4()) for _ in range(3)]
        self.names = {p: f'Player{i}' for i, p in enumerate(self.ids)}
        self.core.presence([{'id': p, 'name': n} for p, n in self.names.items()])
        self.core.set_role(self.ids[2], 'moderator')
    def make_core(self): return Core(self.root / 'db.sqlite', self.deployer, stop_delay=0, history=self.history)
    def tearDown(self): self.core.db.close(); self.temp.cleanup()
    def cmd(self, i, action, *args): return self.core.command(self.ids[i], self.names[self.ids[i]], action, list(args))
    def game(self):
        for i in (0, 1): self.cmd(i, 'queue', '1v1')
        self.core.tick(); mid = next(iter(self.core.active)); self.core.tick()
        return mid
    def read(self, mid): return json.loads(self.history.find(mid).read_text())

    def write_events(self, mid, teams):
        log = self.deployer.folder(mid) / 'plugins/Rush/events-0.jsonl'; log.parent.mkdir(parents=True)
        a, b = teams[0][0], teams[1][0]
        rows = [{'time': '2026-11-13T15:00:00Z', 'event': 'arena_created'},
                {'time': '2026-11-13T15:00:05Z', 'event': 'game_started'},
                {'time': '2026-11-13T15:01:17Z', 'event': 'player_death', 'player': b, 'killer': a, 'void': True, 'eliminated': False},
                {'time': '2026-11-13T15:03:40Z', 'event': 'bed_destroyed', 'side': 1, 'player': a},
                {'time': '2026-11-13T15:03:41Z', 'event': 'pvp_hit', 'attacker': a},
                {'time': '2026-11-13T15:04:02Z', 'event': 'player_death', 'player': b, 'killer': a, 'void': False, 'eliminated': True},
                {'time': '2026-11-13T15:04:02Z', 'event': 'game_finished', 'winner': 0}]
        log.write_text('\n'.join(json.dumps(r) for r in rows) + '\n')

    def test_ranked_game_file_with_elo_timeline_and_freeze(self):
        mid = self.game()
        record = self.read(mid)
        self.assertEqual(('en_cours', '1v1', 'classic', True), (record['statut'], record['mode'], record['carte'], record['classee']))
        teams = self.core.active[mid]['teams']
        winner = self.names[teams[0][0]]
        self.core.command(self.ids[2], 'Player2', 'freeze', [winner])
        self.core.command(self.ids[2], 'Player2', 'freeze', [winner])
        self.write_events(mid, teams)
        self.core.finish(mid, 0, {teams[0][0]: {'kills': 2, 'deaths': 0, 'beds': 1}, teams[1][0]: {'kills': 0, 'deaths': 2, 'beds': 0}})
        record = self.read(mid)
        self.assertEqual(('terminee', 'rouge'), (record['statut'], record['vainqueur']))
        red = record['equipes']['rouge'][0]
        self.assertEqual((winner, 2, 1, 1000, 1016, 16), (red['pseudo'], red['kills'], red['lits'], red['elo_avant'], red['elo_apres'], red['elo_variation']))
        self.assertEqual(-16, record['equipes']['bleu'][0]['elo_variation'])
        self.assertEqual(['freeze', 'degel'], [m['action'] for m in record['moderation']])
        self.assertEqual('Player2', record['moderation'][0]['par'])
        steps = [(e['t'], e['evenement']) for e in record['chronologie']]
        self.assertEqual([('00:00', 'debut'), ('01:12', 'mort'), ('03:35', 'lit_detruit'), ('03:57', 'mort'), ('03:57', 'fin')], steps)
        self.assertTrue(record['chronologie'][1]['vide'] and record['chronologie'][3]['elimination'])
        self.assertEqual(winner, record['chronologie'][2]['par'])

    def test_freeze_is_stored_with_its_duration_and_survives_restart(self):
        self.core.command(self.ids[2], 'Player2', 'freeze', ['Player0'])
        row = self.core.db.execute("SELECT * FROM sanctions WHERE kind='freeze'").fetchone()
        self.assertIsNone(row['expires'])
        self.assertIn('en cours', self.cmd(2, 'history', 'Player0')['lines'][0])
        self.core.db.close(); self.core = self.make_core()
        self.assertIn(self.ids[0], self.core.moderation.frozen)
        self.core.presence([{'id': p, 'name': n} for p, n in self.names.items()])
        self.core.command(self.ids[2], 'Player2', 'freeze', ['Player0'])
        self.assertIsNotNone(self.core.db.execute("SELECT expires FROM sanctions WHERE kind='freeze'").fetchone()[0])

    def test_aborted_and_interrupted_games(self):
        mid = self.game(); self.core.abort(mid, 'draw_time_limit')
        self.assertEqual(('annulee', 'draw_time_limit'), (self.read(mid)['statut'], self.read(mid)['raison_annulation']))
        mid2 = self.game()
        self.core.db.close(); self.core = self.make_core()  # Supervisor restart mid-game.
        self.assertEqual('redemarrage_superviseur', self.read(mid2)['raison_annulation'])

    def test_deployment_tests_and_listing(self):
        with self.core.lock: synthetic = self.core.launch('1v1', [[str(uuid.uuid4())], [str(uuid.uuid4())]], False, record=False)
        self.assertEqual([], list(self.history.dir.glob('*/*.json')))
        self.core.abort(synthetic, 'deployment_test_complete')
        mid = self.game(); self.core.abort(mid, 'draw')
        self.assertEqual([mid], [r['partie'] for r in self.history.recent(player='player1')])
        self.assertEqual([], self.history.recent(player='nobody'))


if __name__ == '__main__':
    unittest.main()
