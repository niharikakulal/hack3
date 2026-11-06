import os
import shutil
from kvstore import KVStore

if os.path.exists("src.kvdb"):
    os.remove("src.kvdb")

db = KVStore("src.kvdb")
db.put(b"a", b"1")
db.batch([("put", b"x", b"1"), ("put", b"y", b"2"), ("del", b"a")])
db.close()
size = os.path.getsize("src.kvdb")

allowed = [
    {},                              # killed before the first put finished
    {b"a": b"1"},                    # put done, batch not
    {b"x": b"1", b"y": b"2"},        # batch fully applied (a deleted)
]
bad = 0
for cut in range(size + 1):
    shutil.copy("src.kvdb", "cut.kvdb")
    with open("cut.kvdb", "r+b") as f:
        f.truncate(cut)
    db = KVStore("cut.kvdb")
    state = dict(db.index)
    db.close()
    if state not in allowed:
        bad += 1
        print("BAD at cut", cut, state)

print(f"{size + 1} cut positions tested, {bad} bad")