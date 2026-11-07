# KVDB: a crash-safe key-value database

An embedded key-value database written from scratch in Python for the "Kill It and Prove It" hackathon.
Everything is stored in one data file. Once `put`, `delete` or `batch` returns success, the data survives the process being killed at any moment.

## Requirements and build

- Python 3.8 or newer (tested with Python 3.12). Standard library only, no packages to install.
- There is no build step. Check that Python works with:

```
python --version
```

All commands below are run from the repository root. On some machines the command is `python3` instead of `python`.

### Short wrappers

Two wrapper scripts in the repository root shorten the commands:

| Platform | Database | Torture tool |
|---|---|---|
| Windows | `kvdb.cmd <command> ...` | `torture.cmd --iterations 500 --seed 12345` |
| Linux / macOS | `./kvdb.sh <command> ...` | `./torture.sh --iterations 500 --seed 12345` |

They only call `python kvdb/kvdb.py` and `python kvdb/torture_v2.py` with the same arguments. Everything below is written with the full `python ...` form so it works without the wrappers.

## Commands

```
python kvdb/kvdb.py open  <path> [--max-bytes N]    open or create, run recovery, print key count and recovery time
python kvdb/kvdb.py put   <path> <key> <value>
python kvdb/kvdb.py get   <path> <key>              prints the value, or NOTFOUND
python kvdb/kvdb.py del   <path> <key>              deleting a missing key is not an error
python kvdb/kvdb.py batch <path> <file>             apply a file of operations atomically
python kvdb/kvdb.py dump  <path>                    print every key and value, sorted by key (key TAB value)
python kvdb/kvdb.py check <path>                    read-only integrity check, prints OK or a reason
python kvdb/kvdb.py serve <path> [--max-bytes N]    long-running mode, reads operations from stdin
```

Example:

```
python kvdb/kvdb.py put demo.kvdb hello world
python kvdb/kvdb.py get demo.kvdb hello
python kvdb/kvdb.py check demo.kvdb
python kvdb/kvdb.py dump demo.kvdb
```

`--max-bytes N` sets a maximum file size. A write that would make the file larger fails with a clear error (`ERROR: disk full (size limit reached)`) and changes nothing on disk. The database stays readable and recoverable.

### Batch file format

One operation per line:

```
put <key> <value>
del <key>
```

The value is the rest of the line, so it may contain spaces. Keys cannot contain spaces. Empty lines are skipped.

**Ordering inside a batch:** operations are applied in file order and later operations win. A batch that does `put X` and then `del X` leaves X absent. A batch that does `del X` and then `put X` leaves X present. This is the same after a crash, because recovery applies a batch's operations in the same order.

### serve mode

`serve` prints `READY` once recovery has finished, then reads one command per line from stdin. Keys and values are sent as hex text so that any bytes can be carried:

```
PUT   <id> <hexkey> <hexvalue>
DEL   <id> <hexkey>
BATCH <id> put:<hexkey>:<hexvalue> del:<hexkey> ...
```

After each operation it prints one line:

- `ACK <id>` only after the operation has been written and `fsync`ed. Every ACK is a durability promise.
- `ERR <id> diskfull` if the size limit was reached (nothing was written).
- `ERR <id> invalid` or `ERR <id> unknown-command` for bad input.

Example: `printf 'PUT 1 6b31 7631\n' | python kvdb/kvdb.py serve demo.kvdb` stores key `k1` with value `v1`.

## Limits

- Keys: 1 to 1,024 bytes. Values: 0 to 1 MB. Batch: 1 to 10,000 operations. The library takes and returns `bytes`; the command line uses UTF-8 text.
- One global lock protects writes and reads inside a process. This is what makes a batch appear to readers either completely or not at all.

## On-disk format

One file. All numbers are little-endian unsigned integers.

```
FILE
+----------------+---------+---------+---------+-----
| "KVDB0001"     | FRAME 1 | FRAME 2 | FRAME 3 | ...
| 8 bytes        |         |         |         |
+----------------+---------+---------+---------+-----

FRAME  (one put, one delete, or one whole batch)
+-----------+-----------+-----------------------------+
| length    | crc32     | payload                     |
| 4 bytes   | 4 bytes   | `length` bytes              |
+-----------+-----------+-----------------------------+
  crc32 is the CRC-32 of the payload bytes only.

PAYLOAD
+-----------+--------+--------+--------+-----+-----+
| op count  | OP 1   | OP 2   | ...                |
| 4 bytes   |        |        |                    |
+-----------+--------+--------+--------------------+

OP
+--------+-----------+-----------+---------+-----------+
| type   | key len   | value len | key     | value     |
| 1 byte | 4 bytes   | 4 bytes   | N bytes | M bytes   |
+--------+-----------+-----------+---------+-----------+
  type 1 = put, type 2 = delete (a delete has value len 0)
```

- A single `put` or `delete` is a frame with an op count of 1. A batch is a frame with an op count of 1 to 10,000.
- The CRC acts as the commit marker. A frame counts only if all of its bytes are on disk and the CRC matches. A batch is one frame, so it is either fully present or fully absent.
- A payload too short to hold an op count (under 4 bytes) cannot decode and is treated as invalid. This matters because a frame of eight zero bytes has a "valid" CRC: the CRC-32 of an empty payload is 0.

### Worked example: hex dump of one put

After `python kvdb/kvdb.py put demo.kvdb k1 v1` on a new file, the file is 33 bytes:

```
00000000  4b 56 44 42 30 30 30 31 11 00 00 00 76 5b f4 dc
00000010  01 00 00 00 01 02 00 00 00 02 00 00 00 6b 31 76
00000020  31
```

Reading it byte by byte:

```
4b 56 44 42 30 30 30 31   header "KVDB0001"             (8 bytes)
11 00 00 00               length = 17                    (4 bytes)
76 5b f4 dc               crc32 of the payload = 0xdcf45b76
01 00 00 00               op count = 1                   (payload starts here)
01                        type = put
02 00 00 00               key length = 2
02 00 00 00               value length = 2
6b 31                     key "k1"
76 31                     value "v1"
```

To print the hex of any database file:

```
python -c "print(open('demo.kvdb','rb').read().hex(' '))"
```

### Write path

Done while holding the lock:

1. Check the size limit. If the new frame would exceed it, fail and write nothing.
2. Build the whole frame in memory.
3. Write it to the end of the file in one `write` call, then `flush`.
4. `os.fsync` the file.
5. Only then update the in-memory index.
6. Return success (in `serve`, print `ACK`).

The caller is told "success" only after step 4, so a promised write is on disk.

### Recovery algorithm (what `open` does)

1. If the file does not exist, or is shorter than the 8-byte header, write the header and `fsync` it.
2. If the header is not `KVDB0001`, stop with an error: this is not a KVDB file.
3. Read frames from the start of the file. For each frame: the length field must fit inside the file, the CRC must match, and the payload must decode. If so, apply its operations to the in-memory index, in order.
4. Stop at the first frame that fails any of those checks. That frame is the torn tail of a write that was never acknowledged.
5. If there are bytes after the last good frame, truncate the file to the end of the last good frame and `fsync`.
6. Open the file for appending. New writes continue from there.

Recovery changes nothing except that one truncation, so running it again after a crash during recovery gives the same result.

## Torture tool

`torture_v2.py` starts `serve` as a child process, sends random puts, deletes and batches from two concurrent writers, and kills the process at a random millisecond (`kill -9` on Linux and macOS, `TerminateProcess` on Windows). It keeps its own record of every acknowledged operation (`oracle.log`), then runs `open`, `check` and `dump` and compares the dump with its record. After each kill:

- every acknowledged operation must be reflected in the dump;
- operations that were sent but not acknowledged (there can be one per writer) may be present or absent;
- a batch must be all present or all absent;
- no value may appear that was never sent.

```
python kvdb/torture_v2.py --iterations 500 --seed 12345
python kvdb/torture_v2.py --iterations 100 --seed 999 --mode large-values
python kvdb/torture_v2.py --iterations 100 --seed 7 --mode large-batches
python kvdb/torture_v2.py --iterations 100 --seed 42 --mode recovery-crash
```

The same seed reproduces the same operations and kill delays. The exact instant of each kill still depends on operating system timing.

## Tests

```
python kvdb/test_basic.py
```

Writes a put and a batch, then cuts the file at every possible byte and checks that recovery always lands on a valid state (nothing, the put only, or the whole batch).

## Crash and power-loss notes

- The tested failure is a process kill with no warning. The operating system keeps bytes already passed to `write`, so a kill after step 3 of the write path does not lose them.
- Every acknowledged write is also `fsync`ed before the ACK, so the design is intended to survive an operating-system crash or power loss as well. Only process kills are tested.

## Repository contents

```
README.md          this file: build, commands, on-disk format, recovery
ARCHITECTURE.md    diagram, scenario-to-mechanism proof table, the three guarantees
BREAKING.md        crash points, ordering argument, weaknesses, failed attempts
PROMPTS.md         every AI prompt used during the event
kvdb/              the engine (kvdb.py), torture tool (torture_v2.py), basic test
diagrams/          architecture diagram
tests/             unit tests
kvdb.cmd, kvdb.sh  short wrappers for the database command line
torture.cmd, torture.sh   short wrappers for the torture tool
```

## Status

Implemented: put, get, delete, atomic batches, open with recovery, close, `check`, `dump`, `serve`, maximum file size, torture tool modes `default`, `large-values`, `large-batches` and `recovery-crash`.

Not implemented yet: compaction (the file only grows), torture modes for compaction and disk-full, and a measured recovery time at 1,000,000 keys. See `BREAKING.md` for known weaknesses.