import tempfile
import time
import unittest
import uuid
from pathlib import Path

from orchestrator.core import Core, RuleError
from orchestrator.moderation import esc, parse_duration
from tests.test_social_maps import Deployer


class ModerationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); root = Path(self.temp.name)
        self.deployer = Deployer(root / 'servers')
        self.core = Core(root / 'db.sqlite', self.deployer, stop_delay=0)
        self.ids = [str(uuid.uuid4()) for _ in range(5)]
        self.names = {p: f'Player{i}' for i, p in enumerate(self.ids)}
        self.core.presence([{'id': p, 'name': n} for p, n in self.names.items()])
        self.core.set_role(self.ids[0], 'moderator'); self.core.set_role(self.ids[4], 'admin')
        self.mod = self.core.moderation
    def tearDown(self): self.core.db.close(); self.temp.cleanup()
    def cmd(self, i, action, *args): return self.core.command(self.ids[i], self.names[self.ids[i]], action, list(args))
    def inbox(self, i): return self.core.routing()['messages'].get(self.ids[i], [])

    def test_players_cannot_use_staff_tools(self):
        for action in ('freeze', 'kick', 'ban', 'mute', 'warn', 'history', 'spec', 'vanish', 'sc', 'staff'):
            with self.assertRaises(RuleError, msg=action): self.cmd(1, action, 'Player2', '1h', 'x')

    def test_ban_blocks_login_and_kicks_online_player(self):
        self.cmd(0, 'ban', 'Player1', '2h', 'Triche', 'kill', 'aura')
        self.assertIn(self.ids[1], self.core.routing()['kicks'])
        login = self.mod.login(self.ids[1], 'Player1')
        self.assertFalse(login['allowed']); self.assertIn('Triche kill aura', login['reason'])
        self.cmd(0, 'unban', 'Player1')
        self.assertTrue(self.mod.login(self.ids[1], 'Player1')['allowed'])

    def test_moderator_ban_limit_and_staff_protection(self):
        with self.assertRaises(RuleError): self.cmd(0, 'ban', 'Player1', 'perm')
        with self.assertRaises(RuleError): self.cmd(0, 'ban', 'Player1', '8d')
        with self.assertRaises(RuleError): self.cmd(0, 'mute', 'Player4', '1h')  # Admin protected from moderators.
        self.cmd(4, 'ban', 'Player1', 'perm', 'Définitif')
        self.assertIsNone(self.mod.active(self.ids[1], 'ban')['expires'])

    def test_expired_mute_is_inactive(self):
        self.cmd(0, 'mute', 'Player1', '30m', 'Spam')
        self.assertIn(self.ids[1], self.mod.state([self.ids[1]])['muted'])
        self.core.db.execute("UPDATE sanctions SET expires=? WHERE player=?", (time.time() - 1, self.ids[1]))
        self.assertNotIn(self.ids[1], self.mod.state([self.ids[1]])['muted'])

    def test_player_text_cannot_inject_markup(self):
        self.cmd(0, 'warn', 'Player1', '<click:run_command:/op', 'me>', 'lol')
        note = self.inbox(1)[0]
        self.assertIn('\\<click', note)
        import re; self.assertIsNone(re.search(r'(?<!\\)<click', note))  # Only escaped, never a live tag.
        self.assertEqual('a \\<b> \\\\c', esc('a  <b>\n\\c'))

    def test_freeze_toggle_and_disconnect_alert(self):
        self.cmd(0, 'freeze', 'Player1'); self.core.routing()
        self.assertEqual([self.ids[1]], self.mod.state(self.ids)['frozen'])
        self.core.presence([{'id': p, 'name': self.names[p]} for p in self.ids if p != self.ids[1]])
        self.assertTrue(any('freeze' in n and '/ban Player1' in n for n in self.inbox(0)))
        self.core.presence([{'id': p, 'name': self.names[p]} for p in self.ids])
        self.cmd(0, 'freeze', 'Player1')
        self.assertEqual([], self.mod.state(self.ids)['frozen'])

    def test_spectate_routes_vanished_staff_and_admits_only_them(self):
        for i in (1, 2): self.cmd(i, 'queue', '1v1')
        self.core.tick(); mid = next(iter(self.core.active)); self.core.tick()
        self.cmd(0, 'spec', 'Player1')
        self.assertEqual(mid, self.core.routing()['routes'][self.ids[0]])
        self.assertTrue(self.mod.admission(mid, self.ids[0])['spectator'])
        self.assertFalse(self.mod.admission(mid, self.ids[3])['spectator'])
        self.assertIn(self.ids[0], self.mod.state([self.ids[0]])['vanished'])
        with self.assertRaises(RuleError): self.cmd(1, 'spec', 'Player2')  # Players cannot.
        self.core.abort(mid, 'moderation')
        self.assertNotIn(self.ids[0], self.core.routes)
        self.assertFalse(self.mod.admission(mid, self.ids[0])['spectator'])

    def test_vanished_staff_hidden_from_players(self):
        self.cmd(0, 'vanish')
        self.assertNotIn('Player0', self.cmd(1, 'players')['players'])
        self.assertIn('Player0', self.cmd(4, 'players')['players'])

    def test_report_reaches_staff_with_buttons_and_is_rate_limited(self):
        self.cmd(1, 'report', 'Player2', 'fly', 'hack')
        self.assertTrue(any('/spec Player2' in n and 'fly hack' in n for n in self.inbox(0)))
        with self.assertRaises(RuleError): self.cmd(1, 'report', 'Player3', 'spam')
        self.assertEqual('Player2', self.cmd(0, 'staff')['reports'][0]['target'])

    def test_staff_chat_and_history(self):
        self.cmd(0, 'sc', 'salut', 'équipe')
        self.assertTrue(any('salut équipe' in n for n in self.inbox(4)))
        self.cmd(0, 'warn', 'Player1', 'Insultes'); self.cmd(0, 'kick', 'Player1')
        lines = self.cmd(0, 'history', 'Player1')['lines']
        self.assertEqual(2, len(lines)); self.assertIn('kick', lines[0])

    def test_offline_mode_disables_staff_tools_but_console_works(self):
        self.core.offline = True
        with self.assertRaises(RuleError): self.cmd(0, 'kick', 'Player1')
        self.mod.console('ban', 'Player1', ['1d', 'Triche'])
        self.assertFalse(self.mod.login(self.ids[1], 'Player1')['allowed'])

    def test_durations(self):
        self.assertEqual((1800, 7200, 604800, None), (parse_duration('30m'), parse_duration('2h'), parse_duration('7j'), parse_duration('perm')))
        for bad in ('0m', '10x', '', '99999d'):
            with self.assertRaises(RuleError): parse_duration(bad)


if __name__ == '__main__':
    unittest.main()
