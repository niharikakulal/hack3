import os

db = {}   # the in-memory dictionary

def load():
    """Read the whole file and rebuild the dictionary."""
    if not os.path.exists("data.log"):
        return
    with open("data.log", "rb") as f:
        for line in f:
            parts = line.decode().rstrip("\n").split(" ", 2)
            if parts[0] == "PUT" and len(parts) == 3:
                db[parts[1]] = parts[2]
            elif parts[0] == "DELETE" and len(parts) == 2:
                db.pop(parts[1], None)

load()                      # runs every time the program starts
f = open("data.log", "ab")

def write_line(text):
    f.write((text + "\n").encode())
    f.flush()
    os.fsync(f.fileno())    # force to disk before we say "done"

def put(key, value):
    write_line(f"PUT {key} {value}")
    db[key] = value

def delete(key):
    write_line(f"DELETE {key}")
    db.pop(key, None)

def get(key):
    return db.get(key, "NOTFOUND")

# --- try it ---
print("name is:", get("name"))
put("name", "Carol")
print("name is now:", get("name"))
import time; 
time.sleep(30)