# Data Easy Store (DES) – README

## 🚀 Nowa generacja systemu archiwizacji małych plików

**Data Easy Store (DES)** to ultra-skalowalny system do przechowywania *praktycznie nieograniczonej liczby małych plików* poprzez ich kompresję do dużych, sekwencyjnych shardów w obiektowym storage (S3/CEPH).

Ten projekt jest odświeżoną i uproszczoną wersją poprzedniego DES – pozbawioną wewnętrznej bazy danych, statusów i zbędnych metadanych. Całość działa *wyłącznie* na czystym, deterministycznym algorytmie.

---

# 🔥 Najważniejsze cechy

* **Zero bazy danych** po stronie DES
* **Zero statusów per plik** w systemie nadrzędnym
* **Czyste Algorytmiczne Shardowanie**: `shard = f(UID)`
* **DataCutoff** – tylko jedna wartość sterująca w DB
* **Skalowalność pozioma** → dowolna liczba packerów i retrieverów
* **Brak mapowania plik → shard** – lokalizacja wyliczana z samego UID
* **Cold storage przy pełnej szybkości odczytu** (range-GET + indeks w shardach)

System zaprojektowany dla skali **milionów plików dziennie** i **miliardów plików historycznych**.

---

# 🧩 Architektura w skrócie

DES składa się z trzech głównych komponentów:

## 1. **Packer**

Proces zbierający stare pliki i zapisujący je do shardów:

* wybiera pliki wg `created_at <= ARCHIVE_TARGET_DATE`,
* grupuje według `(data, shard_hex)`,
* tworzy plik `YYYYMMDD/SHARDHEX.des`,
* zapisuje wewnątrz pliki pod kluczem `UID`,
* wrzuca shard do S3.

## 2. **Retriever**

Zwrotny dostęp do pojedynczego pliku:

* przyjmuje `(UID, created_at)`,
* liczy katalog i shard, otwiera shard,
* zwraca plik `UID` z indeksu DES.

## 3. **Router**

Warstwa, która:

* odbiera zapytanie o plik,
* liczy shard z UID,
* kieruje zapytanie do właściwego retrievera.

---

# 🔧 Algorytm wyznaczania shardu

Wejście: `(UID, created_at)`

### 1. Katalog dzienny

```
YYYYMMDD = format(created_at)
```

### 2. Shardowanie po UID (8–12 bitów)

```
shard_index = f(UID)
shard_hex = hex(shard_index).zfill(2)
```

Zalecana funkcja hashująca:

* UID liczbowy → `UID % 256`
* UID tekstowy → `CRC32(UID) & 0xFF`

### 3. Finalny klucz shardu

```
S3 key = "YYYYMMDD/SHARDHEX.des"
```

Wewnątrz sharda plik jest trzymany **pod swoją nazwą `UID`**.

---

# 🧠 ARCHIVE_CUTOFF_DATE – jedyny stan systemu

System nadrzędny utrzymuje tylko jedną wartość:

```
ARCHIVE_CUTOFF_DATE
```

Jeśli `created_at > cutoff` → plik czytany z oryginału.
Jeśli `created_at <= cutoff` → próba odczytu z DES.

Brak statusów, brak markerów, brak update’ów per plik.

---

# 📦 Format DES

Shard jest plikiem zawierającym:

1. **Header**
2. **Data section** (ciąg binarny danych)
3. **Metadata section**
4. **Index UID → offset**
5. **Footer**

Shard jest *append-only*.

---

# 🛠️ Uruchamianie

W przygotowaniu – wkrótce staną się dostępne:

* obrazy Docker dla packera, retrievera i routera,
* domyślna konfiguracja K8s,
* przykładowe joby cronowe do obsługi cutoff.

---

# 📚 Zastosowania

DES nadaje się idealnie do:

### • Archiwizacji setek milionów małych plików

pliki logów, dokumentów, mini-jsonów, metadanych, załączników.

### • Data Lake dla ML / AI

obrazy, maski, próbki tekstowe, embeddingi – (UID, created_at) + deterministyczny dostęp.

### • Systemów IoT

zimne przechowywanie bilionów odczytów z sensorów.

### • Cold Storage dla obiektowego S3

znaczna redukcja liczby obiektów → lepsza wydajność i niższe koszty.

---

# 🗺️ Roadmap

* [ ] Implementacja pakera
* [ ] Implementacja retrievera
* [ ] Implementacja routera
* [ ] End-to-end testy integracyjne
* [ ] Wersja K8s
* [ ] Caching shardów
* [ ] API REST/GraphQL
* [ ] Auto-retry & failover retrieverów

---

# 🤝 Kontrybucje

Projekt przyjmuje kontrybucje: PR, dyskusje architektoniczne, testy i poprawki.
Wkrótce powstanie pełny CONTRIBUTING.md.

---

# 📄 Licencja

Do ustalenia.

---

**Ten projekt jest nową, uproszczoną generacją DES – całkowicie algorytmiczny, ultra-skalowalny i gotowy na Big Scale.**
