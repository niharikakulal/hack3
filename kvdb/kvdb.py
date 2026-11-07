import sys
import time
import zlib

from kvstore import KVStore, DiskFullError, MAGIC, FRAME_HDR

USAGE = ("usage: kvdb.py [--max-bytes N] "
         "<open|put|get|del|batch|dump|check|compact|serve> <path> [...]")

# how many arguments each command takes after <path>
ARG_COUNTS = {
    "open": 0, "put": 2, "get": 1, "del": 1, "batch": 1,
    "dump": 0, "check": 0, "compact": 0, "serve": 0,
}


def take_max_bytes(args):
    """Pull '--max-bytes N' out of the argument list, if present."""
    if "--max-bytes" in args:
        i = args.index("--max-bytes")
        if i + 1 >= len(args):
            raise ValueError("--max-bytes requires a value")
        try:
            value = int(args[i + 1])
        except ValueError:
            raise ValueError("--max-bytes must be a whole number")
        del args[i:i + 2]
        return value
    return None


def out(data):
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def cmd_open(path, max_bytes):
    t = time.perf_counter()
    db = KVStore(path, max_bytes)
    ms = (time.perf_counter() - t) * 1000
    print(f"keys={len(db.index)} recovery_ms={ms:.1f}")
    db.close()


def cmd_put(path, max_bytes, key, value):
    db = KVStore(path, max_bytes)
    db.put(key.encode(), value.encode())
    db.close()
    print("OK")


def cmd_get(path, max_bytes, key):
    db = KVStore(path, max_bytes)
    value = db.get(key.encode())
    db.close()
    if value is None:
        print("NOTFOUND")
    else:
        out(value + b"\n")


def cmd_del(path, max_bytes, key):
    db = KVStore(path, max_bytes)
    db.delete(key.encode())
    db.close()
    print("OK")


def cmd_batch(path, max_bytes, file):
    ops = []
    with open(file, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if not line:
                continue
            parts = line.split(" ", 2)
            if parts[0] == "put":
                if len(parts) < 2:
                    raise ValueError("bad put line")
                value = parts[2] if len(parts) > 2 else ""
                ops.append(("put", parts[1].encode(), value.encode()))
            elif parts[0] == "del":
                if len(parts) < 2:
                    raise ValueError("bad del line")
                ops.append(("del", parts[1].encode()))
            else:
                print("ERROR bad line:", line)
                sys.exit(1)

    db = KVStore(path, max_bytes)
    db.batch(ops)
    db.close()
    print("OK")


def cmd_dump(path, max_bytes):
    db = KVStore(path, max_bytes)
    for key, value in sorted(db.index.items()):
        out(key + b"\t" + value + b"\n")
    db.close()


def cmd_compact(path, max_bytes):
    db = KVStore(path, max_bytes)
    before = db.size
    db.compact()
    after = db.size
    keys = len(db.index)
    db.close()
    print(f"OK keys={keys} bytes_before={before} bytes_after={after}")


def cmd_check(path):
    """Read-only integrity check. Does not repair anything."""
    with open(path, "rb") as f:
        data = f.read()

    if data[:len(MAGIC)] != MAGIC:
        print("BAD: header missing or wrong")
        sys.exit(1)

    pos = len(MAGIC)
    n = len(data)
    frames = 0

    while pos + FRAME_HDR.size <= n:
        length, crc = FRAME_HDR.unpack_from(data, pos)
        start = pos + FRAME_HDR.size
        end = start + length

        if end > n or zlib.crc32(data[start:end]) != crc:
            break

        if KVStore._decode(data[start:end]) is None:
            print(f"BAD: frame at byte {pos} has a valid checksum but cannot be decoded")
            sys.exit(1)

        frames += 1
        pos = end

    tail = n - pos
    if tail == 0:
        print(f"OK ({frames} frames)")
    else:
        print(f"OK ({frames} frames; {tail} unacknowledged tail bytes, open() will remove them)")


def cmd_serve(path, max_bytes):
    db = KVStore(path, max_bytes)
    print("READY", flush=True)
    counter = 0          # operation numbers for the plain-text commands
    batch = None         # None = not inside a batch, otherwise the queued operations
    for line in sys.stdin:
        line = line.rstrip("\r\n")
        words = line.split(" ", 2)
        cmd = words[0]

        # ---- plain-text commands (lowercase): easy to type by hand ----
        if cmd in ("put", "del", "get", "begin", "commit", "abort"):
            if cmd == "get":
                if len(words) < 2:
                    print("ERR usage: get <key>", flush=True)
                    continue
                value = db.get(words[1].encode())
                if value is None:
                    print("NOTFOUND", flush=True)
                else:
                    print("VALUE " + value.decode(errors="replace"), flush=True)
                continue
            if cmd == "begin":
                if batch is not None:
                    print("ERR already in a batch", flush=True)
                else:
                    batch = []
                    print("BEGIN", flush=True)
                continue
            if cmd == "abort":
                if batch is None:
                    print("ERR not in a batch", flush=True)
                else:
                    batch = None
                    print("ABORTED", flush=True)
                continue
            if cmd == "commit":
                if batch is None:
                    print("ERR not in a batch", flush=True)
                    continue
                ops, batch = batch, None
                if not ops:
                    print("ERR empty batch", flush=True)
                    continue
                counter += 1
                try:
                    db.batch(ops)
                except DiskFullError:
                    print(f"ERR {counter} diskfull", flush=True)
                    continue
                except ValueError:
                    print(f"ERR {counter} invalid", flush=True)
                    continue
                print(f"ACK {counter}", flush=True)   # printed only after the fsync
                continue
            # put or del
            if len(words) < 2:
                print("ERR usage: put <key> <value>  or  del <key>", flush=True)
                continue
            key = words[1].encode()
            if cmd == "put":
                value = words[2].encode() if len(words) > 2 else b""
                op = ("put", key, value)
            else:
                op = ("del", key)
            if batch is not None:
                batch.append(op)                       # queued only, nothing written yet
                print(f"QUEUED ({len(batch)} in batch)", flush=True)
                continue
            counter += 1
            try:
                if op[0] == "put":
                    db.put(op[1], op[2])
                else:
                    db.delete(op[1])
            except DiskFullError:
                print(f"ERR {counter} diskfull", flush=True)
                continue
            except ValueError:
                print(f"ERR {counter} invalid", flush=True)
                continue
            print(f"ACK {counter}", flush=True)
            continue

        # ---- hex protocol (uppercase): used by the torture tool ----
        parts = line.split(" ")
        if len(parts) < 2:
            continue
        cmd, op_id = parts[0], parts[1]
        try:
            if cmd == "PUT":
                db.put(bytes.fromhex(parts[2]), bytes.fromhex(parts[3]))
            elif cmd == "DEL":
                db.delete(bytes.fromhex(parts[2]))
            elif cmd == "BATCH":
                ops = []
                for tok in parts[2:]:
                    f = tok.split(":")
                    if f[0] == "put":
                        ops.append(("put", bytes.fromhex(f[1]), bytes.fromhex(f[2])))
                    else:
                        ops.append(("del", bytes.fromhex(f[1])))
                db.batch(ops)
            else:
                print(f"ERR {op_id} unknown-command", flush=True)
                continue
        except DiskFullError:
            print(f"ERR {op_id} diskfull", flush=True)
            continue
        except (ValueError, IndexError):
            print(f"ERR {op_id} invalid", flush=True)
            continue
        print(f"ACK {op_id}", flush=True)
    db.close()


def main():
    args = sys.argv[1:]

    try:
        max_bytes = take_max_bytes(args)

        if len(args) < 2:
            print(USAGE)
            sys.exit(1)

        cmd, path, rest = args[0], args[1], args[2:]

        if cmd not in ARG_COUNTS:
            print("unknown command:", cmd)
            print(USAGE)
            sys.exit(1)
        if len(rest) != ARG_COUNTS[cmd]:
            print(f"ERROR: '{cmd}' takes {ARG_COUNTS[cmd]} argument(s) after the path")
            print(USAGE)
            sys.exit(1)

        if cmd == "open":
            cmd_open(path, max_bytes)
        elif cmd == "put":
            cmd_put(path, max_bytes, *rest)
        elif cmd == "get":
            cmd_get(path, max_bytes, *rest)
        elif cmd == "del":
            cmd_del(path, max_bytes, *rest)
        elif cmd == "batch":
            cmd_batch(path, max_bytes, *rest)
        elif cmd == "dump":
            cmd_dump(path, max_bytes)
        elif cmd == "check":
            cmd_check(path)
        elif cmd == "compact":
            cmd_compact(path, max_bytes)
        elif cmd == "serve":
            cmd_serve(path, max_bytes)

    except DiskFullError:
        print("ERROR: disk full (size limit reached)")
        sys.exit(1)
    except (ValueError, IndexError, OSError) as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)       # Ctrl+C: stop quietly, without a traceback


main()