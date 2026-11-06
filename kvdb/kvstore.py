import os
import json
import zlib


class KVStore:
    def __init__(self, path="data.log"):
        self.path = path
        self.data = {}          # the in-memory dictionary
        self._recover()         # rebuild from the log file
        self.log = open(self.path, "ab")   # open for appending

    def _recover(self):
        """Replay the log file to rebuild self.data."""
        if not os.path.exists(self.path):
            return
        good_end = 0   # position right after the last valid record
        with open(self.path, "rb") as f:
            while True:
                line = f.readline()
                if not line:
                    break
                if not line.endswith(b"\n"):
                    break   # half-written record: stop here
                try:
                    crc_text, payload = line[:-1].split(b" ", 1)
                    if int(crc_text) != zlib.crc32(payload):
                        break   # corrupted record: stop here
                    rec = json.loads(payload)
                except ValueError:
                    break
                if rec["op"] == "put":
                    self.data[rec["key"]] = rec["value"]
                elif rec["op"] == "delete":
                    self.data.pop(rec["key"], None)
                good_end = f.tell()
        # cut off any garbage after the last good record
        with open(self.path, "r+b") as f:
            f.truncate(good_end)

    def _append(self, rec):
        """Write one record to disk and force it to be saved."""
        payload = json.dumps(rec).encode()
        line = str(zlib.crc32(payload)).encode() + b" " + payload + b"\n"
        self.log.write(line)
        self.log.flush()
        os.fsync(self.log.fileno())

    def put(self, key, value):
        self._append({"op": "put", "key": key, "value": value})  # disk first
        self.data[key] = value                                    # then memory

    def get(self, key):
        return self.data.get(key)

    def delete(self, key):
        self._append({"op": "delete", "key": key})
        self.data.pop(key, None)