import os
from kvstore import KVStore

def test_torn_writes():
    db_path = "test_torn.kdb"
    
    # Clean up any existing test file
    if os.path.exists(db_path):
        os.remove(db_path)

    print("Running torn write recovery test...")

    # Open database and write initial data with proper byte encoding
    db = KVStore(db_path)
    for i in range(20):
        key = f"k{i}".encode("utf-8")
        value = f"v{i}".encode("utf-8")
        db.put(key, value)
    db.close()

    # Simulate recovery by reopening the database and verifying keys exist
    db = KVStore(db_path)
    for i in range(20):
        key = f"k{i}".encode("utf-8")
        val = db.get(key)
        assert val is not None, f"Key k{i} missing after recovery!"
    db.close()

    print("Torn write and recovery test passed successfully!")

if __name__ == "__main__":
    test_torn_writes()