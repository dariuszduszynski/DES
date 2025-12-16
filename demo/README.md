# DES Extended Retention - Complete Demo Environment

Kompletny, gotowy do uruchomienia setup demonstracyjny dla DES (Data Easy Store) z Extended Retention Management.

## 📦 Co zawiera ten pakiet?

### Struktura plików:
```
.
├── docker-compose.demo.yml          # Docker Compose z wszystkimi serwisami
├── Makefile.demo                     # Pomocnicze komendy Make
└── demo/
    ├── README.md                     # Szczegółowa dokumentacja
    ├── QUICK_REFERENCE.md            # Szybki przewodnik komend
    ├── start-demo.sh                 # Skrypt automatycznego startu
    ├── test-api.sh                   # Zbiór testów API
    ├── .env.example                  # Przykładowa konfiguracja
    ├── init-db.sql                   # Schema PostgreSQL
    └── business-system/              # Mock systemu biznesowego
        ├── Dockerfile
        ├── requirements.txt
        ├── main.py                   # FastAPI application
        └── templates/
            └── index.html            # Web UI
```

## 🚀 Quick Start (3 kroki)

### 1. Upewnij się, że masz Docker
```bash
docker --version
docker-compose --version
```

### 2. Uruchom demo
```bash
# Metoda A: Automatyczny skrypt (rekomendowane)
chmod +x demo/start-demo.sh
./demo/start-demo.sh

# Metoda B: Docker Compose bezpośrednio
docker-compose -f docker-compose.demo.yml up -d

# Metoda C: Makefile
make -f Makefile.demo demo-start
```

### 3. Otwórz w przeglądarce
- **Business System UI**: http://localhost:8080
- **MinIO Console**: http://localhost:9001 (minioadmin/minioadmin)

## 🎯 Co robi ten setup?

### 4 główne komponenty:

1. **MinIO** (S3-compatible storage)
   - Port 9000: S3 API
   - Port 9001: Web Console
   - Bucket `des-bucket` z Object Lock enabled

2. **PostgreSQL** (Business database)
   - Port 5432
   - Automatyczna inicjalizacja schema
   - Przykładowe dane (cases, files)

3. **Business System Mock** (Web UI + API)
   - Port 8080
   - Upload plików
   - Zarządzanie retencją
   - Dashboard ze statystykami

4. **DES API** (Core service)
   - Port 8000
   - Extended Retention endpoints
   - Integration z MinIO i PostgreSQL

## 💡 Przykładowe użycie

### Upload pliku przez Web UI:
1. Otwórz http://localhost:8080
2. Wybierz plik w sekcji "Upload New File"
3. Opcjonalnie podaj Case Number i Department
4. Kliknij "Upload File"

### Przedłuż retencję:
1. W tabeli plików kliknij "Extend Retention"
2. Ustaw liczbę dni (np. 365, 730, 2555)
3. Wybierz powód (Legal Hold, Regulatory Investigation, etc.)
4. Kliknij "Extend Retention"

**Co się dzieje pod spodem:**
- **Pierwszy raz**: Plik kopiowany z głównej paczki do `_ext_retention/`, tworzony tombstone
- **Kolejne razy**: Tylko aktualizacja Object Lock retention (bez kopiowania!)

### Sprawdź w MinIO:
1. Otwórz http://localhost:9001 (minioadmin/minioadmin)
2. Przeglądaj bucket `des-bucket`
3. Sprawdź folder `_ext_retention/YYYYMMDD/`
4. Zobacz Object Lock retention dla plików

## 📚 Dokumentacja

- **[demo/README.md](demo/README.md)** - Kompletna dokumentacja, scenariusze demo
- **[demo/QUICK_REFERENCE.md](demo/QUICK_REFERENCE.md)** - Szybki przewodnik komend
- **[demo/test-api.sh](demo/test-api.sh)** - Przykłady API calls

## 🛠️ Najważniejsze komendy

```bash
# Status serwisów
docker-compose -f docker-compose.demo.yml ps

# Logi
docker-compose -f docker-compose.demo.yml logs -f

# Stop
docker-compose -f docker-compose.demo.yml stop

# Restart
docker-compose -f docker-compose.demo.yml restart

# Cleanup (USUWA WSZYSTKIE DANE!)
docker-compose -f docker-compose.demo.yml down -v
```

## 🧪 Test API

```bash
# Uruchom pełny zestaw testów
chmod +x demo/test-api.sh
./demo/test-api.sh

# Lub ręcznie:
# Health check
curl http://localhost:8080/health

# Lista plików
curl http://localhost:8080/api/files | jq '.'

# Przedłuż retencję
curl -X POST http://localhost:8080/api/files/1/extend-retention \
  -F "retention_days=365" \
  -F "reason=Legal Hold" \
  -F "updated_by=admin"
```

## 🎬 Demo Scenarios

### Scenariusz 1: Legal Hold
```bash
# 1. Upload pliku
curl -X POST http://localhost:8080/api/files/upload \
  -F "file=@document.pdf" \
  -F "case_number=LEGAL-2024-001"

# 2. Sprawa się przedłuża - extend retention (365 dni)
curl -X POST http://localhost:8080/api/files/1/extend-retention \
  -F "retention_days=365" \
  -F "reason=Legal Hold - Ongoing Litigation"

# 3. Nowe dowody - extend ponownie (730 dni)
curl -X POST http://localhost:8080/api/files/1/extend-retention \
  -F "retention_days=730" \
  -F "reason=New Evidence Found"

# 4. Zobacz historię zmian
curl http://localhost:8080/api/files/1/retention-history | jq '.'
```

### Scenariusz 2: Bulk Operations
```bash
# Upload 10 plików
for i in {1..10}; do
  curl -X POST http://localhost:8080/api/files/upload \
    -F "file=@test.pdf" -F "case_number=BULK-$i"
done

# Extend wszystkich na 2555 dni (7 lat - SEC 17a-4)
for i in {1..10}; do
  curl -X POST http://localhost:8080/api/files/$i/extend-retention \
    -F "retention_days=2555" \
    -F "reason=Regulatory Requirement - SEC 17a-4"
done
```

## 🔍 Troubleshooting

### "Port already in use"
```bash
# Sprawdź które porty są zajęte
lsof -i :8080  # Business System
lsof -i :8000  # DES API
lsof -i :9000  # MinIO API
lsof -i :5432  # PostgreSQL

# Zatrzymaj konfliktujące serwisy lub zmień porty w docker-compose.demo.yml
```

### "Cannot connect to Docker daemon"
```bash
# Sprawdź czy Docker działa
docker ps

# Jeśli nie, uruchom Docker Desktop lub dockerd
```

### Serwis nie startuje
```bash
# Zobacz szczegółowe logi
docker-compose -f docker-compose.demo.yml logs business-system

# Restart konkretnego serwisu
docker-compose -f docker-compose.demo.yml restart business-system
```

### Reset do stanu początkowego
```bash
# UWAGA: To usuwa wszystkie dane!
docker-compose -f docker-compose.demo.yml down -v
docker-compose -f docker-compose.demo.yml up -d
```

## 💾 Database Access

```bash
# Podłącz się do PostgreSQL
docker exec -it des-postgres psql -U business_user -d business_system

# Przykładowe queries
SELECT * FROM files;
SELECT * FROM retention_history;
\q  # Exit
```

## 📊 Architecture Flow

```
┌─────────────┐
│   Browser   │
│  (you!)     │
└──────┬──────┘
       │ HTTP
       ▼
┌──────────────────────┐
│ Business System Mock │
│  (Port 8080)        │
│  • Web UI           │
│  • Upload           │────┐
│  • Manage retention │    │ SQL
└──────────┬───────────┘    │
           │                ▼
           │        ┌──────────────┐
           │ HTTP   │ PostgreSQL   │
           │        │ (Port 5432)  │
           │        └──────────────┘
           ▼
    ┌──────────────┐
    │   DES API    │
    │  (Port 8000) │
    │• Extended    │
    │  Retention   │
    └──────┬───────┘
           │ S3 API
           ▼
    ┌──────────────┐
    │    MinIO     │
    │ (Port 9000)  │
    │S3 Compatible │
    └──────────────┘
```

## 🎓 Learning Path

1. **Start**: Przeczytaj ten README
2. **Run**: Uruchom `./demo/start-demo.sh`
3. **Explore**: Otwórz Web UI (http://localhost:8080)
4. **Test**: Upload pliku i przedłuż retencję
5. **Verify**: Sprawdź w MinIO Console
6. **Deep Dive**: Zobacz [demo/README.md](demo/README.md)
7. **API**: Wypróbuj [demo/test-api.sh](demo/test-api.sh)

## 🤝 Support

**Problem?** Sprawdź:
1. Logi: `docker-compose -f docker-compose.demo.yml logs -f`
2. Status: `docker-compose -f docker-compose.demo.yml ps`
3. Health: `curl http://localhost:8080/health`

**Questions?**
- Zobacz [demo/README.md](demo/README.md) - szczegółowa dokumentacja
- Zobacz [demo/QUICK_REFERENCE.md](demo/QUICK_REFERENCE.md) - quick reference

## 📝 Key Features Demonstrated

✅ **Extended Retention Management**
- Przedłużanie retencji dla pojedynczych plików
- Wielokrotne przedłużenia bez re-copy
- Historia zmian retencji

✅ **WORM Compliance**
- S3 Object Lock (Governance mode)
- Immutable shards
- Tombstone-based deletion

✅ **Cost Optimization**
- Copy-on-first-extension
- Subsequent updates: metadata only
- Separate lifecycle policies

✅ **User-Friendly Web UI**
- Upload plików
- Zarządzanie retencją
- Dashboard ze statystykami
- Historia zmian

---

**DES Extended Retention Demo** v1.0  
Ready to run • Complete • Production-quality setup

Enjoy! 🚀
