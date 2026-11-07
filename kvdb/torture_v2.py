import argparse
import os
import random
import string
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
KVDB = os.path.join(HERE, "kvdb.py")
DB_PATH = os.path.join(HERE, "torture.kvdb")
ALPHABET = string.ascii_letters + string.digits
WINDOW = 20          # max operations sent but not yet acknowledged
MODES = ["default", "large-values", "large-batches", "recovery-crash"]


def rand_value(rng, mode):
    if mode == "large-values" and rng.random() < 0.5:
        n = rng.randint(100_000, 1_000_000)
    else:
        n = rng.randint(0, 60)
    return "".join(rng.choices(ALPHABET, k=n))


def make_op(rng, mode):
    """An operation is (kind, [(type, key, value), ...])."""
    nkeys = {"large-values": 10, "large-batches": 2000}.get(mode, 30)

    def key():
        return f"k{rng.randint(0, nkeys - 1)}"

    r = rng.random()
    if mode == "large-batches" and r < 0.5:
        items = [("put", key(), rand_value(rng, "default")) if rng.random() < 0.8
                 else ("del", key(), "")
                 for _ in range(rng.randint(1000, 10_000))]
        return ("BATCH", items)
    if r < 0.5:
        return ("PUT", [("put", key(), rand_value(rng, mode))])
    if r < 0.65:
        return ("DEL", [("del", key(), "")])
    items = [("put", key(), rand_value(rng, "default")) if rng.random() < 0.7
             else ("del", key(), "")
             for _ in range(rng.randint(2, 20))]
    return ("BATCH", items)


def encode(op_id, op):
    kind, items = op
    if kind == "PUT":
        _, k, v = items[0]
        return f"PUT {op_id} {k.encode().hex()} {v.encode().hex()}\n"
    if kind == "DEL":
        return f"DEL {op_id} {items[0][1].encode().hex()}\n"
    parts = [f"put:{k.encode().hex()}:{v.encode().hex()}" if t == "put"
             else f"del:{k.encode().hex()}" for t, k, v in items]
    return f"BATCH {op_id} " + " ".join(parts) + "\n"


def apply(state, items):
    for t, k, v in items:            # in order: later operations win
        if t == "put":
            state[k] = v
        else:
            state.pop(k, None)


def run_cmd(*args):
    return subprocess.run([sys.executable, KVDB, *args], capture_output=True)


def parse_dump(raw):
    state = {}
    for line in raw.split(b"\n"):
        if line:
            k, _, v = line.partition(b"\t")
            state[k.decode()] = v.decode()
    return state


def fail(msg):
    print(f"\nFAIL: {msg}")
    sys.exit(1)


def one_iteration(rng, it, mode, oracle):
    """Start serve, hammer it with two writers, kill it at a random moment.
    Returns (ops sent in order, number of ACKs, problems, kill delay in seconds)."""
    proc = subprocess.Popen([sys.executable, KVDB, "serve", DB_PATH],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    sent, acks, problems = [], [], []
    send_lock = threading.Lock()
    stop, ready = threading.Event(), threading.Event()
    window = threading.Semaphore(WINDOW)

    def reader():
        if proc.stdout.readline().strip() == "READY":
            ready.set()
        for line in proc.stdout:
            if not line.endswith("\n"):
                break                                   # cut off by the kill: not an ACK
            parts = line.split()
            if len(parts) == 2 and parts[0] == "ACK":
                op_id = int(parts[1])
                if op_id != len(acks) + 1:
                    problems.append(f"ACK {op_id} arrived out of order")
                    break
                acks.append(op_id)
                oracle.write(f"iter {it} ACK {op_id}\n")
                oracle.flush()
                window.release()
            else:
                problems.append(f"unexpected output from serve: {line.strip()!r}")
                break

    def writer(wrng):
        while not ready.wait(0.05):
            if stop.is_set():
                return
        while not stop.is_set():
            if not window.acquire(timeout=0.05):
                continue
            with send_lock:                             # keeps the two writers' lines apart
                if stop.is_set():
                    return
                op = make_op(wrng, mode)
                sent.append(op)
                try:
                    proc.stdin.write(encode(len(sent), op))
                    proc.stdin.flush()
                except (BrokenPipeError, OSError, ValueError):
                    return

    rt = threading.Thread(target=reader, daemon=True)
    rt.start()
    wts = [threading.Thread(target=writer, args=(random.Random(rng.random()),), daemon=True)
           for _ in range(2)]
    for t in wts:
        t.start()

    lo, hi = (0.2, 2.0) if mode in ("large-values", "large-batches") else (0.05, 0.8)
    delay = rng.uniform(lo, hi)
    time.sleep(delay)                                   # the random kill time
    stop.set()
    proc.kill()                                         # no warning, no cleanup
    proc.wait()
    rt.join(timeout=10)                                 # read every ACK that was printed
    for t in wts:
        t.join(timeout=2)
    for s in (proc.stdin, proc.stdout):
        try:
            s.close()
        except (OSError, ValueError):
            pass
    return list(sent), len(acks), problems, delay


def kill_during_open(rng):
    p = subprocess.Popen([sys.executable, KVDB, "open", DB_PATH],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(rng.uniform(0.0, 0.25))
    p.kill()
    p.wait()


def short(v):
    if v is None:
        return None
    return repr(v) if len(v) <= 40 else repr(v[:40]) + f"... ({len(v)} chars)"


def report_failure(it, L, sent, S_L, S_L1, actual):
    print(f"\nFAIL at iteration {it}")
    print(f"  acknowledged operations: 1 to {L}")
    if S_L1 is not None:
        kind, items = sent[L]
        print(f"  in-flight operation: id {L + 1} ({kind}, {len(items)} item(s))")
    bad = []
    for k in sorted(set(S_L) | set(actual) | set(S_L1 or {})):
        allowed = {S_L.get(k)} | ({S_L1.get(k)} if S_L1 is not None else set())
        if actual.get(k) not in allowed:
            bad.append(k)
    for k in bad[:10]:
        print(f"  key {k}: expected {short(S_L.get(k))}"
              + (f" or {short(S_L1.get(k))}" if S_L1 is not None else "")
              + f", found {short(actual.get(k))}")
    if not bad:
        print("  every key is individually explainable, but the whole state matches")
        print("  neither allowed state: an operation (probably a batch) is only")
        print("  partially applied.")
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=500)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--mode", choices=MODES, default="default")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    for suffix in ("", "~"):
        if os.path.exists(DB_PATH + suffix):
            os.remove(DB_PATH + suffix)

    model = {}                       # database state we expect at the start of each iteration
    total_acked = 0
    with open(os.path.join(HERE, "oracle.log"), "w") as oracle:
        for it in range(1, args.iterations + 1):
            sent, L, problems, delay = one_iteration(rng, it, args.mode, oracle)
            total_acked += L
            if problems:
                fail(f"iteration {it}: {problems[0]}")

            if args.mode == "recovery-crash":
                for _ in range(rng.randint(1, 3)):
                    kill_during_open(rng)

            if run_cmd("open", DB_PATH).returncode != 0:
                fail(f"iteration {it}: open() failed after a kill")
            chk = run_cmd("check", DB_PATH)
            if chk.returncode != 0 or not chk.stdout.startswith(b"OK"):
                fail(f"iteration {it}: check said: {chk.stdout.decode().strip()}")
            dump = run_cmd("dump", DB_PATH)
            if dump.returncode != 0:
                fail(f"iteration {it}: dump failed")
            actual = parse_dump(dump.stdout)

            S_L = dict(model)                       # exactly the acknowledged operations
            for op in sent[:L]:
                apply(S_L, op[1])
            S_L1 = None                             # ... plus the one in-flight operation
            if len(sent) > L:
                S_L1 = dict(S_L)
                apply(S_L1, sent[L][1])

            if actual == S_L:
                outcome = "in-flight lost" if S_L1 is not None else "no in-flight"
            elif S_L1 is not None and actual == S_L1:
                outcome = "in-flight applied"
            else:
                report_failure(it, L, sent, S_L, S_L1, actual)

            model = actual
            print(f"iter {it}: kill {int(delay * 1000)}ms, {L} acked, {outcome}, "
                  f"{len(model)} keys, PASS")

    if total_acked == 0:
        fail("no operation was ever acknowledged, so this run proved nothing")
    print(f"\nDone: {args.iterations}/{args.iterations} passed "
          f"(seed {args.seed}, mode {args.mode}, {total_acked} acknowledged operations)")


main()