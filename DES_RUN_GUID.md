# 📦 DES - Kompleksowy Poradnik Migracji i Pakowania Plików

**Data Easy Store (DES) v0.3.0**  
**Dokumentacja wersji:** 1.0  
**Ostatnia aktualizacja:** Listopad 2024

---

## 📋 Spis treści

1. [Wprowadzenie](#wprowadzenie)
2. [Wymagania wstępne](#wymagania-wstępne)
3. [Instalacja](#instalacja)
4. [Przygotowanie bazy danych](#przygotowanie-bazy-danych)
5. [Konfiguracja migracji](#konfiguracja-migracji)
6. [Pierwsze uruchomienie (dry-run)](#pierwsze-uruchomienie-dry-run)
7. [Migracja jednorazowa](#migracja-jednorazowa)
8. [Migracja ciągła (continuous)](#migracja-ciągła-continuous)
9. [Monitoring i metryki](#monitoring-i-metryki)
10. [Deployment w Kubernetes](#deployment-w-kubernetes)
11. [Troubleshooting](#troubleshooting)
12. [Best Practices](#best-practices)
13. [FAQ](#faq)

---

## 🎯 Wprowadzenie

### Co to jest DES?

**DES (Data Easy Store)** to system do efektywnego pakowania miliardów małych plików w większe kontenery (shardy) zoptymalizowane pod S3. Główne zalety:

- **Bezstanowa architektura** - brak wewnętrznej bazy danych, deterministyczny routing
- **Wysoka skalowalność** - obsługa miliardów plików bez wąskich gardeł
- **Kompresja** - automatyczna kompresja zstd/lz4 z inteligentnymi heurystykami
- **Szybki odczyt** - S3 Range-GET dla indeksu i pojedynczych plików
- **Integracja z bazami danych** - bezpośrednia migracja z PostgreSQL/MySQL/SQLite

### Architektura migracji

```
┌─────────────────┐
│  Twoja Baza     │  (PostgreSQL/MySQL/SQLite)
│  Danych         │  Tabela z metadanymi plików
└────────┬────────┘
         │
         │ 1. Odczyt niezarchiwizowanych plików
         │    (created_at < cutoff, archived = false)
         ▼
┌─────────────────┐
│  Migration      │
│  Orchestrator   │  2. Walidacja (istnienie, rozmiar)
└────────┬────────┘     3. Grupowanie wg klucza sharda
         │              4. Kompresja i pakowanie
         ▼
┌─────────────────┐
│  DES Shardy     │  YYYYMMDD/HH_XXXX.des
│  (lokalne/S3)   │  [HEADER][DATA][INDEX][FOOTER]
└────────┬────────┘
         │
         │ 5. Oznaczenie plików jako archived=true
         ▼
┌─────────────────┐
│  Twoja Baza     │
│  (updated)      │  archived = true
└─────────────────┘
```

### Kluczowe cechy migracji

✅ **Keyset pagination** - wydajna paginacja dla tabel z miliardami wierszy  
✅ **Izolacja błędów** - jeden uszkodzony plik nie zatrzymuje całego batcha  
✅ **Walidacja** - sprawdzanie istnienia i rozmiaru przed pakowaniem  
✅ **Connection pooling** - optymalizacja połączeń do bazy  
✅ **Retry logic** - automatyczne ponowienia przy przejściowych błędach  
✅ **Metryki Prometheus** - pełna obserwowalnść procesu migracji

---

## ⚙️ Wymagania wstępne

### System operacyjny
- Linux (Ubuntu 24.04, Rocky Linux 9, Amazon Linux 2023)
- macOS 12+
- Windows 10/11 z WSL2

### Python
- **Python 3.12** (wymagane)
- pip 24.0+

### Baza danych (źródło migracji)
- **PostgreSQL** 12+ (zalecane dla produkcji)
- **MySQL** 8.0+
- **SQLite** 3.35+ (development/testing)

### Infrastruktura
- **Miejsce na dysku**: 
  - 2x rozmiar migrowanych danych (pliki źródłowe + shardy DES)
  - Przykład: 1TB plików → minimum 2TB wolnego miejsca
- **RAM**: 
  - Minimum 2GB dla małych migracji (<100k plików)
  - Zalecane 8GB+ dla produkcji
- **Procesor**: 
  - Minimum 2 CPU cores
  - Zalecane 4+ cores dla kompresji

### Dostęp do plików
- Pliki mogą być dostępne lokalnie **lub** jako S3 URI (`s3://bucket/key`)
- Obsługiwane: lokalne dyski, NFS, CIFS/SMB mounts, S3/MinIO (przez `S3FileReader`)

---

## 📥 Instalacja

### Metoda 1: Instalacja z source (zalecana dla development)

```bash
# Klonowanie repozytorium
git clone https://github.com/your-org/des-core.git
cd des-core

# Utworzenie virtual environment
python3.12 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Upgrade pip
python -m pip install --upgrade pip

# Instalacja z wszystkimi zależnościami
pip install -e ".[compression,s3,dev]"

# Weryfikacja instalacji
des-migrate --help
des-stats --help
pytest  # opcjonalnie - uruchom testy
```

### Metoda 2: Instalacja runtime-only (produkcja)

```bash
# Tylko runtime dependencies, bez dev tools
pip install -e ".[compression,s3]"
```

### Metoda 3: Docker (izolowane środowisko)

```bash
# Build image
docker build -t des-migrate:latest .

# Uruchomienie migracji w kontenerze
docker run --rm \
  -v $(pwd)/config:/config:ro \
  -v $(pwd)/data:/data \
  -v $(pwd)/output:/output \
  des-migrate:latest \
  des-migrate --config /config/migration.yaml
```

### Weryfikacja instalacji

```bash
# Sprawdzenie wersji i dostępnych komend
des-migrate --version
des-stats --version

# Sprawdzenie zależności
pip list | grep -E "(sqlalchemy|psycopg|pyyaml|zstandard)"

# Output powinien pokazać:
# SQLAlchemy        2.0.x
# psycopg           3.1.x
# PyYAML            6.0.x
# zstandard         0.23.x
```

---

## 🗄️ Przygotowanie bazy danych

### Schemat tabeli źródłowej

Twoja tabela **MUSI** zawierać następujące kolumny (nazwy mogą się różnić):

```sql
CREATE TABLE files (
    -- UID: unikalny identyfikator pliku (wymagane)
    uid VARCHAR(255) NOT NULL,
    
    -- Timestamp utworzenia (wymagane)
    created_at TIMESTAMP NOT NULL,
    
    -- Ścieżka do pliku w filesystemie LUB S3 URI (s3://bucket/key) (wymagane)
    file_location VARCHAR(1024) NOT NULL,
    
    -- Rozmiar w bajtach (opcjonalne ale ZALECANE)
    size_bytes BIGINT,
    
    -- Flaga archiwizacji (wymagane)
    archived BOOLEAN DEFAULT FALSE NOT NULL,
    
    -- Twoje dodatkowe kolumny...
    -- metadata JSONB,
    -- file_type VARCHAR(50),
    -- itp.
);
```

### Niezbędne indeksy

**KRYTYCZNE dla wydajności:**

```sql
-- Index kompozytowy dla keyset pagination
CREATE INDEX idx_files_created_archived 
ON files(created_at ASC, archived) 
WHERE archived = FALSE;

-- Index na UID dla szybkich update'ów
CREATE INDEX idx_files_uid ON files(uid);
```

**Dlaczego te indeksy są ważne?**
- `idx_files_created_archived` - używany do paginacji przez MigrationOrchestrator
- Bez tego indeksu query będzie robić FULL TABLE SCAN (katastrofa dla miliardów wierszy!)
- `idx_files_uid` - przyspiesza UPDATE podczas oznaczania plików jako archived

### Przykłady dla różnych baz danych

#### PostgreSQL

```sql
-- Pełna definicja tabeli z optimized types
CREATE TABLE files (
    uid VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    file_location TEXT NOT NULL,
    size_bytes BIGINT,
    archived BOOLEAN DEFAULT FALSE NOT NULL,
    metadata JSONB,
    created_by VARCHAR(100),
    
    CONSTRAINT pk_files PRIMARY KEY (uid)
);

-- Indeksy
CREATE INDEX idx_files_created_archived 
ON files(created_at ASC, archived) 
WHERE archived = FALSE;

CREATE INDEX idx_files_uid ON files(uid);

-- Opcjonalne: partial index dla niezarchiwizowanych
CREATE INDEX idx_files_not_archived 
ON files(created_at) 
WHERE archived = FALSE;

-- Statystyki dla query plannera
ANALYZE files;
```

#### MySQL

```sql
CREATE TABLE files (
    uid VARCHAR(255) NOT NULL,
    created_at DATETIME NOT NULL,
    file_location VARCHAR(1024) NOT NULL,
    size_bytes BIGINT,
    archived BOOLEAN DEFAULT FALSE NOT NULL,
    
    PRIMARY KEY (uid),
    INDEX idx_files_created_archived (created_at ASC, archived)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

#### SQLite (development)

```sql
CREATE TABLE files (
    uid TEXT NOT NULL PRIMARY KEY,
    created_at TIMESTAMP NOT NULL,
    file_location TEXT NOT NULL,
    size_bytes INTEGER,
    archived INTEGER DEFAULT 0 NOT NULL  -- SQLite używa INT dla BOOLEAN
);

CREATE INDEX idx_files_created_archived 
ON files(created_at ASC, archived) 
WHERE archived = 0;
```

### Import istniejących danych

Jeśli masz już pliki i chcesz je zmigrować, utwórz wpisy w tabeli:

```python
# Przykład: scan directory i wstaw do PostgreSQL
import os
from datetime import datetime
from pathlib import Path
import psycopg

DATA_DIR = "/mnt/archive/files"
conn = psycopg.connect("dbname=mydb user=postgres")

with conn.cursor() as cur:
    for root, dirs, files in os.walk(DATA_DIR):
        for filename in files:
            filepath = Path(root) / filename
            stat = filepath.stat()
            
            cur.execute("""
                INSERT INTO files (uid, created_at, file_location, size_bytes, archived)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (uid) DO NOTHING
            """, (
                filename,  # lub sha256(filepath)
                datetime.fromtimestamp(stat.st_mtime),
                str(filepath),
                stat.st_size,
                False
            ))
    
    conn.commit()
```

---

## ⚙️ Konfiguracja migracji

### Format pliku konfiguracyjnego

DES wspiera **YAML** (zalecane) i **JSON**. Poniżej kompleksowy przykład:

### Przykład: `migration-config.yaml`

```yaml
# ============================================
# SEKCJA: DATABASE (połączenie do źródła)
# ============================================
database:
  # Connection string (SQLAlchemy format)
  # PostgreSQL: postgresql+psycopg://user:password@host:port/database
  # MySQL: mysql+pymysql://user:password@host:port/database
  # SQLite: sqlite+pysqlite:///path/to/database.db
  url: "postgresql+psycopg://archive_user:${DB_PASSWORD}@db.example.com:5432/archive_db"
  
  # Nazwa tabeli z metadanymi plików
  table_name: "files"
  
  # Nazwy kolumn (dostosuj do swojego schematu)
  uid_column: "uid"
  created_at_column: "created_at"
  file_location_column: "file_location"
  size_bytes_column: "size_bytes"      # Opcjonalne, ale zalecane
  archived_column: "archived"
  
  # Connection pooling (opcjonalne, domyślne wartości)
  pool_size: 10              # Ilość aktywnych połączeń
  max_overflow: 20           # Dodatkowe połączenia w szczycie
  pool_timeout: 30           # Timeout w sekundach
  pool_pre_ping: true        # Sprawdzaj połączenia przed użyciem

# ============================================
# SEKCJA: MIGRATION (parametry procesu)
# ============================================
migration:
  # Wiek plików do archiwizacji (dni)
  # Pliki starsze niż (now - archive_age_days) będą migrowane
  archive_age_days: 7
  
  # Rozmiar batcha (ile plików na raz)
  # Optymalne: 100-1000 dla większości workloadów
  batch_size: 1000
  
  # Czy usuwać pliki źródłowe po udanej migracji
  # UWAGA: Ustaw true tylko jeśli masz backup!
  delete_source_files: false
  
  # Retry policy (opcjonalne)
  max_retries: 3
  retry_delay_seconds: 5

# ============================================
# SEKCJA: PACKER (konfiguracja pakowania)
# ============================================
packer:
  # Katalog wyjściowy dla shardów DES
  output_dir: "/mnt/archive/des_output"
  
  # Maksymalny rozmiar sharda (bajty)
  # 1GB = 1_000_000_000
  # Zalecane: 500MB-2GB dla S3
  max_shard_size: 1000000000
  
  # Liczba bitów dla routingu (4-16)
  # n_bits=8 → 256 możliwych shardów na dzień/godzinę
  # n_bits=12 → 4096 możliwych shardów
  n_bits: 8
  
  # Kompresja (opcjonalne)
  compression:
    # Algorytm: zstd (zalecane) lub lz4
    algorithm: "zstd"
    
    # Poziom kompresji zstd (1-22, domyślnie 3)
    level: 3
    
    # Rozszerzenia do pominięcia (już skompresowane)
    skip_extensions:
      - ".jpg"
      - ".jpeg"
      - ".png"
      - ".mp4"
      - ".zip"
      - ".gz"

  # Źródło plików S3 (opcjonalne; fallback do lokalnych ścieżek gdy disabled)
  s3_source:
    enabled: true
    region_name: "us-east-1"       # opcjonalne
    endpoint_url: null             # opcjonalne (MinIO/LocalStack)
    max_retries: 3
    retry_delay_seconds: 2

# ============================================
# SEKCJA: LOGGING (opcjonalne)
# ============================================
logging:
  level: "INFO"           # DEBUG, INFO, WARNING, ERROR
  format: "json"          # json lub text
  file: "/var/log/des/migration.log"

# ============================================
# SEKCJA: METRICS (Prometheus, opcjonalne)
# ============================================
metrics:
  enabled: true
  port: 9090
  path: "/metrics"
```

### Przykład JSON (równoważny)

```json
{
  "database": {
    "url": "postgresql+psycopg://user:${DB_PASSWORD}@host:5432/db",
    "table_name": "files",
    "uid_column": "uid",
    "created_at_column": "created_at",
    "file_location_column": "file_location",
    "size_bytes_column": "size_bytes",
    "archived_column": "archived"
  },
  "migration": {
    "archive_age_days": 7,
    "batch_size": 1000,
    "delete_source_files": false
  },
  "packer": {
    "output_dir": "/mnt/archive/des_output",
    "max_shard_size": 1000000000,
    "n_bits": 8
  }
}
```

### Zmienne środowiskowe

DES wspiera **podstawianie zmiennych środowiskowych** w plikach konfiguracyjnych:

```yaml
database:
  url: "postgresql+psycopg://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:5432/${DB_NAME}"
  
packer:
  output_dir: "${DES_OUTPUT_DIR:-/tmp/des_output}"  # Z wartością domyślną
```

Użycie:

```bash
export DB_USER=archive_user
export DB_PASSWORD=secret123
export DB_HOST=db.prod.example.com
export DB_NAME=archive_db
export DES_OUTPUT_DIR=/mnt/archive/des

des-migrate --config migration-config.yaml
```

### Profilie konfiguracyjne

Dla różnych środowisk możesz mieć oddzielne pliki:

```
config/
├── migration-dev.yaml       # Development (SQLite, małe batche)
├── migration-staging.yaml   # Staging (PostgreSQL, średnie batche)
└── migration-prod.yaml      # Production (PostgreSQL, duże batche)
```

Wybór profilu:

```bash
# Development
des-migrate --config config/migration-dev.yaml

# Production
des-migrate --config config/migration-prod.yaml
```

---

## 🧪 Pierwsze uruchomienie (dry-run)

Przed faktyczną migracją **ZAWSZE** uruchom dry-run, aby zobaczyć:
- Ile plików zostanie zmigrowanych
- Całkowity rozmiar danych
- Najstarszy i najnowszy plik
- Czy konfiguracja jest poprawna

### Dry-run z des-stats

```bash
# Podstawowe użycie
des-stats --config migration-config.yaml

# Z większą ilością szczegółów (verbose)
des-stats --config migration-config.yaml --verbose

# Zapisz wyniki do pliku
des-stats --config migration-config.yaml > migration-preview.txt
```

### Przykładowy output

```
=======================================================
DES Migration Statistics (Dry-Run)
=======================================================

Configuration:
  Database URL: postgresql+psycopg://***@db.example.com:5432/archive_db
  Table: files
  Archive age: 7 days
  Cutoff date: 2024-11-23 00:00:00 UTC

Results:
  Total files to migrate: 1,234,567
  Total size: 456.78 GB
  Oldest file: 2024-01-15 08:23:45 UTC
  Newest file: 2024-11-22 23:59:12 UTC
  
  Estimated shards: 457
  Avg shard size: 999.5 MB
  
Recommendations:
  ✓ Batch size (1000) is optimal
  ✓ Archive age (7 days) leaves buffer
  ⚠ Large migration - consider running overnight
  ⚠ Ensure 913 GB free disk space (2x data size)

=======================================================
```

### Weryfikacja przed migracją

Checklist przed uruchomieniem właściwej migracji:

```bash
# 1. Sprawdź połączenie z bazą
psql -h db.example.com -U archive_user -d archive_db -c "SELECT COUNT(*) FROM files WHERE archived = false;"

# 2. Sprawdź dostępne miejsce na dysku
df -h /mnt/archive/

# 3. Weryfikuj przykładowe ścieżki plików
psql -h db.example.com -U archive_user -d archive_db -c "SELECT file_location FROM files WHERE archived = false LIMIT 5;"

# 4. Sprawdź czy pliki istnieją
ls -lh /ścieżka/z/przykładowego/pliku

# 5. Testuj konfigurację DES
des-migrate --config migration-config.yaml --dry-run
```

---

## 🚀 Migracja jednorazowa

Migracja jednorazowa (single-run) to proces, który:
1. Odczytuje pliki starsze niż `archive_age_days`
2. Pakuje je do DES shardów
3. Oznacza jako `archived = true`
4. Kończy działanie

### Podstawowe uruchomienie

```bash
# Najprostsze użycie
des-migrate --config migration-config.yaml

# Z verbose logging
des-migrate --config migration-config.yaml --verbose

# Z przekierowaniem logów do pliku
des-migrate --config migration-config.yaml 2>&1 | tee migration-$(date +%Y%m%d).log
```

### Output migracji

```
2024-11-30 10:00:00 INFO Starting DES migration cycle
2024-11-30 10:00:00 INFO Config loaded from: migration-config.yaml
2024-11-30 10:00:00 INFO Database: postgresql+psycopg://***@db.example.com/archive_db
2024-11-30 10:00:00 INFO Archive cutoff: 2024-11-23 00:00:00
2024-11-30 10:00:01 INFO Fetching batch of 1000 files...
2024-11-30 10:00:02 INFO Validating 1000 files...
2024-11-30 10:00:03 INFO Validation: 998 OK, 2 failed
2024-11-30 10:00:03 WARN Validation failed for uid=abc123: file does not exist
2024-11-30 10:00:03 WARN Validation failed for uid=def456: size mismatch (expected 1024, got 512)
2024-11-30 10:00:03 INFO Packing 998 validated files...
2024-11-30 10:00:15 INFO Created shard: 20241123/08_00A5.des (234.5 MB, 156 files)
2024-11-30 10:00:28 INFO Created shard: 20241123/09_00B2.des (678.2 MB, 432 files)
2024-11-30 10:00:42 INFO Created shard: 20241123/10_00C1.des (512.8 MB, 410 files)
2024-11-30 10:00:43 INFO Marking 998 files as archived in database...
2024-11-30 10:00:44 INFO Successfully marked 998 files as archived
2024-11-30 10:00:44 INFO Migration cycle completed successfully
2024-11-30 10:00:44 INFO 
================================================
Migration Summary:
================================================
Files processed: 1000
Files migrated: 998
Files failed: 2
Shards created: 3
Total size: 1.4 GB
Duration: 44.2 seconds
Throughput: 31.6 MB/s
================================================
```

### Obsługa błędów

DES izoluje błędy na poziomie pojedynczego pliku - jeden uszkodzony plik nie zatrzymuje całej migracji.

Typowe błędy i rozwiązania:

```bash
# Błąd: plik nie istnieje
ERROR: Validation failed for uid=file123: file does not exist at /path/to/file.txt
# Rozwiązanie: usuń wpis z bazy lub napraw ścieżkę

# Błąd: niezgodność rozmiaru
ERROR: Validation failed for uid=file456: size mismatch (expected 1024, got 512)
# Rozwiązanie: update size_bytes w bazie lub zweryfikuj plik

# Błąd: brak uprawnień
ERROR: Permission denied: /path/to/restricted/file.dat
# Rozwiązanie: sprawdź uprawnienia, uruchom jako odpowiedni user

# Błąd: brak miejsca na dysku
ERROR: No space left on device
# Rozwiązanie: zwolnij miejsce lub zmień output_dir
```

### Raport po migracji

Po zakończeniu sprawdź:

```bash
# 1. Ilość utworzonych shardów
ls -lh /mnt/archive/des_output/

# Przykładowy output:
# drwxr-xr-x 2 user group 4.0K Nov 30 10:00 20241123/
# drwxr-xr-x 2 user group 4.0K Nov 30 10:00 20241124/

ls -lh /mnt/archive/des_output/20241123/
# -rw-r--r-- 1 user group 234M Nov 30 10:00 08_00A5.des
# -rw-r--r-- 1 user group 678M Nov 30 10:00 09_00B2.des
# -rw-r--r-- 1 user group 512M Nov 30 10:00 10_00C1.des

# 2. Weryfikacja w bazie
psql -h db.example.com -U archive_user -d archive_db <<EOF
SELECT 
  archived,
  COUNT(*) as count,
  SUM(size_bytes) as total_bytes
FROM files
GROUP BY archived;
EOF

# Output:
# archived | count   | total_bytes
# ---------+---------+-------------
# f        | 234567  | 123456789012
# t        | 1000    | 1426063360
```

---

## 🔄 Migracja ciągła (continuous)

Continuous mode uruchamia migrację w pętli z określonym interwałem. Idealny dla:
- Regularnej archiwizacji w tle
- Deployment jako daemon/service
- Kubernetes CronJob

### Uruchomienie continuous mode

```bash
# Uruchom z 1-godzinnym interwałem (domyślnie 3600s)
des-migrate --config migration-config.yaml --continuous --interval 3600

# Custom interval (30 minut)
des-migrate --config migration-config.yaml --continuous --interval 1800

# W tle (daemon)
nohup des-migrate --config migration-config.yaml --continuous --interval 3600 \
  >> /var/log/des/migration.log 2>&1 &

# Zapisz PID dla późniejszego zatrzymania
echo $! > /var/run/des-migrate.pid
```

### Zatrzymanie continuous mode

```bash
# Graceful shutdown (pozwól dokończyć aktualny cykl)
kill -TERM $(cat /var/run/des-migrate.pid)

# Natychmiastowe zatrzymanie (nie zalecane)
kill -9 $(cat /var/run/des-migrate.pid)
```

### Systemd service (Linux)

Utwórz `/etc/systemd/system/des-migrate.service`:

```ini
[Unit]
Description=DES Migration Service
After=network.target postgresql.service

[Service]
Type=simple
User=des
Group=des
WorkingDirectory=/opt/des
Environment="PATH=/opt/des/.venv/bin"
ExecStart=/opt/des/.venv/bin/des-migrate \
  --config /etc/des/migration-config.yaml \
  --continuous \
  --interval 3600

# Restart policy
Restart=on-failure
RestartSec=30

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=des-migrate

# Security
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/mnt/archive/des_output

[Install]
WantedBy=multi-user.target
```

Zarządzanie service:

```bash
# Reload systemd
sudo systemctl daemon-reload

# Start service
sudo systemctl start des-migrate

# Enable autostart
sudo systemctl enable des-migrate

# Check status
sudo systemctl status des-migrate

# View logs
sudo journalctl -u des-migrate -f

# Stop service
sudo systemctl stop des-migrate
```

### Logowanie w continuous mode

```bash
# Rotation logów (logrotate)
cat > /etc/logrotate.d/des-migrate <<EOF
/var/log/des/migration.log {
    daily
    rotate 7
    compress
    delaycompress
    notifempty
    create 0644 des des
    sharedscripts
    postrotate
        systemctl reload des-migrate > /dev/null 2>&1 || true
    endscript
}
EOF
```

---

## 📊 Monitoring i metryki

DES exportuje metryki w formacie Prometheus na porcie 9090 (domyślnie).

### Dostępne metryki

```python
# Counters (rosną monotonnie)
des_migration_cycles_total{status="success|failure"}  # Ilość cykli migracji
des_migration_files_total                             # Łączna liczba przetworzonych plików
des_migration_bytes_total                             # Łączna ilość bajtów
des_s3_source_reads_total{status="success|error"}     # Liczba odczytów z S3 jako źródła
des_s3_source_bytes_downloaded                        # Łącznie pobrane bajty z S3

# Histogram (rozkład czasu trwania)
des_migration_duration_seconds{quantile="0.5|0.9|0.99"}
des_s3_source_read_seconds{status="success|error"}

# Gauges (aktualna wartość)
des_migration_pending_files                           # Pliki czekające na migrację
des_migration_batch_size                              # Aktualny rozmiar batcha
```

### Przykładowe zapytania PromQL

```promql
# Success rate (%)
100 * (
  rate(des_migration_cycles_total{status="success"}[5m])
  /
  rate(des_migration_cycles_total[5m])
)

# Throughput (pliki/s)
rate(des_migration_files_total[5m])

# Data throughput (MB/s)
rate(des_migration_bytes_total[5m]) / 1024 / 1024

# P95 migration duration
histogram_quantile(0.95, des_migration_duration_seconds_bucket)

# Pending files trend
des_migration_pending_files
```

### Grafana Dashboard

Przykładowa konfiguracja dashboardu:

```json
{
  "dashboard": {
    "title": "DES Migration Monitoring",
    "panels": [
      {
        "title": "Migration Success Rate",
        "targets": [{
          "expr": "100 * (rate(des_migration_cycles_total{status=\"success\"}[5m]) / rate(des_migration_cycles_total[5m]))"
        }],
        "type": "graph"
      },
      {
        "title": "Files Migrated (rate)",
        "targets": [{
          "expr": "rate(des_migration_files_total[5m])"
        }],
        "type": "graph"
      },
      {
        "title": "Pending Files",
        "targets": [{
          "expr": "des_migration_pending_files"
        }],
        "type": "stat"
      },
      {
        "title": "Migration Duration P95",
        "targets": [{
          "expr": "histogram_quantile(0.95, des_migration_duration_seconds_bucket)"
        }],
        "type": "graph"
      }
    ]
  }
}
```

### Alerting (Prometheus)

Przykładowy plik alertów `/etc/prometheus/alerts/des.yml`:

```yaml
groups:
  - name: des_migration
    interval: 30s
    rules:
      # Alert: High error rate
      - alert: DESHighErrorRate
        expr: |
          (
            rate(des_migration_cycles_total{status="failure"}[5m])
            /
            rate(des_migration_cycles_total[5m])
          ) > 0.05
        for: 10m
        labels:
          severity: warning
          component: des-migration
        annotations:
          summary: "DES migration error rate above 5%"
          description: "{{ $value | humanizePercentage }} of migrations failing"
          
      # Alert: No migrations running
      - alert: DESMigrationStalled
        expr: |
          rate(des_migration_cycles_total[10m]) == 0
        for: 15m
        labels:
          severity: critical
          component: des-migration
        annotations:
          summary: "DES migration has stalled"
          description: "No migration cycles detected in 15 minutes"
          
      # Alert: Large backlog
      - alert: DESLargeBacklog
        expr: des_migration_pending_files > 1000000
        for: 1h
        labels:
          severity: warning
          component: des-migration
        annotations:
          summary: "DES has large backlog of pending files"
          description: "{{ $value }} files waiting for migration"
          
      # Alert: Slow migrations
      - alert: DESSlowMigrations
        expr: |
          histogram_quantile(0.95, 
            rate(des_migration_duration_seconds_bucket[5m])
          ) > 300
        for: 15m
        labels:
          severity: warning
          component: des-migration
        annotations:
          summary: "DES migrations are slow (P95 > 5min)"
          description: "95th percentile: {{ $value | humanizeDuration }}"
```

---

## ☸️ Deployment w Kubernetes

### ConfigMap z konfiguracją

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: des-migration-config
  namespace: data-archive
data:
  migration-config.yaml: |
    database:
      url: "postgresql+psycopg://${DB_USER}:${DB_PASSWORD}@postgres.data-archive.svc:5432/archive_db"
      table_name: "files"
      uid_column: "uid"
      created_at_column: "created_at"
      file_location_column: "file_location"
      size_bytes_column: "size_bytes"
      archived_column: "archived"
    migration:
      archive_age_days: 7
      batch_size: 1000
      delete_source_files: false
    packer:
      output_dir: "/mnt/des-output"
      max_shard_size: 1000000000
      n_bits: 8
```

### Secret z credentials

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: des-db-credentials
  namespace: data-archive
type: Opaque
stringData:
  DB_USER: "archive_user"
  DB_PASSWORD: "super-secret-password"
```

### CronJob dla regularnej migracji

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: des-migration
  namespace: data-archive
spec:
  # Uruchom co godzinę
  schedule: "0 * * * *"
  
  # Concurrent policy
  concurrencyPolicy: Forbid  # Nie pozwalaj na nakładające się joby
  
  # Job history
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  
  jobTemplate:
    spec:
      template:
        metadata:
          labels:
            app: des-migration
        spec:
          restartPolicy: OnFailure
          
          # Service account (jeśli potrzebny)
          serviceAccountName: des-migration
          
          containers:
          - name: migration
            image: des-migrate:v0.3.0
            imagePullPolicy: IfNotPresent
            
            command:
            - des-migrate
            - --config
            - /config/migration-config.yaml
            
            env:
            - name: DB_USER
              valueFrom:
                secretKeyRef:
                  name: des-db-credentials
                  key: DB_USER
            - name: DB_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: des-db-credentials
                  key: DB_PASSWORD
            
            # Resource limits
            resources:
              requests:
                memory: "2Gi"
                cpu: "1000m"
              limits:
                memory: "4Gi"
                cpu: "2000m"
            
            # Volumes
            volumeMounts:
            - name: config
              mountPath: /config
              readOnly: true
            - name: des-output
              mountPath: /mnt/des-output
            - name: source-files
              mountPath: /mnt/source-files
              readOnly: true
          
          volumes:
          - name: config
            configMap:
              name: des-migration-config
          - name: des-output
            persistentVolumeClaim:
              claimName: des-output-pvc
          - name: source-files
            persistentVolumeClaim:
              claimName: source-files-pvc
```

### Deployment dla continuous mode

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: des-migration-daemon
  namespace: data-archive
spec:
  replicas: 1  # Tylko jedna instancja (stateful proces)
  
  selector:
    matchLabels:
      app: des-migration-daemon
  
  template:
    metadata:
      labels:
        app: des-migration-daemon
    spec:
      containers:
      - name: migration
        image: des-migrate:v0.3.0
        
        command:
        - des-migrate
        - --config
        - /config/migration-config.yaml
        - --continuous
        - --interval
        - "3600"
        
        env:
        - name: DB_USER
          valueFrom:
            secretKeyRef:
              name: des-db-credentials
              key: DB_USER
        - name: DB_PASSWORD
          valueFrom:
            secretKeyRef:
              name: des-db-credentials
              key: DB_PASSWORD
        
        # Liveness probe
        livenessProbe:
          exec:
            command:
            - pgrep
            - -f
            - des-migrate
          initialDelaySeconds: 30
          periodSeconds: 60
        
        # Resource limits
        resources:
          requests:
            memory: "2Gi"
            cpu: "500m"
          limits:
            memory: "4Gi"
            cpu: "2000m"
        
        volumeMounts:
        - name: config
          mountPath: /config
          readOnly: true
        - name: des-output
          mountPath: /mnt/des-output
        - name: source-files
          mountPath: /mnt/source-files
          readOnly: true
      
      volumes:
      - name: config
        configMap:
          name: des-migration-config
      - name: des-output
        persistentVolumeClaim:
          claimName: des-output-pvc
      - name: source-files
        persistentVolumeClaim:
          claimName: source-files-pvc
```

### Service dla Prometheus metrics

```yaml
apiVersion: v1
kind: Service
metadata:
  name: des-migration-metrics
  namespace: data-archive
  labels:
    app: des-migration-daemon
spec:
  selector:
    app: des-migration-daemon
  ports:
  - name: metrics
    port: 9090
    targetPort: 9090
    protocol: TCP
```

### ServiceMonitor (Prometheus Operator)

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: des-migration
  namespace: data-archive
spec:
  selector:
    matchLabels:
      app: des-migration-daemon
  endpoints:
  - port: metrics
    interval: 30s
    path: /metrics
```

---

## 🔧 Troubleshooting

### Problem: Wolna migracja

**Symptomy:** Migracja trwa bardzo długo, niski throughput.

**Możliwe przyczyny i rozwiązania:**

```bash
# 1. Brak indeksów w bazie danych
# Sprawdź:
psql -h db -U user -d archive_db -c "\d+ files"

# Powinno pokazać:
# "idx_files_created_archived" btree (created_at, archived) WHERE archived = false

# Jeśli brak, dodaj:
CREATE INDEX CONCURRENTLY idx_files_created_archived 
ON files(created_at, archived) 
WHERE archived = false;

# 2. Zbyt mały batch_size
# Zwiększ w konfiguracji:
migration:
  batch_size: 5000  # Zamiast 100

# 3. Wolna kompresja
# Obniż poziom kompresji:
packer:
  compression:
    algorithm: "lz4"  # Szybszy niż zstd
    level: 1

# 4. Wolny I/O do plików
# Sprawdź performance:
iostat -x 1
# Jeśli %util > 80%, storage jest wąskim gardłem

# 5. Connection pooling jest za mały
database:
  pool_size: 20      # Zwiększ z 10
  max_overflow: 40   # Zwiększ z 20
```

### Problem: Out of Memory (OOM)

**Symptomy:** Proces zabijany przez OOM killer, `MemoryError`.

**Rozwiązania:**

```bash
# 1. Zmniejsz batch_size
migration:
  batch_size: 100  # Zamiast 1000

# 2. Ogranicz max_shard_size
packer:
  max_shard_size: 500000000  # 500MB zamiast 1GB

# 3. Zwiększ pamięć w Kubernetes
resources:
  limits:
    memory: "8Gi"  # Zwiększ limit

# 4. Monitoring pamięci
watch -n 1 'ps aux | grep des-migrate | awk "{print \$6}"'
```

### Problem: Database connection errors

**Symptomy:** `OperationalError`, `Connection refused`, timeout.

**Rozwiązania:**

```bash
# 1. Sprawdź łączność
telnet db.example.com 5432

# 2. Sprawdź credentials
psql -h db.example.com -U archive_user -d archive_db -c "SELECT 1;"

# 3. Zwiększ timeout
database:
  pool_timeout: 60  # Z 30s

# 4. Enable pre-ping (wykrywanie martwych połączeń)
database:
  pool_pre_ping: true

# 5. Sprawdź logi PostgreSQL
tail -f /var/log/postgresql/postgresql-*.log
```

### Problem: Pliki nie mogą być odczytane

**Symptomy:** `Permission denied`, `File not found`.

**Rozwiązania:**

```bash
# 1. Sprawdź uprawnienia
ls -la /ścieżka/do/plików/

# 2. Sprawdź czy katalog jest zamontowany (NFS)
mount | grep /mnt/source-files

# 3. Uruchom jako właściwy user
# Sprawdź ownership plików:
ls -la /mnt/source-files/ | head -n 20

# Jeśli pliki należą do user "data":
sudo -u data des-migrate --config migration-config.yaml

# 4. W Dockerze: zmapuj user ID
docker run --user $(id -u):$(id -g) ...
```

### Problem: Duplikaty UID w bazie

**Symptomy:** `IntegrityError`, `UNIQUE constraint failed`.

**Rozwiązania:**

```sql
-- Znajdź duplikaty
SELECT uid, COUNT(*) 
FROM files 
GROUP BY uid 
HAVING COUNT(*) > 1;

-- Usuń duplikaty (zachowaj najnowszy)
WITH duplicates AS (
  SELECT uid, created_at,
    ROW_NUMBER() OVER (PARTITION BY uid ORDER BY created_at DESC) as rn
  FROM files
)
DELETE FROM files
WHERE (uid, created_at) IN (
  SELECT uid, created_at FROM duplicates WHERE rn > 1
);

-- Lub dodaj UNIQUE constraint jeśli brak
ALTER TABLE files ADD CONSTRAINT uk_files_uid UNIQUE (uid);
```

### Debugging z verbose logging

```bash
# Włącz DEBUG level
export DES_LOG_LEVEL=DEBUG
des-migrate --config migration-config.yaml --verbose

# Lub w konfiguracji:
logging:
  level: "DEBUG"

# Śledzenie SQL queries
export SQLALCHEMY_ECHO=1
```

---

## 💡 Best Practices

### 1. Rozpocznij od małego testu

```bash
# Testowa konfiguracja: mała liczba plików
migration:
  archive_age_days: 365  # Bardzo stare pliki (niewiele ich)
  batch_size: 10         # Mały batch

# Uruchom i zweryfikuj
des-stats --config test-config.yaml
des-migrate --config test-config.yaml

# Sprawdź wyniki
ls -lh /output/dir/
```

### 2. Optymalizacja batch_size

```yaml
# Zależnie od rozmiaru plików:

# Małe pliki (< 1MB):
migration:
  batch_size: 5000

# Średnie pliki (1-10MB):
migration:
  batch_size: 1000

# Duże pliki (> 10MB):
migration:
  batch_size: 100
```

### 3. Monitoring i alerty

- Skonfiguruj Prometheus + Grafana **przed** produkcją
- Ustaw alerty na error rate > 1%
- Monitoruj pending files (backlog)
- Sprawdzaj disk usage regularnie

### 4. Backup przed delete_source_files

```yaml
# NIGDY nie włączaj bez backupu!
migration:
  delete_source_files: false  # Domyślnie

# Tylko jeśli masz:
# - Backup wszystkich plików
# - Weryfikację integralności shardów
# - Przetestowany proces recovery
```

### 5. Incremental migration

```yaml
# Zamiast migrować wszystko na raz:

# Tydzień 1: pliki > 180 dni
migration:
  archive_age_days: 180

# Tydzień 2: pliki > 90 dni
migration:
  archive_age_days: 90

# Tydzień 3: pliki > 30 dni
migration:
  archive_age_days: 30

# Tydzień 4: pliki > 7 dni (docelowo)
migration:
  archive_age_days: 7
```

### 6. Database maintenance

```sql
-- Regularnie (tygodniowo):

-- Aktualizuj statystyki
ANALYZE files;

-- Sprawdź index bloat
SELECT 
  schemaname, tablename, 
  pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables 
WHERE tablename = 'files';

-- Reindex jeśli potrzeba (w oknie maintenance)
REINDEX INDEX CONCURRENTLY idx_files_created_archived;
```

### 7. Shard size tuning

```yaml
# S3-optimized sizes:
packer:
  max_shard_size: 1000000000   # 1GB - optimal dla S3
  # Możliwe: 500MB - 2GB
  # Unikaj: > 5GB (wolny upload/download)
  # Unikaj: < 100MB (za dużo małych obiektów)
```

### 8. Compression strategy

```yaml
# Dla mieszanych typów plików:
packer:
  compression:
    algorithm: "zstd"
    level: 3  # Balance speed/ratio
    skip_extensions:
      # Już skompresowane
      - ".jpg"
      - ".jpeg"
      - ".png"
      - ".gif"
      - ".mp4"
      - ".avi"
      - ".zip"
      - ".gz"
      - ".bz2"
      - ".xz"
      - ".7z"
```

### 9. Graceful shutdown

```python
# W systemd service:
[Service]
# Daj 60s na dokończenie cyklu przed SIGKILL
TimeoutStopSec=60
KillMode=mixed

# W kodzie aplikacji DES już obsługuje:
# SIGTERM - graceful shutdown
# SIGINT - graceful shutdown
```

### 10. Regular verification

```bash
# Miesięcznie: weryfikuj losowe shardy
#!/bin/bash

# Wybierz 100 losowych shardów
find /mnt/des-output -name "*.des" | shuf -n 100 > /tmp/verify-list.txt

# Weryfikuj (gdy DES 0.4.0 dodaje des-verify)
# while read shard; do
#   des-verify --shard "$shard"
# done < /tmp/verify-list.txt
```

---

## ❓ FAQ

### Q: Czy mogę uruchomić wiele instancji jednocześnie?

**A:** NIE dla tego samego `output_dir`. DES nie ma distributed locking. Możesz uruchomić wiele instancji, jeśli:
- Używają różnych `output_dir`
- Lub dzielą przestrzeń shardów (shard filtering - feature not yet implemented)

### Q: Co się stanie jeśli migracja zostanie przerwana?

**A:** DES jest idempotentny. Następne uruchomienie:
- Ponownie przetworzy niezarchiwizowane pliki
- Utworzy nowe shardy
- NIE nadpisze istniejących shardów (append-only)

### Q: Jak długo trwa migracja 1TB danych?

**A:** Zależne od:
- I/O storage: 100-500 MB/s
- Kompresja: 50-200 MB/s (core)
- Database: ograniczenie przez IOPS

Szacunkowo:
- **1TB nieskompreswowanych plików**: 2-8 godzin
- **1TB już skompresowanych**: 1-4 godziny

### Q: Czy mogę anulować migrację w trakcie?

**A:** TAK, ale:
- `Ctrl+C` lub `SIGTERM` - dokończy aktualny batch
- `SIGKILL` - natychmiastowe zabicie (może pozostawić częściowo przetworzone batch)

Zawsze preferuj graceful shutdown.

### Q: Jak zmigrować pliki z wielu lokalizacji?

**A:** Dwie opcje:

**Opcja 1:** Osobne tabele i konfiguracje
```bash
des-migrate --config config-location-1.yaml
des-migrate --config config-location-2.yaml
```

**Opcja 2:** Jedna tabela z różnymi `file_location`
```sql
-- Tabela zawiera pliki z różnych lokalizacji
SELECT file_location FROM files LIMIT 5;
-- /mnt/storage-a/file1.dat
-- /mnt/storage-b/file2.dat
-- /mnt/storage-c/file3.dat
```

### Q: Czy mogę używać S3 URI jako `file_location`?

**A:** TAK. Ustaw w konfiguracji:

```yaml
packer:
  s3_source:
    enabled: true
    region_name: "us-east-1"   # opcjonalne
    endpoint_url: null         # opcjonalne (MinIO/LocalStack)
```

`file_location` może wtedy wskazywać `s3://bucket/key`. Brak włączonej sekcji `s3_source` spowoduje błąd przy napotkaniu URI S3.

### Q: Czy DES wspiera Windows paths?

**A:** TAK, ale musisz użyć raw strings w konfiguracji:

```yaml
# Windows path
packer:
  output_dir: "C:\\Archive\\DES"  # Escape backslashes

# Lub forward slashes (działa w Python)
packer:
  output_dir: "C:/Archive/DES"
```

### Q: Jak obsłużyć bardzo duże pliki (> 1GB)?

**A:** DES v0.3.0+ wspiera BigFiles:
- Pliki > 10MB są przechowywane osobno (poza shardem)
- Konfiguruj przez `DES_BIG_FILE_THRESHOLD_BYTES`
- Shardy zawierają tylko metadata + hash

```bash
export DES_BIG_FILE_THRESHOLD_BYTES=10485760  # 10MB
des-migrate --config migration-config.yaml
```

### Q: Czy mogę migrować pliki do S3 bezpośrednio?

**A:** TAK, użyj S3 packera (wymaga konfiguracji):

```yaml
packer:
  type: "s3"  # Zamiast lokalnego
  s3:
    bucket: "my-archive-bucket"
    region: "us-east-1"
    prefix: "des/"
```

### Q: Jak obsłużyć pliki z identycznym UID ale różnym created_at?

**A:** To jest OK! Routing używa **obu** `uid` i `created_at`:
- `(uid="file1", created_at=2024-01-01)` → shard A
- `(uid="file1", created_at=2024-06-01)` → shard B

Każda kombinacja (uid, created_at) jest unikalna.

### Q: Czy mogę zmienić n_bits po rozpoczęciu migracji?

**A:** NIE ZALECANE. Zmiana `n_bits` zmienia routing:
- Stare shardy: n_bits=8 (256 shardów/dzień)
- Nowe shardy: n_bits=12 (4096 shardów/dzień)

Może prowadzić do kolizji. Jeśli musisz, migruj dane do nowej lokalizacji.

### Q: Jak usunąć plik z DES?

**A:** DES v0.3.0 **NIE** wspiera deletion. To jest planned feature (v0.4.0):
- Tombstone management
- Repack system

Na razie: shardy są immutable (WORM-compatible).

---

## 📚 Dodatkowe zasoby

### Dokumentacja projektu
- `README.md` - Przegląd projektu
- `ARCHITECTURE.md` - Szczegóły architektury
- `DEPLOYMENT.md` - Deployment w różnych środowiskach
- `ROADMAP.md` - Plany rozwoju

### Przykłady konfiguracji
- `examples/migration-config.yaml` - Pełny przykład YAML
- `examples/migration-config.json` - Pełny przykład JSON
- `examples/zones.yaml` - Multi-S3 zones

### CI/CD
- `.github/workflows/ci.yml` - GitHub Actions pipeline

### Wsparcie
- GitHub Issues: Zgłaszanie błędów i feature requests
- Email: des-support@example.com

---

## 📝 Changelog poradnika

**v1.0.0** (2024-11-30)
- Pierwsza wersja kompleksowego poradnika
- Pokrycie instalacji, konfiguracji, uruchomienia
- Przykłady dla PostgreSQL, MySQL, SQLite
- Kubernetes deployment
- Troubleshooting i FAQ

---

**Przygotowane przez:** Dariusz  
**Dla projektu:** DES (Data Easy Store)  
**Licencja:** MIT
