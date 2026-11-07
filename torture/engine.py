import os
import sys


db = {}


def apply(parts):
    if len(parts) == 3 and parts[0] == "PUT":
        db[parts[1]] = parts[2]
    elif len(parts) == 2 and parts[0] == "DELETE":
        db.pop(parts[1], None)


def load():
    if not os.path.exists("data.log"):
        return

    current_batch = None
    batch_start = 0
    pos = 0

    with open("data.log", "rb") as source:
        for raw_line in source:
            line = raw_line.decode(errors="replace").rstrip("\r\n")
            parts = line.split(" ", 2)

            if not parts or parts[0] == "":
                pos += len(raw_line)
                continue
            if parts[0] == "BEGIN":
                current_batch = []
                batch_start = pos
            elif parts[0] == "COMMIT":
                if current_batch is not None:
                    for operation in current_batch:
                        apply(operation)
                current_batch = None
            elif current_batch is not None:
                current_batch.append(parts)
            else:
                apply(parts)
            pos += len(raw_line)

    if current_batch is not None:
        with open("data.log", "r+b") as repaired:
            repaired.truncate(batch_start)
            repaired.flush()
            os.fsync(repaired.fileno())


load()
f = open("data.log", "ab")


def write_line(text):
    f.write((text + "\n").encode())
    f.flush()
    os.fsync(f.fileno())


def put(key, value):
    write_line(f"PUT {key} {value}")
    db[key] = value


def delete(key):
    write_line(f"DELETE {key}")
    db.pop(key, None)


def batch(ops):
    lines = ["BEGIN"]
    for kind, key, value in ops:
        lines.append(f"PUT {key} {value}" if kind == "PUT" else f"DELETE {key}")
    lines.append("COMMIT")

    f.write(("\n".join(lines) + "\n").encode())
    f.flush()
    os.fsync(f.fileno())

    for kind, key, value in ops:
        if kind == "PUT":
            db[key] = value
        else:
            db.pop(key, None)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(2)

    cmd = sys.argv[1]
    if cmd == "dump":
        for key in sorted(db):
            print(key, db[key])
    elif cmd == "serve":
        for line in sys.stdin:
            raw_parts = line.rstrip("\n").split(" ", 3)
            if len(raw_parts) < 2:
                continue

            request = raw_parts[0]
            op_id = raw_parts[1]

            if request == "put" and len(raw_parts) == 4:
                put(raw_parts[2], raw_parts[3])
            elif request == "del" and len(raw_parts) == 3:
                delete(raw_parts[2])
            elif request == "batch":
                parts = line.rstrip("\n").split()
                operations = []
                for token in parts[2:]:
                    operation = token.split(",", 2)
                    if operation[0] == "P" and len(operation) == 3:
                        operations.append(("PUT", operation[1], operation[2]))
                    elif operation[0] == "D" and len(operation) == 2:
                        operations.append(("DELETE", operation[1], None))
                if operations:
                    batch(operations)
                else:
                    continue
            else:
                continue

            print("ACK", op_id, flush=True)