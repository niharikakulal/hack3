import os
import struct
import threading
import time
import zlib

MAGIC = b"KVDB0001"
FRAME_HDR = struct.Struct("<II")    # payload length, crc32
OP_PUT, OP_DEL = 1, 2
MAX_KEY, MAX_VALUE, MAX_BATCH = 1024, 1024 * 1024, 10_000
COMPACT_FRAME_OPS = 10_000          # compaction writes frames of at most this many operations
COMPACT_FRAME_BYTES = 4_000_000     # ... and of roughly this many bytes


class DiskFullError(Exception):
    pass


class KVStore:
    def __init__(self, path, max_bytes=None):
        self.path = path
        self.temp_path = path + ".compact.tmp"   # only exists while a compaction is running
        self.max_bytes = max_bytes
        self.lock = threading.Lock()             # one global lock, allowed by the rules
        self.index = {}                          # key (bytes) -> value (bytes)
        self.failed = False                      # set if a failed write could not be undone
        self._open_and_recover()

    # ---------- recovery ----------
    def _open_and_recover(self):
        # A temp file can only be left over from a compaction that never reached its
        # rename, so the real file is still complete. Deleting the temp file is always safe.
        if os.path.exists(self.temp_path):
            try:
                os.remove(self.temp_path)
            except OSError:
                pass

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
            if self.failed:
                raise OSError("a previous write failed and could not be undone; reopen the database")
            if self.max_bytes is not None and self.size + len(frame) > self.max_bytes:
                raise DiskFullError("file size limit reached")
            try:
                self.f.write(frame)
                self.f.flush()
                os.fsync(self.f.fileno())      # data is on disk from here
            except OSError:
                self._discard_failed_write()   # never leave a torn tail behind a running process
                raise
            self.size += len(frame)
            self._apply(ops)                   # memory is updated only after the disk

    def _discard_failed_write(self):
        """A write failed part-way. Cut the file back to the last acknowledged size."""
        try:
            self.f.close()
        except OSError:
            pass
        try:
            with open(self.path, "r+b") as f:
                f.truncate(self.size)
                f.flush()
                os.fsync(f.fileno())
            self.f = open(self.path, "ab")
        except OSError:
            self.failed = True                 # the next open() will drop the torn tail

    # ---------- compaction ----------
    def _compaction_frames(self):
        """Yield frames that together hold every live key exactly once."""
        chunk, chunk_bytes = [], 0
        for key, val in self.index.items():
            chunk.append((OP_PUT, key, val))
            chunk_bytes += 9 + len(key) + len(val)
            if len(chunk) >= COMPACT_FRAME_OPS or chunk_bytes >= COMPACT_FRAME_BYTES:
                yield self._encode(chunk)
                chunk, chunk_bytes = [], 0
        if chunk:
            yield self._encode(chunk)

    @staticmethod
    def _replace(src, dst):
        """os.replace, retried briefly: on Windows an antivirus scan can hold a new file for a moment."""
        for attempt in range(10):
            try:
                os.replace(src, dst)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.05)

    def _sync_directory(self):
        """Make the rename itself durable. Windows cannot fsync a directory, so it is skipped there."""
        if os.name == "nt":
            return
        fd = os.open(os.path.dirname(os.path.abspath(self.path)), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def compact(self):
        """Rewrite the file so it holds only live keys.

        1. write a complete new file next to the old one (the old file is untouched)
        2. fsync it, so it is fully on disk
        3. close our handle to the old file (Windows cannot replace an open file)
        4. os.replace: atomic, so exactly one of the two files is the database
        5. reopen the file that now sits at self.path
        A crash before step 4 leaves the old file complete (plus a temp file that open() deletes).
        A crash after step 4 leaves the new file complete.
        """
        with self.lock:
            if self.failed:
                raise OSError("a previous write failed and could not be undone; reopen the database")
            written = len(MAGIC)
            try:
                with open(self.temp_path, "wb") as t:
                    t.write(MAGIC)
                    for frame in self._compaction_frames():
                        written += len(frame)
                        if self.max_bytes is not None and written > self.max_bytes:
                            raise DiskFullError("compacted file would exceed the size limit")
                        t.write(frame)
                    t.flush()
                    os.fsync(t.fileno())
            except BaseException:
                try:
                    os.remove(self.temp_path)
                except OSError:
                    pass
                raise

            self.f.close()
            try:
                self._replace(self.temp_path, self.path)
            except BaseException:
                try:
                    os.remove(self.temp_path)
                except OSError:
                    pass
                raise
            finally:
                self.f = open(self.path, "ab")   # always reopen whichever file is now at self.path
            self.size = os.path.getsize(self.path)
            self._sync_directory()

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