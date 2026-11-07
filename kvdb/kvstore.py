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
        self.lock = threading.Lock()
        self.index = {}
        self._open_and_recover()

    # ---------- recovery ----------
    def _open_and_recover(self):
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
                break

            payload = data[start:end]
            if zlib.crc32(payload) != crc:
                break

            ops = self._decode(payload)
            if ops is None:
                break

            self._apply(ops)
            pos = good_end = end

        if good_end < n:
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
            if len(payload) < 4:
                return None

            (count,) = struct.unpack_from("<I", payload, 0)
            if count > MAX_BATCH:
                return None

            pos = 4
            ops = []

            for _ in range(count):
                if pos + 9 > len(payload):
                    return None

                kind, klen, vlen = struct.unpack_from("<BII", payload, pos)
                pos += 9

                if kind not in (OP_PUT, OP_DEL):
                    return None
                if not 1 <= klen <= MAX_KEY or vlen > MAX_VALUE:
                    return None
                if kind == OP_DEL and vlen != 0:
                    return None
                if pos + klen + vlen > len(payload):
                    return None

                key = payload[pos:pos + klen]
                pos += klen
                val = payload[pos:pos + vlen]
                pos += vlen
                ops.append((kind, key, val))

            if pos != len(payload):
                return None

            return ops
        except (struct.error, OverflowError):
            return None

    def _apply(self, ops):
        for kind, key, val in ops:
            if kind == OP_PUT:
                self.index[key] = val
            elif kind == OP_DEL:
                self.index.pop(key, None)

    # ---------- the one write path ----------
    def _commit(self, ops):
        frame = self._encode(ops)
        with self.lock:
            if self.max_bytes is not None and self.size + len(frame) > self.max_bytes:
                raise DiskFullError("file size limit reached")

            self.f.write(frame)
            self.f.flush()
            os.fsync(self.f.fileno())
            self.size += len(frame)
            self._apply(ops)

    # ---------- compaction ----------
    def compact(self):
        """Rewrite the log with only live key/value pairs.

        A complete replacement file is fsynced before os.replace() publishes it.
        A crash before replacement leaves the old database intact; a crash after
        replacement leaves the new complete database at the original path.
        """
        with self.lock:
            ops = [
                (OP_PUT, key, value)
                for key, value in sorted(self.index.items())
            ]

            if ops:
                compacted = MAGIC + self._encode(ops)
            else:
                compacted = MAGIC

            if self.max_bytes is not None and len(compacted) > self.max_bytes:
                raise DiskFullError("compacted database exceeds size limit")

            temp_path = self.path + ".compact.tmp"
            old_file = self.f

            try:
                with open(temp_path, "wb") as temp:
                    temp.write(compacted)
                    temp.flush()
                    os.fsync(temp.fileno())

                # Publish the fully written database atomically.
                os.replace(temp_path, self.path)

                # Persist the directory entry where supported.
                directory = os.path.dirname(os.path.abspath(self.path)) or "."
                try:
                    dir_fd = os.open(directory, os.O_RDONLY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except OSError:
                    # Some platforms/filesystems do not support directory fsync.
                    pass

                # Switch this KVStore instance to the replacement file.
                self.f = open(self.path, "ab")
                self.size = len(compacted)
                old_file.close()

            except Exception:
                # If replacement did not happen, keep using the old open file.
                # If replacement did happen, reopen the current path before
                # propagating the exception.
                if os.path.exists(self.path):
                    try:
                        current_size = os.path.getsize(self.path)
                        if current_size != self.size:
                            self.f = open(self.path, "ab")
                            old_file.close()
                            self.size = current_size
                    except OSError:
                        pass
                raise
            finally:
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass

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
