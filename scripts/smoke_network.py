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
from orchestrator.maps import Maps
from orchestrator.history import History
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
    log=make_logger(root/'runtime/events.jsonl')
    processes=Processes(root,settings,token,secrets.token_hex(32),log)
    history=History(root/'runtime/history')
    core=Core(root/'runtime/rush.sqlite3',processes,log,maps=Maps(root/'runtime/maps'),history=history)
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
        def chat_seen(name,text):
            drain();return any(e.get('event')=='chat' and e.get('name')==name and text in e.get('message','') for e in events)
        exchange('chat',message='/friend add RushBotB')
        wait_for(lambda:chat_seen('RushBotB','veut être ton ami') and chat_seen('RushBotB','[Accepter]'),timeout=10,label='friend request notification')
        exchange('chat',name='RushBotB',message='/friend accept RushBotA')
        wait_for(lambda:core.db.execute('SELECT count(*) FROM friends WHERE accepted=1').fetchone()[0]==1,timeout=10,label='friend commands')
        exchange('chat',message='/team invite RushBotB');time.sleep(0.5)
        wait_for(lambda:chat_seen('RushBotB','t’invite dans son équipe'),timeout=10,label='team invite notification')
        exchange('chat',name='RushBotB',message='/team accept RushBotA')
        wait_for(lambda:chat_seen('RushBotA','a rejoint l’équipe'),timeout=10,label='team join notification')
        wait_for(lambda:core.db.execute('SELECT count(*) FROM teams').fetchone()[0]==2,timeout=10,label='team commands')
        exchange('chat',message='/team leave')
        wait_for(lambda:core.db.execute('SELECT count(*) FROM teams').fetchone()[0]==0,timeout=10,label='team leave')
        print('PASS friend and team commands with clickable notifications',flush=True)
        exchange('chat',message='/rush');time.sleep(1)
        assert exchange('state')['window'], 'Rush menu missing'
        exchange('click',slot=20)  # 1v1 button; the menu closes itself.
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
        exchange('click',slot=10);time.sleep(0.5)  # Category « Blocs »
        exchange('click',slot=10);time.sleep(0.5)  # 16 sandstone for 4 bronze
        inventory=exchange('state')['items']
        assert any(i['name']=='sandstone' and i['count']==16 for i in inventory),inventory
        exchange('click',slot=10);time.sleep(0.5)
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
        wait_for(lambda:chat_seen('RushBotA','Elo') and chat_seen('RushBotB','Elo'),timeout=10,label='Elo result notification')
        print('PASS bed protection + bed destruction + elimination + ranked result + Elo notification',flush=True)
        wait_for(lambda:all(abs(exchange('state',name=n)['position']['x']-0.5)<1 for n in ('RushBotA','RushBotB')),timeout=30,label='lobby return')
        print('PASS automatic lobby return',flush=True)
        def ranked_record():
            path=history.find(mid);record=path and json.loads(path.read_text())
            return record if record and record['chronologie'] else None
        record=wait_for(ranked_record,timeout=40,label='game history timeline')
        assert record['statut']=='terminee' and record['vainqueur'] in ('rouge','bleu') and record['classee'],record
        steps=[e['evenement'] for e in record['chronologie']]
        assert steps[0]=='debut' and 'lit_detruit' in steps and 'mort' in steps and steps[-1]=='fin',steps
        assert all(row['elo_variation'] in (16,-16) for team in record['equipes'].values() for row in team),record['equipes']
        print('PASS game history JSON: result, Elo before/after, timeline',flush=True)
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
        # Map editor: create from the classic template, edit, save and leave.
        wait_for(lambda:abs(exchange('state')['position']['x']-0.5)<1 and not core.active,timeout=30,label='lobby before editor')
        core.set_role(core.target('RushBotA'),'admin')
        exchange('chat',message='/map create arene classic')
        wait_for(lambda:(root/'runtime/maps/arene/map.json').exists(),timeout=10,label='map created')
        exchange('chat',message='/map edit arene')
        editor_id=wait_for(lambda:next(iter(core.editors),None),timeout=10,label='editor deployment')
        wait_for(lambda:processes.status(editor_id)=='ready',label='editor ready')
        wait_for(lambda:chat_seen('RushBotA','Éditeur de'),timeout=30,label='editor transfer')
        time.sleep(1)
        exchange('chat',message='/map gen or');time.sleep(0.5)
        exchange('chat',message='/map pnj armes bleu');time.sleep(0.5)
        exchange('chat',message='/map save')
        def saved():
            m=json.loads((root/'runtime/maps/arene/map.json').read_text())
            return m['layout'] is None and (root/'runtime/maps/arene/world/region').is_dir() and m
        try:
            saved_map=wait_for(saved,timeout=30,label='map saved')
        except AssertionError:
            drain();print('Editor chat:',[e['message'] for e in events if e.get('event')=='chat' and e.get('name')=='RushBotA' and 'sélectionné' not in e['message']][-12:],flush=True)
            shutil.copy(root/'runtime/servers'/editor_id/'console.log',root/'editor-console.log');shutil.copytree(root/'runtime/servers'/editor_id/'plugins/Rush',root/'editor-rush',symlinks=True);print('Editor console kept in',root/'editor-console.log',flush=True);raise
        assert len(saved_map['generators'])==6 and len(saved_map['npcs'])==9,(len(saved_map['generators']),len(saved_map['npcs']))
        regions=list((root/'runtime/maps/arene/world/region').glob('*.mca'))
        # Region files are written in 4 KiB sectors: any other size means a copy taken mid-write.
        assert regions and all(r.stat().st_size%4096==0 for r in regions),[(r.name,r.stat().st_size) for r in regions]
        assert not (root/'runtime/maps/arene/world/playerdata').exists(),'player data leaked into the map'
        wait_for(lambda:chat_seen('RushBotA','enregistrée'),timeout=15,label='map saved notification')
        def editor_messages():
            drain();return sum(e.get('event')=='chat' and e.get('name')=='RushBotA' and 'Éditeur de' in e.get('message','') for e in events)
        try:
            wait_for(lambda:editor_messages()>=2,timeout=60,label='editor reopened after save')
        except AssertionError:
            for folder in (root/'runtime/servers').glob('edit-*'):shutil.copy(folder/'console.log',root/(folder.name+'-console.log'))
            drain();print('Bot events:',[e for e in events if e.get('name')=='RushBotA' and e.get('event')!='chat'][-8:],flush=True);raise
        time.sleep(1)
        exchange('chat',message='/map quitter')
        wait_for(lambda:not core.editors,timeout=15,label='editor closed')
        wait_for(lambda:abs(exchange('state')['position']['x']-0.5)<1,timeout=30,label='editor lobby return')
        exchange('chat',message='/map enable arene');time.sleep(1)
        assert json.loads((root/'runtime/maps/arene/map.json').read_text())['enabled']
        assert Maps(root/'runtime/maps').choose('1v1')[0]['name'] in ('arene','classic')
        exchange('chat',message='/map disable classic');time.sleep(1)
        import uuid as _uuid
        with core.lock:game=core.launch('1v1',[[str(_uuid.uuid4())],[str(_uuid.uuid4())]],False)
        assert core.active[game]['map']['name']=='arene'
        game_log=root/'runtime/servers'/game/'plugins/Rush/events-0.jsonl'
        created=wait_for(lambda:game_log.exists() and next((json.loads(l) for l in game_log.read_text().splitlines() if '"arena_created"' in l),None),timeout=120,label='game on saved map')
        assert created['map']=='arene' and created['beds']==[True,True] and created['npcs']==9,created
        core.abort(game,'deployment_test_complete')
        print('PASS map editor: create, edit, save world + NPC/generator, leave, enable, game on saved map',flush=True)
        # Moderation: vanish, freeze, spectate in vanish + inventory, mute, report, staff menu, ban.
        wait_for(lambda:abs(exchange('state')['position']['x']-0.5)<1,timeout=30,label='lobby before moderation')
        exchange('chat',message='/vanish')
        wait_for(lambda:'RushBotA' not in exchange('players',name='RushBotB'),timeout=10,label='vanished from TAB')
        exchange('chat',message='/vanish')
        wait_for(lambda:'RushBotA' in exchange('players',name='RushBotB'),timeout=10,label='visible again')
        exchange('chat',message='/freeze RushBotB')
        wait_for(lambda:chat_seen('RushBotB','Tu es gelé'),timeout=10,label='freeze notification');time.sleep(1.5)
        exchange('chat',name='RushBotB',message='/rush 1v1')
        wait_for(lambda:chat_seen('RushBotB','Commandes bloquées'),timeout=10,label='frozen command blocked')
        exchange('chat',message='/freeze RushBotB')
        wait_for(lambda:chat_seen('RushBotB','plus gelé'),timeout=10,label='unfreeze')
        with core.lock:watched=core.launch('1v1',[[core.target('RushBotB')],[str(_uuid.uuid4())]],False)
        wait_for(lambda:processes.status(watched)=='ready' and core.active[watched]['state']=='ready',timeout=120,label='watched game')
        wait_for(lambda:chat_seen('RushBotB','Tu joues dans l’équipe'),timeout=30,label='player in watched game')
        exchange('chat',message='/spec RushBotB')
        wait_for(lambda:chat_seen('RushBotA','Tu observes'),timeout=30,label='spectator transfer')
        time.sleep(2)
        assert 'RushBotA' not in exchange('players',name='RushBotB'),'spectator visible to the player'
        exchange('chat',message='/freeze RushBotB')
        wait_for(lambda:chat_seen('RushBotB','Tu es gelé'),timeout=10,label='freeze in game')
        exchange('chat',message='/freeze RushBotB');time.sleep(1)
        exchange('chat',message='/invsee RushBotB');time.sleep(1)
        assert 'Inventaire' in str(exchange('state')['window']),exchange('state')['window']
        exchange('close')
        exchange('chat',message='/rush leave')
        wait_for(lambda:abs(exchange('state')['position']['x']-0.5)<1,timeout=30,label='spectator back to lobby')
        core.abort(watched,'moderation')
        watched_record=json.loads(history.find(watched).read_text())
        actions=[m['action'] for m in watched_record['moderation']]
        assert actions==['spectateur_staff','freeze','degel'] and watched_record['statut']=='annulee',(actions,watched_record['statut'])
        assert watched_record['moderation'][1]['par']=='RushBotA'
        wait_for(lambda:abs(exchange('state',name='RushBotB')['position']['x']-0.5)<1,timeout=30,label='player back to lobby')
        exchange('chat',message='/mute RushBotB 30m spam');time.sleep(1.5)
        exchange('chat',name='RushBotB',message='message interdit')
        wait_for(lambda:chat_seen('RushBotB','réduit au silence'),timeout=10,label='mute enforced')
        assert not chat_seen('RushBotA','message interdit'),'muted message delivered'
        exchange('chat',message='/unmute RushBotB')
        exchange('chat',name='RushBotB',message='/report RushBotA test signalement')
        wait_for(lambda:chat_seen('RushBotA','Signalement') and chat_seen('RushBotA','[Spec]'),timeout=10,label='report to staff')
        exchange('chat',message='/staff');time.sleep(1)
        assert 'Staff' in str(exchange('state')['window']),exchange('state')['window']
        exchange('close')
        exchange('chat',message='/ban RushBotB 1h triche')
        wait_for(lambda:(drain() or True) and any(e.get('event')=='kicked' and e.get('name')=='RushBotB' and 'banni' in str(e.get('reason')) for e in events),timeout=10,label='ban kick')
        again=exchange('reconnect',name='RushBotB')
        assert again.startswith('kicked') and 'banni' in again,again
        print('PASS moderation: vanish, freeze, spectate in vanish + invsee, mute, report, staff menu, ban',flush=True)
        exchange('quit')
        report={'result':'PASS','client':'1.9.4','bot_physics':False,'checks':['lobby','proxy','friends','teams','gui','matchmaking','deployment','transfer','pvp_full_damage','diagnostics','respawn','shop','tnt_launch','own_bed_protection','enemy_bed','elimination','leaderboard','lobby_return','private_match','forfeit','notifications','elo_notification','map_editor','moderation','game_history'],'workspace':str(root)}
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
