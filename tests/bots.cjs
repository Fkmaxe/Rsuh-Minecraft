// Real Minecraft 1.9.4 clients; only used against the isolated localhost test proxy.
const mineflayer = require('../.cache/bots/node_modules/mineflayer');
const readline = require('node:readline');
const bots = {};
const output = (data) => process.stdout.write(JSON.stringify(data)+'\n');
for (const name of ['RushBotA','RushBotB']) {
  // Positions are controlled by test-console teleports. Mineflayer 4.33's legacy
  // physics emits NaN positions here; do not confuse this with server PvP physics.
  const bot = mineflayer.createBot({host:'127.0.0.1',port:Number(process.argv[2]),username:name,auth:'offline',version:'1.9.4',hideErrors:true,physicsEnabled:false});
  bots[name]=bot;
  const write=bot._client.write.bind(bot._client);
  bot._client.write=(packet,data)=> {
    if(['position','position_look','look'].includes(packet) && Object.values(data).some(v=>typeof v==='number'&&!Number.isFinite(v)))
      output({event:'error',name,error:'Non-finite bot movement',packet,data,position:bot.entity?.position,attributes:bot.entity?.attributes});
    return write(packet,data);
  };
  bot.on('spawn',()=>output({event:'spawn',name}));
  bot.on('messagestr',message=>output({event:'chat',name,message}));
  bot.on('kicked',reason=>output({event:'kicked',name,reason}));
  bot.on('error',error=>output({event:'error',name,error:String(error)}));
  bot.on('windowOpen',w=>output({event:'window',name,title:w.title}));
}
readline.createInterface({input:process.stdin}).on('line', async line=> {
  let req;
  try {
    req=JSON.parse(line);const bot=bots[req.name || 'RushBotA'];
    let result;
    if(req.action==='chat'){bot.chat(req.message);result=true;}
    if(req.action==='state') result={position:bot.entity.position,health:bot.health,items:bot.inventory.items().map(i=>({name:i.name,count:i.count})),window:bot.currentWindow?.title};
    if(req.action==='click'){await bot.clickWindow(req.slot,0,0);result=true;}
    if(req.action==='close'){if(bot.currentWindow)bot.closeWindow(bot.currentWindow);result=true;}
    if(req.action==='attack') {
      const target=bot.players[req.target]?.entity;if(!target)throw new Error('Target not visible');
      await bot.lookAt(target.position.offset(0,1.5,0));bot.attack(target);result=true;
    }
    if(req.action==='dig') {
      const Vec3=require('../.cache/bots/node_modules/vec3').Vec3;
      const block=bot.blockAt(new Vec3(...req.position));if(!block)throw new Error('Block not loaded');
      await bot.dig(block);result={block:block.name};
    }
    if(req.action==='quit'){for(const b of Object.values(bots))b.quit();setTimeout(()=>process.exit(0),500);result=true;}
    output({id:req.id,result});
  } catch(error){output({id:req?.id,error:String(error)});}
});
