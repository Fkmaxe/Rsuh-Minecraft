"""Isolated actual Minecraft test. Requires the EULA accepted via CLI init.

No production identity/settings are modified. Offline bots use a loopback-only
proxy and a separate database, API token and forwarding secret.
"""
import json
import queue
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from orchestrator.cli import read_settings
from orchestrator.core import Core
from orchestrator.processes import Processes
from orchestrator.protocol import ping
from orchestrator.server import serve, make_logger


def wait_for(check, timeout=180, label='condition'):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        result=check()
        if result:return result
        time.sleep(0.25)
    raise AssertionError('Timeout: '+label)


def main():
    settings=read_settings()
    assert settings['eula'], 'Accepter l’EULA via CLI init --accept-eula avant les tests Minecraft'
    settings.update(api_port=18788,proxy_port=25575,lobby_port=25576,first_match_port=25620,
                    max_instances=2,test_offline=True,diagnostic=True)
    root=Path(tempfile.mkdtemp(prefix='network-',dir=ROOT/'.cache'))
    (root/'artifacts').symlink_to(ROOT/'artifacts',target_is_directory=True)
    (root/'runtime').mkdir()
    token=secrets.token_hex(32)
    processes=Processes(root,settings,token,secrets.token_hex(32),make_logger(root/'runtime/events.jsonl'))
    core=Core(root/'runtime/rush.sqlite3',processes)
    server=serve(core,settings,token)
    stop=threading.Event()
    def ticker():
        while not stop.wait(0.5):core.tick()
    thread=threading.Thread(target=ticker);thread.start()
    botproc=None
    report_path=ROOT/'runtime/smoke-report.json'
    report_path.write_text(json.dumps({'result':'RUNNING','workspace':str(root)},indent=2))
    print('Test workspace:',root,flush=True)
    try:
        processes.start();processes.start_proxy()
        wait_for(lambda:processes.status('lobby')=='ready' and processes.status('proxy')=='ready',label='lobby and proxy startup')
        print('PASS lobby + proxy status:',ping('127.0.0.1',settings['proxy_port'])['version'],flush=True)
        botproc=subprocess.Popen(['node',str(ROOT/'tests/bots.cjs'),str(settings['proxy_port'])],cwd=ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
        incoming=queue.Queue();events=[]
        def reader():
            for line in botproc.stdout:
                try:
                    row=json.loads(line);incoming.put(row)
                    if row.get('event') in ('kicked','error'):print('BOT:',row,flush=True)
                except ValueError:print('BOT:',line.strip(),flush=True)
        threading.Thread(target=reader,daemon=True).start()
        counter=0
        def drain():
            while not incoming.empty():events.append(incoming.get())
        def exchange(action,name='RushBotA',**fields):
            nonlocal counter
            counter+=1
            botproc.stdin.write(json.dumps(dict(id=counter,action=action,name=name,**fields))+'\n');botproc.stdin.flush()
            def reply():
                drain();return next((e for e in events if e.get('id')==counter),None)
            result=wait_for(reply,timeout=20,label=action)
            if 'error' in result:raise AssertionError(result['error'])
            return result.get('result')
        def online():
            drain()
            return len(core.online)==2 and sum(e.get('event')=='spawn' for e in events)>=2
        wait_for(online,timeout=45,label='1.9.4 bot login')
        print('PASS two 1.9.4 clients authenticated by isolated test proxy',flush=True)
        exchange('chat',message='/friend add RushBotB');time.sleep(0.5)
        exchange('chat',name='RushBotB',message='/friend accept RushBotA')
        wait_for(lambda:core.db.execute('SELECT count(*) FROM friends WHERE accepted=1').fetchone()[0]==1,timeout=10,label='friend commands')
        exchange('chat',message='/team invite RushBotB');time.sleep(0.5)
        exchange('chat',name='RushBotB',message='/team accept RushBotA')
        wait_for(lambda:core.db.execute('SELECT count(*) FROM teams').fetchone()[0]==2,timeout=10,label='team commands')
        exchange('chat',message='/team leave')
        wait_for(lambda:core.db.execute('SELECT count(*) FROM teams').fetchone()[0]==0,timeout=10,label='team leave')
        print('PASS friend and team commands from 1.9.4 clients',flush=True)
        exchange('chat',message='/rush');time.sleep(1)
        assert exchange('state')['window'], 'Rush menu missing'
        exchange('click',slot=11);exchange('close')
        exchange('chat',name='RushBotB',message='/rush 1v1')
        mid=wait_for(lambda:next(iter(core.active),None),label='automatic matchmaking')
        wait_for(lambda:processes.status(mid)=='ready',label='game process ready')
        logfile=root/'runtime/servers'/mid/'plugins/Rush/events-0.jsonl'
        def game_events():
            return [json.loads(line) for line in logfile.read_text().splitlines()] if logfile.exists() else []
        wait_for(lambda:any(e['event']=='game_started' for e in game_events()),timeout=60,label='transfer + countdown')
        print('PASS GUI queue → deployment → automatic transfer → game start',flush=True)
        def console(cmd):
            p=processes.children[mid]['process'];p.stdin.write((cmd+'\n').encode());p.stdin.flush()
        # Move players with server console to test combat without bridge pathfinding.
        console('tp RushBotA -32.5 65 2');console('tp RushBotB -30.5 65 2');time.sleep(3)
        # 1.8 cadence: hurt_ticks=20 blocks hits for 10 ticks (0.5 s), so the middle attack must be ignored.
        # No sleep before the 2nd attack: the bot round trip alone is ~0.2 s, well inside the window.
        exchange('attack',target='RushBotB')
        exchange('attack',target='RushBotB');time.sleep(0.8)
        exchange('attack',target='RushBotB');time.sleep(0.5)
        state=exchange('state',name='RushBotB')
        assert state['health']<20, state
        hits=[e for e in game_events() if e['event']=='pvp_hit']
        assert len(hits)==2 and all(h['damage']==4 for h in hits),hits
        print('PASS 1.8 hit cadence, full-damage attacks and structured diagnostics',flush=True)
        # 1.8 armor: iron chestplate = 6 points = 24 % (modern formula would give 16 %: 3.36).
        console('item replace entity RushBotB armor.chest with minecraft:iron_chestplate');time.sleep(0.5)
        exchange('attack',target='RushBotB');time.sleep(0.5)
        armored=[e for e in game_events() if e['event']=='pvp_hit'][-1]
        assert armored['armor']==6 and abs(armored['damage']-3.04)<0.01,armored
        print('PASS 1.8 armor reduction',flush=True)
        console('kill RushBotB')
        time.sleep(5)
        assert core.db.execute('SELECT state FROM matches WHERE id=?',(mid,)).fetchone()[0]=='ready'
        assert exchange('state',name='RushBotB')['health']==20
        print('PASS respawn while bed is intact',flush=True)
        # Own bed is protected, opponent bed is breakable.
        a=core.target('RushBotA');side=next(i for i,t in enumerate(core.active[mid]['teams']) if a in t)
        own=-32 if side==0 else 32;enemy=-own
        console(f'tp RushBotA {own} 65 3');console('give RushBotA minecraft:brick 4');time.sleep(1)
        exchange('chat',message='/shop');time.sleep(0.5)
        exchange('click',slot=0);time.sleep(0.5)
        inventory=exchange('state')['items']
        assert any(i['name']=='sandstone' and i['count']==16 for i in inventory),inventory
        exchange('click',slot=0);time.sleep(0.5)
        assert sum(i['count'] for i in exchange('state')['items'] if i['name']=='sandstone')==16
        exchange('close')
        console(f'summon minecraft:tnt {own} 65 2')
        wait_for(lambda:any(e['event']=='tnt_launch' for e in game_events()),timeout=10,label='TNT launch')
        assert exchange('state')['health']==20
        print('PASS shop payment, insufficient funds and TNT launch diagnostics',flush=True)
        console(f'tp RushBotA {own} 65 -1');time.sleep(1)
        exchange('dig',position=[own,65,-3]);time.sleep(0.5)
        assert not any(e['event']=='bed_destroyed' for e in game_events()),'Own bed destroyed'
        console(f'tp RushBotA {enemy} 65 -1');time.sleep(1)
        exchange('dig',position=[enemy,65,-3]);time.sleep(1)
        assert any(e['event']=='bed_destroyed' for e in game_events()),'Enemy bed not destroyed'
        console('kill RushBotB')
        wait_for(lambda:core.db.execute('SELECT state FROM matches WHERE id=?',(mid,)).fetchone()[0]=='finished',timeout=30,label='result callback')
        rows=core.db.execute("SELECT * FROM stats WHERE mode='global'").fetchall()
        assert len(rows)==2 and sum(r['wins'] for r in rows)==1
        print('PASS bed protection + bed destruction + elimination + ranked result',flush=True)
        wait_for(lambda:all(abs(exchange('state',name=n)['position']['x']-0.5)<1 for n in ('RushBotA','RushBotB')),timeout=30,label='lobby return')
        print('PASS automatic lobby return',flush=True)
        wins_before=core.db.execute('SELECT sum(wins) FROM stats').fetchone()[0]
        exchange('chat',message='/private create 1v1')
        code=wait_for(lambda:next(iter(core.rooms),None),timeout=10,label='private room')
        exchange('chat',name='RushBotB',message='/private join '+code);time.sleep(0.5)
        exchange('chat',message='/private start')
        private_id=wait_for(lambda:next(iter(core.active),None),timeout=10,label='private deployment')
        private_log=root/'runtime/servers'/private_id/'plugins/Rush/events-0.jsonl'
        wait_for(lambda:private_log.exists() and 'game_started' in private_log.read_text(),timeout=180,label='private game starts')
        exchange('chat',name='RushBotB',message='/rush leave')
        wait_for(lambda:core.db.execute('SELECT state FROM matches WHERE id=?',(private_id,)).fetchone()[0]=='finished',timeout=30,label='private finish')
        assert core.db.execute('SELECT sum(wins) FROM stats').fetchone()[0]==wins_before
        print('PASS private match deployment, forfeit and no ranked points',flush=True)
        exchange('quit')
        report={'result':'PASS','client':'1.9.4','bot_physics':False,'checks':['lobby','proxy','friends','teams','gui','matchmaking','deployment','transfer','pvp_full_damage','diagnostics','respawn','shop','tnt_launch','own_bed_protection','enemy_bed','elimination','leaderboard','lobby_return','private_match','forfeit'],'workspace':str(root)}
        report_path.write_text(json.dumps(report,indent=2))
        print(json.dumps(report,indent=2),flush=True)
    except BaseException as exc:
        report_path.write_text(json.dumps({'result':'FAIL','error':str(exc),'workspace':str(root)},indent=2))
        raise
    finally:
        if botproc and botproc.poll() is None:
            botproc.terminate();botproc.wait(timeout=10)
        stop.set();thread.join();server.shutdown();server.server_close();processes.close();core.db.close()


if __name__=='__main__':main()
