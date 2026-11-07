import sys
from kvstore import KVStore

if len(sys.argv) < 3:
    print("Usage: python writer.py <db_path> <start_index>")
    sys.exit(1)

db_path = sys.argv[1]
start = int(sys.argv[2])

# Open database instance
db = KVStore(db_path)

# Write test keys ensuring valid byte lengths (1 to 1024 bytes)
for i in range(start, start + 10):
    key = f"key_{i}".encode("utf-8")
    value = f"val_{i}".encode("utf-8")
    db.put(key, value)

db.close()
print(f"Successfully wrote 10 keys starting from index {start}!")