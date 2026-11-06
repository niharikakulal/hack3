import sys
from kvstore import KVStore

path = sys.argv[1]
start = int(sys.argv[2])

db = KVStore(path)
i = start
while True:
    db.put(f"k{i}", f"v{i}")
    print(f"ACK {i}", flush=True)   # only printed AFTER put() returned
    i += 1