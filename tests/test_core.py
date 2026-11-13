import json
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from orchestrator.core import Core, RuleError, MODES
from orchestrator.processes import prune_match


class FakeDeployer:
    def __init__(self, limit=4):self.matches={};self.limit=limit;self.failed=set()
    def available(self):return len(self.matches)<self.limit
    def start(self, match):self.matches[match['id']]=match
    def status(self, mid):return 'failed' if mid in self.failed else 'ready'
    def stop(self, mid):self.matches.pop(mid,None)
    def servers(self):return [{'name':m,'host':'127.0.0.1','port':25600+i} for i,m in enumerate(self.matches)]


class CoreTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.deployer=FakeDeployer()
        self.core=Core(Path(self.temp.name)/'test.db',self.deployer,stop_delay=0)
        self.ids=[str(uuid.uuid4()) for _ in range(12)]
        self.names={p:'Player'+str(i) for i,p in enumerate(self.ids)}
        self.core.presence([{'id':p,'name':n} for p,n in self.names.items()])

    def tearDown(self):self.core.db.close();self.temp.cleanup()
    def cmd(self,i,action,*args):return self.core.command(self.ids[i],self.names[self.ids[i]],action,list(args))
    def group(self,owner,members):
        for m in members:self.cmd(owner,'team','invite','Player'+str(m));self.cmd(m,'team','accept','Player'+str(owner))
    def match(self,mode='1v1'):
        for i in range(MODES[mode]*2):self.cmd(i,'queue',mode)
        self.core.tick();self.core.tick()
        return next(iter(self.core.active))
    def stats(self,mid):
        return {p:{'kills':2,'deaths':1,'beds':0} for t in self.core.active[mid]['teams'] for p in t}

    def test_all_modes_fill_exactly(self):
        for mode,size in MODES.items():
            mid=self.match(mode)
            self.assertEqual([size,size],list(map(len,self.core.active[mid]['teams'])))
            self.assertEqual(2*size,len(self.core.routing()['routes']))
            self.core.abort(mid,'test')

    def test_no_duplicate_queue_or_cross_mode(self):
        self.cmd(0,'queue','1v1')
        with self.assertRaises(RuleError):self.cmd(0,'queue','2v2')
        self.cmd(1,'queue','2v2');self.core.tick();self.assertFalse(self.core.active)

    def test_party_preserved_and_mutation_blocked(self):
        self.group(0,[1]);self.cmd(0,'queue','2v2')
        with self.assertRaises(RuleError):self.cmd(1,'team','leave')
        self.cmd(2,'queue','2v2');self.cmd(3,'queue','2v2');self.core.tick()
        teams=next(iter(self.core.active.values()))['teams']
        self.assertTrue(any(set(t)==set(self.ids[:2]) for t in teams))

    def test_group_too_large_and_nonleader(self):
        self.group(0,[1])
        with self.assertRaises(RuleError):self.cmd(0,'queue','1v1')
        with self.assertRaises(RuleError):self.cmd(1,'queue','2v2')

    def test_packing_backtracks_without_splitting(self):
        entries=[{'players':list(range(n))} for n in (3,3,2,2)]
        left,right=Core.packing(entries,5)
        self.assertEqual(5,sum(len(entries[i]['players']) for i in left))
        self.assertEqual(5,sum(len(entries[i]['players']) for i in right))
        self.assertFalse(set(left)&set(right))

    def test_offline_member_removes_entire_entry(self):
        self.group(0,[1]);self.cmd(0,'queue','2v2')
        self.core.presence([{'id':self.ids[0],'name':'Player0'}]);self.assertFalse(self.core.queue)

    def test_queue_survives_capacity_limit(self):
        self.deployer.limit=0;self.cmd(0,'queue','1v1');self.cmd(1,'queue','1v1');self.core.tick()
        self.assertEqual(2,len(self.core.queue));self.deployer.limit=1;self.core.tick();self.assertEqual(1,len(self.core.active))

    def test_ranked_result_atomic_and_idempotent(self):
        mid=self.match();stats=self.stats(mid)
        self.core.finish(mid,0,stats);self.core.finish(mid,0,stats)
        rows=self.core.db.execute("SELECT * FROM stats WHERE mode='global'").fetchall()
        self.assertEqual(2,len(rows));self.assertEqual(1,sum(r['wins'] for r in rows));self.assertEqual(2000,sum(r['rating'] for r in rows))
        self.assertFalse(self.core.routes);self.assertFalse(self.deployer.matches)

    def test_bad_result_cannot_award_scores(self):
        mid=self.match();stats=self.stats(mid)
        for winner,data in [(2,stats),(True,stats),(0,{}),(0,{**stats,self.ids[5]:{'kills':0,'deaths':0,'beds':0}})]:
            with self.assertRaises(RuleError):self.core.finish(mid,winner,data)
        stats[self.ids[0]]['kills']=-1
        with self.assertRaises(RuleError):self.core.finish(mid,0,stats)
        self.assertEqual(0,self.core.db.execute('SELECT count(*) FROM stats').fetchone()[0])

    def test_private_requires_owner_full_and_not_ranked(self):
        self.cmd(0,'private','create','1v1');code=next(iter(self.core.rooms))
        with self.assertRaises(RuleError):self.cmd(0,'private','start')
        self.cmd(1,'private','join',code)
        with self.assertRaises(RuleError):self.cmd(1,'private','start')
        self.cmd(0,'private','start');self.core.tick();mid=next(iter(self.core.active))
        self.core.finish(mid,0,self.stats(mid));self.assertEqual(0,self.core.db.execute('SELECT count(*) FROM stats').fetchone()[0])

    def test_friends_require_incoming_request(self):
        self.cmd(0,'friend','add','Player1')
        with self.assertRaises(RuleError):self.cmd(0,'friend','accept','Player1')
        self.cmd(1,'friend','accept','Player0')
        self.assertIn('connecté',self.cmd(0,'friend','list')['lines'][0])
        self.cmd(1,'friend','remove','Player0');self.assertEqual([],self.cmd(0,'friend','list')['lines'])

    def test_permission_roles(self):
        with self.assertRaises(RuleError):self.cmd(0,'admin')
        self.core.set_role(self.ids[0],'admin');self.cmd(0,'admin','role','Player1','moderator')
        self.assertEqual('moderator',self.cmd(1,'status')['role'])
        with self.assertRaises(RuleError):self.cmd(1,'admin','role','Player2','admin')
        self.assertEqual('player',self.cmd(2,'status')['role'])

    def test_crash_releases_players_without_ratings(self):
        mid=self.match();self.deployer.failed.add(mid);self.core.tick()
        self.assertFalse(self.core.active);self.assertFalse(self.core.routes)
        self.assertEqual('aborted',self.core.db.execute('SELECT state FROM matches').fetchone()[0])

    def test_spawn_failure_preserves_queue_and_supervisor(self):
        start=self.deployer.start
        def failed(match):raise OSError('executable unavailable')
        self.deployer.start=failed
        self.cmd(0,'queue','1v1');self.cmd(1,'queue','1v1');self.core.tick()
        self.assertEqual(2,len(self.core.queue));self.assertFalse(self.core.active)
        self.assertFalse(self.core.routes)
        self.deployer.start=start;self.core.deployment_retry_at=0;self.core.tick()
        self.assertEqual(1,len(self.core.active));self.assertFalse(self.core.queue)

    def test_simultaneous_duplicate_requests(self):
        results=[]
        def join():
            try:self.cmd(0,'queue','1v1');results.append(True)
            except RuleError:results.append(False)
        threads=[threading.Thread(target=join) for _ in range(8)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(1,sum(results));self.assertEqual(1,len(self.core.queue))

    def test_restart_preserves_social_and_stats_aborts_pending(self):
        self.cmd(0,'friend','add','Player1');mid=self.match()
        self.core.db.close();self.core=Core(Path(self.temp.name)/'test.db',FakeDeployer())
        self.assertEqual('aborted',self.core.db.execute('SELECT state FROM matches WHERE id=?',(mid,)).fetchone()[0])
        self.assertEqual(1,len(self.cmd(1,'friend','list')['lines']))

    def test_abort_is_idempotent_for_settled_matches(self):
        mid=self.match()
        self.core.abort(mid,'draw')
        self.core.abort(mid,'draw')  # Game server retry after a lost response.
        with self.assertRaises(RuleError):self.core.abort('rush-unknown','draw')


class OfflineModeTest(unittest.TestCase):
    """Offline fallback: unverified names must never earn ratings or admin rights."""
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.core=Core(Path(self.temp.name)/'test.db',FakeDeployer(),offline=True)
        self.ids=[str(uuid.uuid4()) for _ in range(2)]
        self.core.presence([{'id':p,'name':'Player'+str(i)} for i,p in enumerate(self.ids)])
    def tearDown(self):self.core.db.close();self.temp.cleanup()

    def test_matches_are_unranked(self):
        for i,p in enumerate(self.ids):self.core.command(p,'Player'+str(i),'queue',['1v1'])
        self.core.tick();self.core.tick()
        mid=next(iter(self.core.active))
        teams=self.core.active[mid]['teams']
        self.core.finish(mid,0,{p:{'kills':1,'deaths':0,'beds':1} for t in teams for p in t})
        self.assertEqual(0,self.core.db.execute('SELECT ranked FROM matches WHERE id=?',(mid,)).fetchone()[0])
        self.assertEqual(0,self.core.db.execute('SELECT count(*) FROM stats').fetchone()[0])

    def test_in_game_admin_disabled_even_for_admin_name(self):
        self.core.set_role(self.ids[0],'admin')
        with self.assertRaises(RuleError):self.core.command(self.ids[0],'Player0','admin',[])

    def test_name_lookup_prefers_connected_account(self):
        stale=str(uuid.uuid4())  # Same name seen earlier with its online-mode UUID, stored first.
        self.core.db.execute('DELETE FROM players WHERE id=?',(self.ids[1],))
        self.core.db.execute('INSERT INTO players(id,name) VALUES(?,?)',(stale,'Player1'))
        self.core.db.execute('INSERT INTO players(id,name) VALUES(?,?)',(self.ids[1],'Player1'))
        self.assertEqual(self.ids[1],self.core.target('player1'))


class PruneTest(unittest.TestCase):
    def test_prune_keeps_logs_and_never_follows_shared_folders(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);lobby=root/'lobby';match=root/'rush-x'
            (lobby/'libraries').mkdir(parents=True);(lobby/'libraries'/'lib.jar').write_text('x')
            (lobby/'plugins'/'.paper-remapped').mkdir(parents=True);(lobby/'plugins'/'.paper-remapped'/'index.json').write_text('{}')
            (match/'plugins'/'Rush').mkdir(parents=True);(match/'world').mkdir()
            (match/'libraries').symlink_to(lobby/'libraries',target_is_directory=True)
            (match/'plugins'/'.paper-remapped').symlink_to(lobby/'plugins'/'.paper-remapped',target_is_directory=True)
            for f in ('console.log','server.properties','plugins/rush-paper.jar','plugins/Rush/instance.json','plugins/Rush/events-0.jsonl','world/level.dat'):
                (match/f).write_text('x')
            prune_match(match)
            self.assertEqual({'console.log','plugins'},{e.name for e in match.iterdir()})
            self.assertEqual(['events-0.jsonl'],[e.name for e in (match/'plugins'/'Rush').iterdir()])
            self.assertEqual(['Rush'],[e.name for e in (match/'plugins').iterdir()])
            self.assertTrue((lobby/'libraries'/'lib.jar').exists() and (lobby/'plugins'/'.paper-remapped'/'index.json').exists())


if __name__=='__main__':unittest.main()
