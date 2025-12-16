# DES Extended Retention Demo Environment

Kompletne środowisko demonstracyjne dla DES (Data Easy Store) z obsługą Extended Retention.

## Komponenty

### 1. MinIO (S3 Compatible Storage)
- **Port 9000**: S3 API
- **Port 9001**: Web Console
- **Credentials**: minioadmin / minioadmin
- **Bucket**: `des-bucket` z 90-day retention i Object Lock

### 2. PostgreSQL Database
- **Port 5432**
- **Database**: `business_system`
- **Credentials**: business_user / business_pass
- Automatyczna inicjalizacja schema z przykładowymi danymi

### 3. Business System Mock
- **Port 8080**: Web UI + API
- **URL**: http://localhost:8080
- Symuluje system merytoryczny (case management, legal hold, etc.)
- Funkcje:
  - Upload plików
  - Listowanie plików z filtrowaniem
  - Przedłużanie retencji (calls DES API)
  - Historia zmian retencji
  - Dashboard ze statystykami

### 4. DES API
- **Port 8000**: HTTP API
- **URL**: http://localhost:8000
- Core DES service z Extended Retention

## Szybki Start

### Wymagania
- Docker Desktop lub Docker + Docker Compose
- Min 4GB RAM dostępne dla Docker
- Porty 5432, 8000, 8080, 9000, 9001 wolne

### Uruchomienie

```bash
# 1. Clone repozytorium i przejdź do katalogu
cd /path/to/des

# 2. Uruchom wszystkie serwisy
docker-compose -f docker-compose.demo.yml up -d

# 3. Sprawdź logi (opcjonalnie)
docker-compose -f docker-compose.demo.yml logs -f

# 4. Poczekaj ~30 sekund na inicjalizację wszystkich serwisów
```

### Weryfikacja

Sprawdź czy wszystkie serwisy są dostępne:

```bash
# Business System
curl http://localhost:8080/health

# DES API
curl http://localhost:8000/health

# MinIO
curl http://localhost:9000/minio/health/live

# PostgreSQL
docker exec des-postgres pg_isready -U business_user
```

## Użycie Demo

### 1. Web UI Business System

Otwórz w przeglądarce: **http://localhost:8080**

#### Upload pliku
1. Kliknij sekcję "Upload New File"
2. Wybierz plik
3. Opcjonalnie podaj Case Number, Department, Document Type
4. Kliknij "Upload File"

#### Przedłużanie retencji
1. W tabeli plików kliknij "Extend Retention" przy wybranym pliku
2. Ustaw liczbę dni (np. 365, 730, 2555)
3. Wybierz powód (Legal Hold, Regulatory Investigation, etc.)
4. Kliknij "Extend Retention"
5. System automatycznie:
   - Wywołuje DES API endpoint
   - Jeśli pierwszy raz: kopiuje plik do `_ext_retention/`
   - Jeśli kolejny raz: tylko aktualizuje Object Lock retention
   - Zapisuje historię zmian

#### Monitoring
- Dashboard pokazuje statystyki (Total Files, Extended Retention, Active Cases)
- Filtruj pliki po statusie: All / Active / Extended / Expired
- Kliknij "Refresh" aby odświeżyć listę

### 2. MinIO Web Console

Otwórz w przeglądarce: **http://localhost:9001**

**Login**: minioadmin / minioadmin

W konsoli możesz:
- Przeglądać bucket `des-bucket`
- Sprawdzać strukturę folderów:
  - `shards/YYYYMMDD/` - główne paczki
  - `_ext_retention/YYYYMMDD/` - pliki z przedłużoną retencją
  - `tombstones/` - tombstone markers
- Weryfikować Object Lock retention dla plików
- Monitorować storage usage

### 3. DES API Direct (curl)

```bash
# Health check
curl http://localhost:8000/health

# Extend retention dla pliku
curl -X PUT http://localhost:8000/files/demo-file-001/retention-policy \
  -H "Content-Type: application/json" \
  -d '{
    "created_at": "2024-12-15T10:00:00Z",
    "due_date": "2027-12-15T00:00:00Z"
  }'

# Response:
# {
#   "uid": "demo-file-001",
#   "created_at": "2024-12-15T10:00:00Z",
#   "location": "extended_retention",
#   "retention_until": "2027-12-15T00:00:00Z",
#   "action": "moved"  # or "updated"
# }
```

### 4. PostgreSQL Direct Query

```bash
# Podłącz się do bazy
docker exec -it des-postgres psql -U business_user -d business_system

# Przykładowe queries
\dt                          # Lista tabel
SELECT * FROM files;         # Wszystkie pliki
SELECT * FROM retention_history;  # Historia zmian retencji
SELECT * FROM files_with_retention;  # View z obliczonymi datami
```

## Scenariusze Demo

### Scenariusz 1: Legal Hold
1. Upload pliku związanego z przypadkiem prawnym
2. Po 30 dniach: sprawa się przedłuża
3. Extend retention o 365 dni (Legal Hold - Ongoing Litigation)
4. Po 6 miesiącach: nowe dowody, extend o kolejne 730 dni
5. System automatycznie:
   - Pierwszy extend: kopiuje do `_ext_retention/`
   - Drugi extend: tylko update Object Lock (NO re-copy!)

### Scenariusz 2: Regulatory Audit
1. Upload wielu plików finansowych (standard 90-day retention)
2. Audyt rozpoczyna się
3. Bulk extend dla wszystkich plików case'u (2555 dni = 7 lat)
4. Po audycie: niektóre pliki mogą wrócić do standard retention

### Scenariusz 3: GDPR Right to Erasure
1. Plik w extended retention
2. Użytkownik żąda usunięcia
3. Business system usuwa retention policy
4. DES tworzy tombstone
5. Repack process usuwa fizycznie plik

## Monitoring & Troubleshooting

### Sprawdź status kontenerów
```bash
docker-compose -f docker-compose.demo.yml ps
```

### Logi
```bash
# Wszystkie serwisy
docker-compose -f docker-compose.demo.yml logs -f

# Konkretny serwis
docker-compose -f docker-compose.demo.yml logs -f business-system
docker-compose -f docker-compose.demo.yml logs -f des-api
docker-compose -f docker-compose.demo.yml logs -f minio
```

### Restart serwisu
```bash
docker-compose -f docker-compose.demo.yml restart business-system
```

### Czyszczenie środowiska
```bash
# Stop wszystkich kontenerów
docker-compose -f docker-compose.demo.yml down

# Stop + usuń volumes (UWAGA: traci dane)
docker-compose -f docker-compose.demo.yml down -v
```

## Struktura Plików Demo

```
demo/
├── init-db.sql              # PostgreSQL schema + sample data
├── business-system/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py             # FastAPI application
│   └── templates/
│       └── index.html      # Web UI
└── docker-compose.demo.yml  # Kompletny stack
```

## Endpoints

### Business System API (Port 8080)
- `GET /` - Web UI
- `GET /health` - Health check
- `GET /api/files` - List files (query params: status, case_number)
- `POST /api/files/upload` - Upload file
- `POST /api/files/{id}/extend-retention` - Extend retention
- `GET /api/files/{id}/retention-history` - Get retention change history

### DES API (Port 8000)
- `GET /health` - Health check
- `PUT /files/{uid}/retention-policy` - Set retention policy
- `GET /files/{uid}` - Retrieve file (checks extended retention first)

### MinIO S3 API (Port 9000)
- Standard S3 API endpoints
- Object Lock support

## Konfiguracja

### Environment Variables

Możesz dostosować konfigurację edytując `docker-compose.demo.yml`:

```yaml
# Business System
DATABASE_URL: postgresql://user:pass@host:5432/db
DES_API_URL: http://des-api:8000
S3_ENDPOINT: http://minio:9000
S3_ACCESS_KEY: minioadmin
S3_SECRET_KEY: minioadmin
S3_BUCKET: des-bucket

# DES API
S3_ENDPOINT: http://minio:9000
S3_ACCESS_KEY: minioadmin
S3_SECRET_KEY: minioadmin
S3_BUCKET: des-bucket
DATABASE_URL: postgresql://user:pass@host:5432/db
```

## Zaawansowane

### Custom Sample Data

Edytuj `demo/init-db.sql` aby dodać własne przykładowe dane:

```sql
INSERT INTO cases (case_number, case_name, status, department) VALUES
    ('CASE-2024-XXX', 'Your Case Name', 'open', 'Your Department');

INSERT INTO files (uid, filename, file_size, mime_type, case_number) VALUES
    ('your-uid', 'your-file.pdf', 1024000, 'application/pdf', 'CASE-2024-XXX');
```

### Integration Testing

```bash
# Run integration tests against demo environment
pytest tests/integration/ --env=demo
```

## FAQ

**Q: MinIO pokazuje "No buckets found"**  
A: Poczekaj na `minio-init` container - sprawdź logi: `docker logs des-minio-init`

**Q: Business System nie łączy się z DES API**  
A: Sprawdź czy DES API działa: `curl http://localhost:8000/health`

**Q: PostgreSQL connection refused**  
A: Database może jeszcze się inicjalizować, poczekaj ~10 sekund

**Q: Jak zresetować środowisko do stanu początkowego?**  
A: `docker-compose -f docker-compose.demo.yml down -v && docker-compose -f docker-compose.demo.yml up -d`

**Q: Czy mogę użyć prawdziwego S3 zamiast MinIO?**  
A: Tak, zmień `S3_ENDPOINT` na `https://s3.amazonaws.com` i podaj AWS credentials

## Architektura Flow

```
┌─────────────────┐
│   Web Browser   │
└────────┬────────┘
         │ HTTP
         ▼
┌─────────────────────────┐
│  Business System Mock   │◄──┐
│  (Port 8080)           │   │
│  - Web UI              │   │
│  - Upload files        │   │
│  - Manage retention    │   │
└────────┬───────┬────────┘   │
         │       │            │
         │       └────────────┤
         │ SQL               │ HTTP
         ▼                   │
┌────────────────┐           │
│   PostgreSQL   │           │
│   (Port 5432)  │           │
└────────────────┘           │
                             │
                             ▼
                    ┌──────────────┐
                    │   DES API    │
                    │  (Port 8000) │
                    │- Extended    │
                    │  Retention   │
                    └───────┬──────┘
                            │ S3 API
                            ▼
                    ┌──────────────┐
                    │    MinIO     │
                    │ (Port 9000)  │
                    │ S3 Compatible│
                    └──────────────┘
```

## Support

Issues? Questions?  
1. Sprawdź logi: `docker-compose logs -f`
2. Verify health checks: wszystkie `/health` endpoints
3. Check network: `docker network inspect des-network`

---

**Demo Environment** - DES Extended Retention System  
Version: 1.0.0  
Last Updated: 2024-12-15
