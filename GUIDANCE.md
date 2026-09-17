# StudyFlow — çalıştırma rehberi

**Proje kökü:** `backend/`, `frontend/`, `package.json` ve `scripts/` içeren klasör (ör. `studyflow`).

PowerShell’de sık kullanıyorsanız:

```powershell
$PROJ = "C:\Users\...\Masaüstü\studyflow"   # kendi yolunuz
```

Klasörünüz hâlâ `studyflow1` ise: Cursor ve bu klasörde açık tüm terminalleri kapatın, Dosya Gezgini’nde `Masaüstü\studyflow1` → `studyflow` olarak yeniden adlandırın; sonra projeyi yeni yoldan açın.

---

## Backend (FastAPI)

**İlk kurulum:**

```powershell
cd $PROJ\backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

`backend\.env.example` dosyasını `backend\.env` olarak kopyalayıp değerleri doldurun.

MySQL tabloları: varsayılan olarak `DB_AUTO_CREATE_TABLES=true` iken uygulama açılışında oluşturulur. Elle kurmak veya referans için `backend\sql\studyflow_schema.sql` dosyasına bakın (veritabanı adı `studyflow`).

**Her çalıştırmada:**

```powershell
cd $PROJ\backend
.\venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

- API: http://127.0.0.1:8000  
- Sağlık: http://127.0.0.1:8000/health  

---

## Frontend (Angular)

Uygulama doğrudan **`frontend/`** altında.

**İlk kurulum:**

```powershell
cd $PROJ\frontend
npm install
```

**Her çalıştırmada** (kök veya `frontend`):

```powershell
cd $PROJ
npm start
```

`frontend` içinden: `npm start` (aynı script, `scripts/run-ng.js` + doğru `cwd` + Node OpenSSL ayarı).

- Uygulama: http://localhost:4200  
- `environment.ts` içindeki API tabanı: **http://127.0.0.1:8000** (backend ile aynı host; CORS köklerde `localhost` ve `127.0.0.1` ayrı sayılır, backend ikisini de kabul eder).

Kaynak haritalı (daha yavaş) derleme: `cd frontend` → `npm run serve:maps`.

---

## İkisi birden

1. Terminal 1 — backend (`uvicorn` yukarıdaki gibi).  
2. Terminal 2 — frontend (`npm start` proje kökünden veya `frontend` içinden).

---

## Sorun giderme

- **`uvicorn` bulunamadı** — önce `venv` aktif edin veya `python -m uvicorn ...` kullanın.  
- **Angular / webpack hatası** — `npm start` kullanın.  
- **OpenSSL / Node** — `npm start` gerekli bayrakları `run-ng.js` üzerinden ayarlar.  
- **VS Code terminal** — `.vscode/settings.json` içinde `NODE_OPTIONS` tanımlı olabilir; yine de `npm start` tercih edin.
