# Breaking Points, Failed Attempts, and Improvements

This document records what can still break, what was actually tested, and what I would improve next. It is intentionally honest rather than promotional.

## Crash-point reasoning

The implementation's main durability argument is the ordering in `KVDatabase.put`, `delete`, and `batch`: append complete framed records, flush, call `os.fsync()`, update the in-memory index, then return. Therefore a returned success has crossed the durability point. A process killed before that response may lose the operation or retain a fully written operation; both outcomes are allowed for an unacknowledged operation.

The batch argument is different from the single-record argument. Batch operation frames are staged during recovery and are not applied until a valid commit record matches both the operation count and the CRC32 of all operation frames. A torn operation or missing commit therefore cannot expose only part of the batch. In memory, the global `RLock` prevents readers from seeing the index between individual batch updates.

The compaction argument depends on replacement ordering. The temporary file is synced before `os.replace()` is called. Before replacement, the old path remains authoritative. After replacement, the new path is complete. The exact instruction-level rename and recovery kill points have not been injected, so this is a design argument, not a verified claim.

## Actual failed attempt during development

The first Phase 1 test run failed because the binary header declaration omitted the record-type byte. `struct.pack` reported:

```text
struct.error: pack expected 7 items for packing (got 8)
```

The header was corrected to include magic, version, record type, transaction id, and four length/index fields. The full unit suite then passed.

During Phase 4 documentation, a README update accidentally overwrote the earlier README instead of appending to it. The error was detected during Git review, the complete README was restored, and a separate correction commit was made. This is why the repository contains both the Phase 4 implementation commit and a subsequent README restoration commit.

A manual size-limit check also initially expected a write to fail after compacting an empty database. That expectation was wrong: compaction reduced the file to zero live records, so the write correctly fit under the cap. The check was corrected to retain a live record and then passed.

## Current weaknesses

1. **No exact crash failpoints.** The torture tool kills a separate process at randomized short delays, but it cannot prove exact points such as “after fsync returns and before the response” or “in the middle of `os.replace()`.”
2. **Recovery is a full log scan.** The in-memory index is rebuilt by scanning every valid frame. This is simple and defensible, but one million keys and a long history need a real benchmark before claiming the two-second requirement.
3. **Compaction is not directly kill-tested.** The temp-file/rename design is conservative, but the exact C3/C4/C6 cases remain unverified.
4. **The file-size limit is logical, not a physical disk-full simulation.** Preflight checks prevent the tested over-cap writes. A real `ENOSPC` during a multi-frame append is not simulated.
5. **Concurrency evidence is incomplete.** The implementation uses one global thread lock and the thread test passes, but the exact two-writer same-key and reader-during-batch scenarios were not separately run.
6. **There is no cross-process lock.** The requirement permits threads or processes, and this implementation guarantees thread safety. Two independent database instances must not be pointed at the same file concurrently.
7. **Middle-of-file corruption is not handled.** Recovery stops at the first invalid frame and preserves the valid prefix. Random bit rot in the middle is explicitly outside the challenge's required tests, but this implementation does not search past it.

## Prioritized improvements

### Priority 1: Add deterministic failpoints

Add an optional test-only callback or environment-controlled failpoint around each write, flush, fsync, truncate, and rename step. The production path should remain unchanged unless testing is explicitly enabled. The torture tool could then kill at named points and produce evidence for A1-A6, B1-B3, C1-C4, and C6.

### Priority 2: Benchmark and optimize recovery

Generate a database with 1,000,000 small keys, measure `open()` with a monotonic clock, and record memory and file size. If the scan is too slow, add a durable index checkpoint or compacted snapshot format, but only after preserving the same recovery invariant.

### Priority 3: Improve capacity-failure coverage

Use a controlled write wrapper in tests that raises `ENOSPC` after a chosen byte count. Verify that a partially appended single record or batch is ignored on reopen and that the current process rebuilds its index after the failure.

### Priority 4: Expand concurrency tests

Run two writer threads on different keys, two writer threads on the same key, a long batch beside single puts, and a reader repeatedly checking a batch's affected keys. Compare results against before/after snapshots.

## What I would say to a judge

“I chose a checksummed append-only log because the failure boundary is visible. Success happens only after fsync. Batches have a commit marker and are staged until the marker validates. Recovery ignores an incomplete tail and incomplete batches. I built an independent parent-owned oracle and killed child processes, but I will distinguish the randomized cases I tested from exact failpoints I did not test. The biggest remaining risk is the one-million-key recovery timing and compaction kill-point evidence.”
