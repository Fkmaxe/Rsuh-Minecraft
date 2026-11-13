"""Download pinned upstream artifacts and verify SHA-256 before installing."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def main():
    folder=ROOT/'artifacts'
    folder.mkdir(exist_ok=True)
    for name, artifact in json.loads((ROOT/'config/artifacts.lock.json').read_text()).items():
        dest=folder/name
        if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest()==artifact['sha256']:
            print(name, 'verified (cached)')
            continue
        temp=dest.with_suffix('.tmp')
        subprocess.run(['curl','--fail','--location','--silent','--show-error','--retry','3','--max-time','180',
                        '--user-agent','RushTraining/1.0 (local development)',artifact['url'],'--output',str(temp)],check=True)
        data=temp.read_bytes()
        if hashlib.sha256(data).hexdigest()!=artifact['sha256']:
            raise RuntimeError('SHA-256 mismatch: '+name)
        temp.replace(dest)
        print(name, artifact['version'], 'verified', flush=True)


if __name__=='__main__':main()
