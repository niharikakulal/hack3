import os
import random
import shutil

from kvstore import KVStore

SRC = "torn_src.log"
TEST = "torn_test.log"
N = 200

for f in (SRC, TEST):
    if os.path.exists(f):
        os.remove(f)

# build a clean log with N records
db = KVStore(SRC)
for i in range(N):
    db.put(f"k{i}", f"v{i}")
db.log.close()
size = os.path.getsize(SRC)


def check(label, trial):
    """After damage, the data must be exactly the first n writes, nothing else."""
    db = KVStore(TEST)
    n = len(db.data)
    expected = {f"k{i}": f"v{i}" for i in range(n)}
    if db.data != expected:
        print(f"{label} trial {trial}: FAIL (data is not a clean prefix)")
        raise SystemExit(1)
    # the store must still work after recovery
    db.put("after", "crash")
    db.log.close()
    db = KVStore(TEST)
    if db.get("after") != "crash":
        print(f"{label} trial {trial}: FAIL (new write lost after recovery)")
        raise SystemExit(1)
    db.log.close()


# Test A: cut the file at a random byte (a torn write)
for t in range(1, 201):
    shutil.copy(SRC, TEST)
    with open(TEST, "r+b") as f:
        f.truncate(random.randint(0, size))
    check("truncate", t)
print("Truncation test: 200 trials PASS")

# Test B: flip one random byte (corruption)
for t in range(1, 201):
    shutil.copy(SRC, TEST)
    with open(TEST, "r+b") as f:
        pos = random.randint(0, size - 1)
        f.seek(pos)
        b = f.read(1)
        f.seek(pos)
        f.write(bytes([b[0] ^ 0xFF]))
    check("bitflip", t)
print("Bit-flip test: 200 trials PASS")