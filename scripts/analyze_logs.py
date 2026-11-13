"""Summarize structured deployment and combat logs without extra packages."""
import collections
import json
import statistics
import sys
from pathlib import Path

root=Path(sys.argv[1] if len(sys.argv)>1 else 'runtime')
counts=collections.Counter()
hits=[]
for path in root.rglob('*.jsonl*'):
    for line in path.read_text(errors='replace').splitlines():
        try:
            event=json.loads(line)
        except json.JSONDecodeError:
            continue
        counts[event.get('event','unknown')]+=1
        if event.get('event')=='pvp_hit':hits.append(event)
print(json.dumps({'events':dict(counts),'pvp':{
    'hits':len(hits),
    'average_damage':statistics.mean(h['damage'] for h in hits) if hits else None,
    'average_distance':statistics.mean(h['distance'] for h in hits) if hits else None,
    'average_attacker_ping_ms':statistics.mean(h['attacker_ping'] for h in hits) if hits else None,
}},indent=2))
