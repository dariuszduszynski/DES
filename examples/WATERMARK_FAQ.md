# FAQ: Watermark Approach dla DES Migration

## 🤔 Najczęściej Zadawane Pytania

---

### Q1: Czy watermark approach jest bezpieczny dla produkcji?

**A:** TAK, pod warunkiem że:
- ✅ Archiwizacja jest **sekwencyjna** (starsze pliki → nowsze)
- ✅ Możesz zaakceptować **replay całego okna** w przypadku błędu
- ✅ Używasz **external audit logs** zamiast per-file statusu
- ✅ DES packer jest **idempotentny** (te same pliki → te same shardy)

**Uwaga:** Watermark NIE nadaje się gdy:
- ❌ Potrzebujesz per-file tracking dla compliance (bez hybrid approach)
- ❌ Archiwizujesz pliki w losowej kolejności
- ❌ Często występują częściowe błędy wymagające granular retry

---

### Q2: Co się stanie jeśli proces padnie w trakcie przetwarzania okna?

**A:** Watermark NIE zostanie zaktualizowany, więc następne uruchomienie **przetworzy to samo okno ponownie** (replay).

```
┌─────────────────────────────────────────────────┐
│ Cycle 1 (FAILED):                               │
│   Window: 2024-11-25 → 2024-11-26              │
│   Processed 800/1000 files                      │
│   ❌ CRASH before watermark update              │
│   Watermark: STILL 2024-11-25                   │
└─────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────┐
│ Cycle 2 (RETRY):                                │
│   Window: 2024-11-25 → 2024-11-26 (SAME!)      │
│   Process ALL 1000 files again                  │
│   ✅ Success, watermark updated to 2024-11-26   │
└─────────────────────────────────────────────────┘
```

**Dlaczego to jest safe?**
- DES routing jest **deterministyczny**: ten sam (uid, created_at) → ten sam shard
- Drugi run utworzy **identyczne shardy** (overwrites lub idempotent S3 PUTs)
- Zero duplikatów dzięki deterministic routing

---

### Q3: Jak śledzić które konkretne pliki zostały zarchiwizowane?

**A:** Watermark nie przechowuje per-file status. Masz kilka opcji:

#### Opcja 1: Query based on watermark
```sql
-- Sprawdź czy plik został zarchiwizowany
SELECT 
    CASE 
        WHEN created_at <= (SELECT archived_until FROM des_archive_config WHERE id = 1)
        THEN 'archived'
        ELSE 'pending'
    END as status
FROM files
WHERE uid = 'file-12345';
```

#### Opcja 2: External audit log
```python
# Log każdy spakowany plik do osobnej tabeli
CREATE TABLE des_audit_log (
    uid TEXT,
    created_at TIMESTAMP,
    archived_at TIMESTAMP,
    shard_key TEXT,
    status TEXT
);

# Po spakowaniu:
log_to_audit(uid, created_at, shard_key, status='archived')
```

#### Opcja 3: Hybrid approach (najlepsze z obu światów)
```python
# Migration używa watermark (fast!)
# Async job aktualizuje archived column (slow but compliance-friendly)

# Nightly job:
UPDATE files 
SET archived = true
WHERE created_at <= (
    SELECT archived_until FROM des_archive_config WHERE id = 1
)
AND archived = false;
```

---

### Q4: Czy mogę używać watermark z wieloma workerami (horizontal scaling)?

**A:** TAK! To jest jedna z największych zalet watermark approach.

#### Shard-based partitioning:
```yaml
# Worker 1
worker_config:
  shard_id: 0
  shards_total: 10

# Worker 2
worker_config:
  shard_id: 1
  shards_total: 10

# ... Worker 10
worker_config:
  shard_id: 9
  shards_total: 10
```

**Jak to działa:**
- Każdy worker przetwarza **to samo okno czasowe**
- Każdy worker filtruje pliki **po shard_id** (hash(uid) % shards_total)
- Zero koordynacji między workerami
- Każdy worker aktualizuje ten sam watermark (idempotent)

```python
# W DatabaseSourceProvider:
async for record in iter_records_for_window(window):
    # Python-level filter by shard
    if hash(record.uid) % shards_total != shard_id:
        continue
    
    yield record  # Tylko pliki dla tego workera
```

**Wynik:**
- 10 workerów = 10x throughput
- Zero lock contention
- Linearna skalowalność

---

### Q5: Co jeśli potrzebuję "cofnąć" watermark (replay starszych plików)?

**A:** Możesz manualnie cofnąć watermark w des_archive_config:

```sql
-- Cofnij o 1 dzień (replay yesterday's files)
UPDATE des_archive_config 
SET archived_until = archived_until - INTERVAL '1 day'
WHERE id = 1;

-- LUB ustaw na konkretną datę
UPDATE des_archive_config 
SET archived_until = '2024-11-20 00:00:00+00'::timestamp
WHERE id = 1;
```

**Użyj CLI tool:**
```bash
# Cofnij o 1 dzień
python3 des_watermark_migrate.py adjust \
  --config config.yaml \
  --days-offset -1

# Ustaw na konkretną datę
python3 des_watermark_migrate.py adjust \
  --config config.yaml \
  --set-date 2024-11-20
```

**Uwaga:** Replay utworzy identyczne shardy (dzięki deterministic routing), więc jest safe.

---

### Q6: Jak monitorować postęp migracji?

**A:** Użyj Prometheus metrics + Grafana dashboard:

#### Kluczowe metryki:
```promql
# Lag między watermark a target
des_watermark_lag_seconds

# Pliki per window
rate(des_migration_files_total[5m])

# Czas przetwarzania window
des_migration_duration_seconds

# Rozmiar window
des_watermark_window_size_seconds
```

#### SQL queries:
```sql
-- Aktualny lag
SELECT 
    archived_until,
    NOW() - INTERVAL '7 days' AS target_cutoff,
    (NOW() - INTERVAL '7 days') - archived_until AS lag
FROM des_archive_config
WHERE id = 1;

-- Pending files w current window
SELECT COUNT(*) as pending_files
FROM files
WHERE created_at > (SELECT archived_until FROM des_archive_config WHERE id = 1)
  AND created_at <= NOW() - INTERVAL '7 days';
```

#### CLI tool:
```bash
# Pokaż statystyki
python3 des_watermark_migrate.py stats --config config.yaml

# Output:
# Current watermark:    2024-11-25
# Target cutoff:        2024-11-26
# Lag behind target:    1 days
# Files pending:        45,678
# Total size:           89.3 GB
```

---

### Q7: Co się stanie z kolumną `archived` po migracji?

**A:** Masz 3 opcje:

#### Opcja 1: Usuń kolumnę (najbardziej radykalna)
```sql
-- UWAGA: Destructive! Zrób backup!
ALTER TABLE files DROP COLUMN archived;
DROP INDEX idx_files_created_archived;

-- Korzyść: Mniejsza tabela, prostszy schema
-- Wada: Nie można wrócić do per-record bez migracji
```

#### Opcja 2: Zostaw ale ignoruj (bezpieczna)
```sql
-- Kolumna archived pozostaje ale nie jest używana
-- Query używa tylko created_at vs watermark
-- Kolumna może zawierać stare wartości (nie szkodzi)

-- Korzyść: Łatwy rollback do per-record
-- Wada: Nieużywana kolumna zajmuje miejsce
```

#### Opcja 3: Sync async (hybrid approach)
```sql
-- Migration używa watermark (fast)
-- Nightly job aktualizuje archived (compliance)

-- Cron: 2am daily
UPDATE files 
SET archived = true
WHERE created_at <= (SELECT archived_until FROM des_archive_config WHERE id = 1)
  AND archived = false
LIMIT 1000000;  -- Rate limit

-- Korzyść: Compliance + performance
-- Wada: Kolumna archived może być "za" watermarkiem (max 24h)
```

**Rekomendacja:** Opcja 3 (hybrid) dla większości przypadków.

---

### Q8: Czy watermark approach jest zgodny z compliance (SEC 17a-4, HIPAA)?

**A:** To zależy od wymagań:

#### ✅ Watermark może być compliance-ready gdy:
- Prowadzisz **external audit logs** dla każdego pliku
- Audit log zawiera: uid, timestamp, shard_key, operation
- Możesz udowodnić że plik został zarchiwizowany (przez DES retriever)
- Używasz **hybrid approach** z async update `archived` column

#### ❌ Watermark NIE spełnia compliance gdy:
- Wymaga się **atomowego** per-file statusu w source DB
- Audit trail musi być **wewnątrz transakcji** z główną tabelą
- Wymagane jest **instant** per-file tracking (bez lag)

**Hybrid approach dla compliance:**
```python
# 1. Migration używa watermark (fast, zero overhead)
# 2. Audit log zapisuje każdy spakowany plik
# 3. Nightly job aktualizuje archived column (compliance)

# Rezultat:
# - Fast migration (watermark)
# - Compliance (archived column + audit log)
# - Max lag: 24h (akceptowalne dla większości regulacji)
```

---

### Q9: Jak testować watermark migration przed production deploy?

**A:** Użyj 3-stage approach:

#### Stage 1: Dry-run w dev
```bash
# Test z demo database
python3 demo_comparison.py --records 10000

# Output pokazuje różnicę w performance
```

#### Stage 2: Shadow mode w staging
```python
# Uruchom obie ścieżki równolegle, porównaj wyniki
class ShadowMigrationOrchestrator:
    def run_cycle(self):
        # A. Per-record (current)
        result_old = per_record_orchestrator.run_cycle()
        
        # B. Watermark (new)
        result_new = watermark_orchestrator.run_cycle()
        
        # C. Verify: same files processed
        assert result_old.files_processed == result_new.files_processed
        
        # D. Verify: same shards created (deterministic!)
        assert compare_shards(result_old.shards, result_new.shards)
```

#### Stage 3: Canary deployment w prod
```yaml
# Day 1: 10% traffic to watermark
worker_per_record: 9 replicas
worker_watermark: 1 replica

# Day 3: 50% traffic
worker_per_record: 5 replicas
worker_watermark: 5 replicas

# Day 7: 100% watermark
worker_per_record: 0 replicas
worker_watermark: 10 replicas
```

---

### Q10: Co jeśli niektóre pliki w oknie się nie powiodą (validation errors)?

**A:** To jest największa różnica między per-record a watermark:

#### Per-Record approach:
```
Files in batch: [A, B, C(fail), D, E]
                 ↓
Pack successful: [A, B, D, E]
                 ↓
UPDATE archived=true: [A, B, D, E]  ← Tylko successful!
                 ↓
Next batch will see: [C, F, G, ...]  ← C is retried
```

#### Watermark approach:
```
Files in window: [A, B, C(fail), D, E]
                 ↓
Pack attempt: [A, B, C(FAIL), D, E]
                 ↓
Options:
1. Stop on error: watermark NOT advanced, entire window replayed
2. Continue on error: watermark advanced, C is SKIPPED!
```

**Rozwiązanie: Error isolation + retry logic**
```python
# Opcja 1: Skip failures, log to error table
CREATE TABLE des_migration_errors (
    uid TEXT,
    created_at TIMESTAMP,
    error_message TEXT,
    retry_count INTEGER,
    failed_at TIMESTAMP
);

# Po migracji: manual retry errors
SELECT * FROM des_migration_errors
WHERE retry_count < 3;

# Opcja 2: Separate error window
# Watermark advances past successful files
# Error files get separate processing (out-of-band)
```

**Best practice:**
- Use error isolation (don't block entire window)
- Log failures to separate table
- Manual review/retry failed files
- Alert on high failure rate

---

### Q11: Czy mogę używać watermark z S3 source files?

**A:** TAK! Watermark approach działa identycznie z S3:

```python
from des_core.file_reader import S3FileReader

# Configure S3 reader
file_reader = S3FileReader(
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY"),
    aws_secret_access_key=os.getenv("AWS_SECRET_KEY"),
    region_name="us-east-1"
)

# Create orchestrator with S3 reader
orchestrator = WatermarkMigrationOrchestrator(
    db_connection=conn,
    config_connection=config_conn,
    source_config=source_config,
    packer_config=packer_config,
    file_reader=file_reader,  # ← S3FileReader!
)

# Database schema (same as local):
CREATE TABLE files (
    uid TEXT PRIMARY KEY,
    created_at TIMESTAMP,
    file_location TEXT,  -- s3://bucket/key/file.dat
    size_bytes BIGINT
);
```

**Performance tips:**
- Use S3 batch operations gdy możliwe
- Prefetch file metadata (HEAD requests)
- Use multipart download dla dużych plików

---

### Q12: Jak długo powinien być `lag_days`?

**A:** To zależy od przypadku użycia:

| Use Case | Recommended lag_days | Reasoning |
|----------|---------------------|-----------|
| **Real-time archive** | 1-2 days | Minimal lag, fast archiving |
| **Standard archive** | 7 days | Buffer for errors, safe default |
| **Compliance archive** | 30 days | Extra safety, audit window |
| **Cold storage** | 90+ days | Only very old files |

**Trade-offs:**
- **Mały lag (1-2 dni):**
  - ✅ Szybsze archiwizowanie
  - ✅ Mniej pending files
  - ❌ Mniej czasu na wykrycie błędów
  
- **Duży lag (30+ dni):**
  - ✅ Więcej czasu na weryfikację
  - ✅ Buffor dla błędów/zmian
  - ❌ Więcej pending files w queue

**Best practice:** Start with 7 days, adjust based on monitoring.

---

### Q13: Co z transaction safety przy watermark approach?

**A:** Watermark używa **eventual consistency** zamiast strict transactions:

#### Per-Record (strict transactions):
```python
with db.begin():
    # Pack files
    pack_files(files)
    # Mark as archived (same transaction)
    UPDATE files SET archived=true WHERE uid IN (...)
# Either both succeed or both rollback atomically
```

#### Watermark (eventual consistency):
```python
# Phase 1: Pack files (idempotent, no transaction)
try:
    pack_files(files)
except:
    # Failure: watermark NOT advanced
    # Next run will replay window (safe!)
    return

# Phase 2: Advance watermark (separate, atomic)
UPDATE des_archive_config SET archived_until = ...
```

**Safety guarantees:**
- ✅ **Idempotency:** Replay window = same shards
- ✅ **No data loss:** Failed pack = watermark not advanced
- ✅ **No duplicates:** Deterministic routing prevents doubles
- ❌ **Not atomic:** Pack can succeed but watermark update fail (rare)

**Handling edge case (watermark update fails):**
```python
try:
    # Pack files
    shards = pack_files(...)
    
    # Try to advance watermark
    try:
        advance_watermark(target_cutoff)
    except:
        # Watermark update failed!
        # Log error, alert operator
        logger.error("Watermark update failed but files were packed!")
        # Next run will replay window
        # DES routing ensures same shards = safe
```

---

### Q14: Jaki jest sizing dla des_archive_config table?

**A:** BARDZO mała - zawsze 1 wiersz!

```sql
-- Table size
SELECT pg_size_pretty(pg_total_relation_size('des_archive_config'));
-- Output: 8 kB (includes indexes)

-- Row count
SELECT COUNT(*) FROM des_archive_config;
-- Output: 1 (always!)

-- UPDATE performance
EXPLAIN ANALYZE 
UPDATE des_archive_config SET archived_until = NOW() WHERE id = 1;
-- Execution time: 0.123 ms (sub-millisecond!)
```

**Dlaczego to jest tak szybkie?**
- 1 wiersz = instant lookup (no scan)
- Brak indeksu (nie potrzebny dla 1 row)
- UPDATE in-place (no reordering)
- Zero lock contention (single row)

**Porównanie:**
```
Per-Record:
  UPDATE 1,000,000 rows = ~5-10 minutes (depends on hardware)
  Transaction log: 50+ GB
  Lock time: seconds

Watermark:
  UPDATE 1 row = ~0.1 milliseconds
  Transaction log: 100 bytes
  Lock time: microseconds
```

---

### Q15: Czy mogę migrować tylko częściowo (niektóre pliki per-record, niektóre watermark)?

**A:** NIE, nie jest to zalecane. Ale możesz mieć **2 osobne tabele**:

#### Opcja 1: Split tables (zalecane)
```sql
-- Tabela 1: Hot data (per-record)
CREATE TABLE files_hot (
    uid TEXT PRIMARY KEY,
    created_at TIMESTAMP,
    file_location TEXT,
    archived BOOLEAN DEFAULT FALSE  -- per-record tracking
);

-- Tabela 2: Cold data (watermark)
CREATE TABLE files_cold (
    uid TEXT PRIMARY KEY,
    created_at TIMESTAMP,
    file_location TEXT
    -- NO archived column!
);

-- Separate des_archive_config for cold table
CREATE TABLE des_archive_config_cold (
    id INTEGER PRIMARY KEY,
    archived_until TIMESTAMP,
    lag_days INTEGER
);
```

**Migration flow:**
```python
# 1. Hot files use per-record (small volume, need tracking)
hot_orchestrator = MigrationOrchestrator(table="files_hot")

# 2. Cold files use watermark (huge volume, need speed)
cold_orchestrator = WatermarkMigrationOrchestrator(table="files_cold")

# Run both:
hot_result = hot_orchestrator.run_cycle()
cold_result = await cold_orchestrator.run_cycle()
```

#### Opcja 2: Time-based split (nie zalecane)
```python
# Files < 30 days: per-record
# Files > 30 days: watermark

# Problem: Mixing approaches in same table is confusing!
# Better to use Option 1 (split tables)
```

---

## 🎯 Podsumowanie FAQ

**Najważniejsze punkty:**

1. ✅ **Watermark jest production-ready** dla sequential archiving
2. ✅ **Replay window jest safe** dzięki idempotency
3. ✅ **Horizontal scaling działa** out-of-the-box
4. ✅ **Compliance możliwy** przez hybrid approach
5. ⚠️ **Trade-off:** Window-based tracking vs per-file precision

**Kiedy używać watermark:**
- Masz >100M plików
- UPDATE overhead jest problemem  
- Możesz zaakceptować window-based tracking
- Priorytet: maksymalna wydajność

**Kiedy NIE używać watermark:**
- Potrzebujesz atomic per-file tracking
- Archiwizacja nie jest sekwencyjna
- <10M plików (overhead nie jest problemem)
- Strict compliance wymaga per-file status

**Hybrid approach = Best of both worlds!**
- Fast migration (watermark)
- Compliance tracking (archived column updated async)
- External audit logs (complete trail)
