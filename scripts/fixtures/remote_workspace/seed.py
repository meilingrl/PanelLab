"""Disposable Linux SSH fixture; run only inside the verification container."""
from pathlib import Path
import os
import pwd

home = Path('/home/lab')
root = home / 'experiments' / '演示训练 run A'
root.mkdir(parents=True, exist_ok=True)
(root / 'outputs').mkdir(exist_ok=True)
(root / 'config.json').write_text('{"fixture": true, "epochs": 100}\n')
(root / 'outputs' / 'result.json').write_text('{"fixture": true, "score": 0.42}\n')
(root / "quote's result.txt").write_text('safe quoted filename\n')
(root / 'large.log').write_text('x' * 70000 + '\nTAIL_MARKER\n')
with (root / 'too-large.bin').open('wb') as f:
    f.truncate(17 * 1024 * 1024)
(root / 'outside-link').symlink_to('/etc')
os.mkfifo(root / 'named-pipe')
(root / 'train.log').write_text('PanelLab fixture: simulated training output, not an ML experiment\n')
(root / 'train_demo.py').write_text('''import time
from pathlib import Path
root = Path(__file__).parent
for step in range(10000):
    with (root / 'train.log').open('a') as log:
        log.write(f'fixture_step={step} status=running\\n')
    time.sleep(2)
''')
user = pwd.getpwnam('lab')
for directory, subdirs, files in os.walk(home):
    os.chown(directory, user.pw_uid, user.pw_gid)
    for name in subdirs + files:
        os.chown(Path(directory) / name, user.pw_uid, user.pw_gid, follow_symlinks=False)
print(str(root))
