from kvstore import KVStore

db = KVStore("test.log")
db.put("name", "Asha")
db.put("city", "Mangaluru")
db.delete("city")
print("name =", db.get("name"))
print("city =", db.get("city"))