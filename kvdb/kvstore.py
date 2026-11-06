import os
import struct
import threading
import zlib

MAGIC = b"KVDB0001"
FRAME_HDR = struct.Struct("<II")    # payload length, crc32
OP_PUT, OP_DEL = 1, 2
MAX_KEY, MAX_VALUE, MAX_BATCH = 1024, 1024 * 1024, 10_000


class DiskFullError(Exception):
    pass


class KVStore:
    def __init__(self, path, max_bytes=None):
        self.path = path
        self.max_bytes = max_bytes
        self.lock = threading.Lock()   # one global lock, allowed by the rules
        self.index = {}                # key (bytes) -> value (bytes)
        self._open_and_recover()

    # ---------- recovery ----------
    def _open_and_recover(self):
        # new file, or a crash while the header was being created
        if not os.path.exists(self.path) or os.path.getsize(self.path) < len(MAGIC):
            with open(self.path, "wb") as f:
                f.write(MAGIC)
                f.flush()
                os.fsync(f.fileno())

        with open(self.path, "rb") as f:
            data = f.read()
        if data[:len(MAGIC)] != MAGIC:
            raise ValueError("not a KVDB file")

        pos = good_end = len(MAGIC)
        n = len(data)
        while pos + FRAME_HDR.size <= n:
            length, crc = FRAME_HDR.unpack_from(data, pos)
            start = pos + FRAME_HDR.size
            end = start + length
            if end > n:
                break                          # torn frame: not fully on disk
            payload = data[start:end]
            if zlib.crc32(payload) != crc:
                break                          # frame is damaged
            ops = self._decode(payload)
            if ops is None:
                break
            self._apply(ops)
            pos = good_end = end

        if good_end < n:                       # cut off the bad tail
            with open(self.path, "r+b") as f:
                f.truncate(good_end)
                f.flush()
                os.fsync(f.fileno())

        self.size = good_end
        self.f = open(self.path, "ab")

    # ---------- encoding ----------
    @staticmethod
    def _encode(ops):
        parts = [struct.pack("<I", len(ops))]
        for kind, key, val in ops:
            parts.append(struct.pack("<BII", kind, len(key), len(val)))
            parts.append(key)
            parts.append(val)
        payload = b"".join(parts)
        return FRAME_HDR.pack(len(payload), zlib.crc32(payload)) + payload

    @staticmethod
    def _decode(payload):
        try:
            (count,) = struct.unpack_from("<I", payload, 0)
            pos = 4
            ops = []
            for _ in range(count):
                kind, klen, vlen = struct.unpack_from("<BII", payload, pos)
                pos += 9
                key = payload[pos:pos + klen]
                pos += klen
                val = payload[pos:pos + vlen]
                pos += vlen
                ops.append((kind, key, val))
            return ops
        except struct.error:
            return None

    def _apply(self, ops):
        for kind, key, val in ops:             # in order: later ops win
            if kind == OP_PUT:
                self.index[key] = val
            else:
                self.index.pop(key, None)

    # ---------- the one write path ----------
    def _commit(self, ops):
        frame = self._encode(ops)
        with self.lock:
            if self.max_bytes is not None and self.size + len(frame) > self.max_bytes:
                raise DiskFullError("file size limit reached")
            self.f.write(frame)
            self.f.flush()
            os.fsync(self.f.fileno())          # data is on disk from here
            self.size += len(frame)
            self._apply(ops)                   # memory updated only after disk

    # ---------- public API ----------
    @staticmethod
    def _check(key, value=b""):
        if not isinstance(key, bytes) or not 1 <= len(key) <= MAX_KEY:
            raise ValueError("key must be 1 to 1024 bytes")
        if not isinstance(value, bytes) or len(value) > MAX_VALUE:
            raise ValueError("value must be at most 1 MB of bytes")

    def put(self, key, value):
        self._check(key, value)
        self._commit([(OP_PUT, key, value)])

    def delete(self, key):
        self._check(key)
        self._commit([(OP_DEL, key, b"")])

    def batch(self, ops):
        """ops: list of ("put", key, value) or ("del", key). Applied in order."""
        if not 1 <= len(ops) <= MAX_BATCH:
            raise ValueError("batch must have 1 to 10000 operations")
        encoded = []
        for op in ops:
            if op[0] == "put":
                self._check(op[1], op[2])
                encoded.append((OP_PUT, op[1], op[2]))
            elif op[0] == "del":
                self._check(op[1])
                encoded.append((OP_DEL, op[1], b""))
            else:
                raise ValueError("unknown op")
        self._commit(encoded)

    def get(self, key):
        with self.lock:
            return self.index.get(key)

    def close(self):
        with self.lock:
            self.f.close()