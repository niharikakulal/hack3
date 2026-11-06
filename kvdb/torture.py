import os
import random
import subprocess
import sys

from kvstore import KVStore

PATH = "torture.log"
ROUNDS = 30

if os.path.exists(PATH):
    os.remove(PATH)

acked = []      # every write number the database promised was safe
next_i = 0

for r in range(1, ROUNDS + 1):
    proc = subprocess.Popen(
        [sys.executable, "writer.py", PATH, str(next_i)],
        stdout=subprocess.PIPE,
        text=True,
    )
    target = random.randint(1, 200)
    for _ in range(target):
        line = proc.stdout.readline()
        if not line:
            break
        acked.append(int(line.split()[1]))

    proc.kill()          # the "crash": no warning, no cleanup
    proc.wait()
    proc.stdout.close()

    next_i = acked[-1] + 1 if acked else 0

    db = KVStore(PATH)   # recovery happens here
    missing = [i for i in acked if db.get(f"k{i}") != f"v{i}"]
    db.log.close()

    if missing:
        print(f"Round {r}: FAIL, {len(missing)} acknowledged writes lost, e.g. k{missing[0]}")
        sys.exit(1)
    print(f"Round {r}: PASS ({len(acked)} acknowledged writes all present)")

print("All rounds passed.")