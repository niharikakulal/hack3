# Design Document: KVStore Engine & Crash-Resilient Storage

## 1. Overview
`KVStore` is a lightweight, log-structured key-value database engine built in Python, designed to guarantee strict data integrity, durability, and atomic crash recovery under abrupt system failures or torn writes.

---

## 2. Architecture & Core Components
The project is organized into modular components separating core engine logic, workload generation, and stress-testing:

* **`kvstore.py` (The Storage Engine):** Implements core database operations (`put`, `get`, `close`) and handles file-level operations. Enforces strict validation rules (keys and values must be UTF-8 encoded bytes/strings between 1 and 1024 bytes).
* **`writer.py` (Data Injection):** Handles systematic batch writing of records to validate data ingestion and workload handling.
* **`torture.py` (Fuzzing & Stress Testing):** Automates multi-iteration crash simulations (e.g., 250+ stress runs) to verify system stability under erratic execution patterns.
* **`test_torn.py` & `test_basic.py` (Verification Suites):** Validates atomicity, state recovery after abrupt shutdowns, and file truncation across various cut positions.

---

## 3. Data Format & Validation Rules
* **Key-Value Constraints:** To prevent memory overflow and malformed records, every key and value is validated via `_check()` to ensure a strict length constraint of **1 to 1024 bytes**.
* **Storage Model:** Utilizes an append-friendly structured layout on disk (`.kdb` files), ensuring sequential write performance and predictable recovery paths.

---

## 4. Durability & Crash Recovery
* **Torn-Write Resilience:** The engine handles partial or interrupted writes (torn writes) by validating record boundaries during startup recovery. 
* **Cut-Position Testing:** Tested rigorously across dozens of byte-level file truncation points (verified via `test_basic.py` with zero corruption failures), ensuring that uncommitted or cut-off trailing data does not compromise valid prior state.

---

## 5. Verification & Test Results
The engine has passed a rigorous automated validation pipeline:
* **Torture Stress Tests:** Successfully completed extended iteration runs (e.g., 250+ automated crash simulations).
* **Recovery Validation:** Confirmed atomic recovery of records via `test_torn.py`.
* **Cut Position Verification:** Passed comprehensive boundary checks (e.g., 76 cut positions tested with 0 bad states).