 subprocess, threading, random, time, sys, os

ENGINE = [sys.executable, "engine.py"]
acked = {}      # key -> last ACKed value (None = deleted)
maybe = {}      # key -> set of values from un-ACKed ops

def run_iteration(rng, n):import
    pending = {}            # op id -> (key, value)
    lock = threading.Lock()
    counter = [0]

    proc = subprocess.Popen(ENGINE + ["serve"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True)

    def reader():                       # collects ACKs
        for line in proc.stdout:
            parts = line.split()
            if len(parts) == 2 and parts[0] == "ACK":
                op = int(parts[1])
                with lock:
                    if op in pending:
                        key, val = pending.pop(op)
                        acked[key] = val
                        for o in [o for o, (k, _) in pending.items() if k == key and o < op]:
                            del pending[o]      # older ops can't be final

    def writer(wid, wrng):              # sends random ops
        try:
            while True:
                key = f"w{wid}_k{wrng.randint(0, 20)}"   # each writer has its own keys
                with lock:
                    counter[0] += 1
                    op = counter[0]
                    if wrng.random() < 0.8:
                        val = f"v{op}"                    # unique value
                        pending[op] = (key, val)
                        msg = f"put {op} {key} {val}\n"
                    else:
                        pending[op] = (key, None)
                        msg = f"del {op} {key}\n"
                proc.stdin.write(msg)
                proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass

    threading.Thread(target=reader, daemon=True).start()
    for w in range(2):
        threading.Thread(target=writer, args=(w, random.Random(rng.random())), daemon=True).start()

    time.sleep(rng.uniform(0.05, 1.0))   # RANDOM kill time
    proc.kill()                           # SIGKILL: no cleanup possible
    proc.wait()
    time.sleep(0.05)

    with lock:                            # leftover pending ops = "maybe"
        for key, val in pending.items():
            maybe.setdefault(key, set()).add(val)

    # restart and read what the database holds
    out = subprocess.run(ENGINE + ["dump"], capture_output=True, text=True)
    actual = {}
    for line in out.stdout.splitlines():
        k, v = line.split(" ", 1)
        actual[k] = v

    # compare
    for key in set(acked) | set(maybe) | set(actual):
        found = actual.get(key)                       # None = absent
        allowed = {acked.get(key)} | maybe.get(key, set())
        if found not in allowed:
            print(f"FAIL iteration {n}: key {key}")
            print(f"  expected one of: {allowed}")
            print(f"  found: {found}")
            return False

    # database state is now the new baseline
    acked.clear(); maybe.clear()
    for k, v in actual.items():
        acked[k] = v
    return True

if __name__ == "__main__":
    iterations = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    rng = random.Random(seed)
    if os.path.exists("data.log"):
        os.remove("data.log")
    for i in range(1, iterations + 1):
        if not run_iteration(rng, i):
            print(f"Stopped. Re-run with the same seed ({seed}) to reproduce.")
            sys.exit(1)
        print(f"iteration {i}: OK")
    print(f"PASSED {iterations}/{iterations}")
