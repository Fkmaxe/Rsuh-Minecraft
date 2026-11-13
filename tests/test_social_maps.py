import json
import tempfile
import unittest
import uuid
from pathlib import Path

from orchestrator.core import Core, RuleError
from orchestrator.maps import Maps, CLASSIC, problems, sanitize


class Deployer:
    def __init__(self, root):
        self.root = Path(root); self.started = {}; self.status_of = {}
    def available(self): return len(self.started) < 4
    def start(self, match): self.started[match['id']] = match
    def status(self, mid): return self.status_of.get(mid, 'ready')
    def stop(self, mid, on_exit=None):
        self.started.pop(mid, None)
        if on_exit: on_exit(self.folder(mid), self.clean)
    clean = True
    def servers(self): return []
    def folder(self, name): return self.root / name


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); root = Path(self.temp.name)
        self.maps = Maps(root / 'maps'); self.deployer = Deployer(root / 'servers')
        self.core = Core(root / 'db.sqlite', self.deployer, maps=self.maps, stop_delay=0)
        self.ids = [str(uuid.uuid4()) for _ in range(4)]
        self.names = {p: f'Player{i}' for i, p in enumerate(self.ids)}
        self.online(self.ids)
    def tearDown(self): self.core.db.close(); self.temp.cleanup()
    def online(self, ids): self.core.presence([{'id': p, 'name': self.names[p]} for p in ids])
    def cmd(self, i, action, *args): return self.core.command(self.ids[i], self.names[self.ids[i]], action, list(args))
    def inbox(self, i): return self.core.routing()['messages'].get(self.ids[i], [])


class NotificationTest(Base):
    def test_team_invite_reaches_target_with_buttons_and_deny(self):
        self.cmd(0, 'team', 'invite', 'Player1')
        note = self.inbox(1)[0]
        self.assertIn("/team accept Player0", note); self.assertIn("/team deny Player0", note)
        self.cmd(1, 'team', 'deny', 'Player0')
        self.assertIn('refusé', self.inbox(0)[0])
        with self.assertRaises(RuleError): self.cmd(1, 'team', 'accept', 'Player0')

    def test_team_join_and_leave_notify_members(self):
        self.cmd(0, 'team', 'invite', 'Player1'); self.cmd(1, 'team', 'accept', 'Player0'); self.core.routing()
        self.cmd(1, 'team', 'leave')
        self.assertIn('quitté', self.inbox(0)[0])

    def test_friend_add_back_accepts_and_presence_is_announced(self):
        self.cmd(0, 'friend', 'add', 'Player1'); self.core.routing()
        self.cmd(1, 'friend', 'add', 'Player0')  # Mutual request = accepted.
        self.assertEqual(1, self.core.db.execute('SELECT accepted FROM friends').fetchone()[0])
        self.core.routing()
        self.online([p for p in self.ids if p != self.ids[1]])
        self.assertIn('déconnecté', self.inbox(0)[0])
        self.online(self.ids)
        self.assertIn('en ligne', self.inbox(0)[0])

    def test_pending_invites_shown_on_connection(self):
        self.online(self.ids[:1])
        self.cmd(0, 'friend', 'add', 'Player1'); self.core.routing()
        self.online(self.ids[:2])
        self.assertTrue(any('/friend accept Player0' in n for n in self.inbox(1)))

    def test_private_invite_and_full_room_offer_start(self):
        code = self.cmd(0, 'private', 'create', '1v1')['room']['code']
        self.cmd(0, 'private', 'invite', 'Player1')
        self.assertIn('/private join ' + code, self.inbox(1)[0])
        self.cmd(1, 'private', 'join', code)
        self.assertTrue(any('/private start' in n for n in self.inbox(0)))

    def test_ranked_result_announces_elo_change(self):
        for i in (0, 1): self.cmd(i, 'queue', '1v1')
        self.core.tick(); mid = next(iter(self.core.active)); self.core.tick(); self.core.routing()
        teams = self.core.active[mid]['teams']
        self.core.finish(mid, 0, {p: {'kills': 1, 'deaths': 0, 'beds': 0} for t in teams for p in t})
        winner = self.ids.index(teams[0][0])
        self.assertIn('+16 Elo', self.inbox(winner)[0])

    def test_finished_game_stops_after_players_left(self):
        import time
        self.core.stop_delay = 0.05
        for i in (0, 1): self.cmd(i, 'queue', '1v1')
        self.core.tick(); mid = next(iter(self.core.active)); self.core.tick()
        teams = self.core.active[mid]['teams']
        self.core.finish(mid, 0, {p: {'kills': 0, 'deaths': 0, 'beds': 0} for t in teams for p in t})
        self.assertNotIn(mid, self.core.routing()['routes'].values())  # Proxy moves them to the lobby…
        self.assertIn(mid, self.deployer.started)                       # …before the server stops.
        time.sleep(0.06); self.core.tick()
        self.assertNotIn(mid, self.deployer.started)

    def test_profiles_expose_role_rating_queue(self):
        self.cmd(0, 'queue', '2v2')
        data = self.core.profiles([self.ids[0], 'not-a-uuid'])
        profile = data['players'][self.ids[0]]
        self.assertEqual(('player', 1000, '2v2'), (profile['role'], profile['rating'], profile['queue']['mode']))
        self.assertEqual(1, data['network']['queue']['2v2'])
        self.assertEqual(1, len(data['players']))


class MapTest(Base):
    def admin(self): self.core.set_role(self.ids[0], 'admin')

    def test_map_commands_require_admin(self):
        with self.assertRaises(RuleError): self.cmd(1, 'map', 'list')

    def test_create_enable_requires_complete_map(self):
        self.admin()
        self.cmd(0, 'map', 'create', 'arene', 'vide')
        with self.assertRaises(RuleError): self.cmd(0, 'map', 'enable', 'arene')
        self.cmd(0, 'map', 'create', 'copie', 'classic'); self.cmd(0, 'map', 'enable', 'copie')
        self.assertTrue(self.maps.load('copie')['enabled'])
        with self.assertRaises(RuleError): self.cmd(0, 'map', 'create', '../evil')
        with self.assertRaises(RuleError): self.cmd(0, 'map', 'delete', 'classic', 'confirm')

    def test_matches_use_enabled_maps_for_their_mode(self):
        self.admin(); self.cmd(0, 'map', 'create', 'duel'); self.cmd(0, 'map', 'modes', 'duel', '1v1')
        self.cmd(0, 'map', 'enable', 'duel'); self.cmd(0, 'map', 'disable', 'classic')
        self.assertEqual('duel', self.maps.choose('1v1')[0]['name'])
        self.assertEqual('classic', self.maps.choose('5v5')[0]['name'])  # Built-in fallback.

    def write_world(self, eid):
        world = self.deployer.folder(eid) / 'world'
        (world / 'region').mkdir(parents=True); (world / 'region/r.0.0.mca').write_text('x')
        (world / 'playerdata').mkdir(); (world / 'playerdata/admin.dat').write_text('x'); (world / 'session.lock').write_text('x')

    def test_editor_save_restarts_on_saved_map_then_close(self):
        self.admin(); self.cmd(0, 'map', 'create', 'neuve', 'vide')
        self.cmd(0, 'map', 'edit', 'neuve')
        eid = next(iter(self.core.editors))
        self.assertEqual(eid, self.core.routing()['routes'][self.ids[0]])
        with self.assertRaises(RuleError): self.cmd(0, 'queue', '1v1')  # Busy while editing.
        self.write_world(eid)
        data = json.loads(json.dumps(CLASSIC)) | {'name': 'neuve'}
        self.core.editor_save(eid, data, quit=False, position=[1.5, 70.0, 2.5, 90.0, 0.0])
        saved = self.maps.load('neuve')
        self.assertIsNone(saved['layout']); self.assertFalse(saved['enabled'])
        self.assertEqual({'region'}, {p.name for p in (self.maps.dir / 'neuve/world').iterdir()})  # No player data or lock.
        self.assertTrue(any('enregistrée' in n for n in self.inbox(0)))
        (reopened, editor), = self.core.editors.items()
        self.assertNotEqual(eid, reopened)
        self.assertEqual([1.5, 70.0, 2.5, 90.0, 0.0], self.deployer.started[reopened]['position'])
        self.assertEqual(self.maps.world('neuve'), self.deployer.started[reopened]['world'])
        self.core.editor_save(reopened, data, quit=True)
        self.assertFalse(self.core.editors); self.assertNotIn(self.ids[0], self.core.routes)

    def test_unclean_editor_stop_keeps_previous_map(self):
        self.admin(); self.cmd(0, 'map', 'create', 'neuve', 'vide'); self.cmd(0, 'map', 'edit', 'neuve')
        eid = next(iter(self.core.editors)); self.write_world(eid)
        self.deployer.clean = False
        self.core.editor_save(eid, json.loads(json.dumps(CLASSIC)) | {'name': 'neuve'}, quit=True)
        self.assertEqual('empty', self.maps.load('neuve')['layout'])
        self.assertTrue(any('impossible' in n for n in self.inbox(0)))
        self.assertFalse(self.core.editors)

    def test_close_without_saving(self):
        self.admin(); self.cmd(0, 'map', 'edit', 'classic')
        eid = next(iter(self.core.editors)); self.core.editor_close(eid)
        self.assertFalse(self.core.editors); self.assertNotIn(eid, self.deployer.started)

    def test_idle_editor_is_closed(self):
        self.admin(); self.cmd(0, 'map', 'edit', 'classic')
        eid = next(iter(self.core.editors)); self.core.editors[eid]['seen'] -= 10_000
        self.online(self.ids[1:]); self.core.tick()
        self.assertFalse(self.core.editors)

    def test_sanitize_rejects_bad_editor_data(self):
        good = json.loads(json.dumps(CLASSIC))
        self.assertEqual([], problems(sanitize('x', good)))
        for bad in ({**good, 'beds': [None]}, {**good, 'npcs': [{'x': 1, 'y': 1, 'z': 1, 'yaw': 0, 'shop': 'hack', 'side': 0}]},
                    {**good, 'build': {'min_y': 100, 'max_y': 10}}, {**good, 'spawns': [[float('nan'), 0, 0, 0, 0], None]}):
            with self.assertRaises(ValueError): sanitize('x', bad)


if __name__ == '__main__':
    unittest.main()
