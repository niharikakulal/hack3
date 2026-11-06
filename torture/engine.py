import sys

if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "dump":
        for k in sorted(db):
            print(k, db[k])
    elif cmd == "serve":
        for line in sys.stdin:
            p = line.rstrip("\n").split(" ", 3)   # "put <id> <key> <value>" or "del <id> <key>"
            if p[0] == "put":
                put(p[2], p[3])
            elif p[0] == "del":
                delete(p[2])
            print("ACK", p[1], flush=True)       # only AFTER fsync inside put/delete