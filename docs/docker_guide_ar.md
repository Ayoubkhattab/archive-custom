# دليل Docker لتشغيل Paperless-ngx (محليًا Dev + Production)

هذا الدليل مخصص لشخص مبتدئ في Docker ويركّز على تشغيل المشروع **محليًا** للتطوير (Back-end على `8000` + Front-end على `4200` مع Hot Reload + قاعدة بيانات PostgreSQL)، ثم يشرح طريقة تشغيل **Production**.

> ملاحظة: أوامر هذا الدليل مكتوبة لتعمل على Windows (PowerShell). إذا كنت على Linux/macOS فالأوامر نفسها غالبًا تعمل كما هي.

---

## 1) مفاهيم أساسية بسرعة

- **Image**: قالب جاهز لتشغيل برنامج.
- **Container**: نسخة تشغيل من الـ image.
- **Volume**: مساحة تخزين يديرها Docker لحفظ البيانات (مثل قاعدة البيانات) حتى لو حذفت الـ container.
- **docker compose**: ملف يعرّف مجموعة خدمات (Postgres + Redis + Webserver + Frontend…)

---

## 2) متطلبات التشغيل

- Docker Desktop مثبت ويعمل.
- `docker compose` متاح (يأتي مع Docker Desktop غالبًا).

للتحقق:

```powershell
docker --version
docker compose version
```

---

## 3) أين ملفات Docker في المشروع؟

داخل هذا الريبو ستجد أهم الملفات هنا:

- `docker/compose/`
  - ملفات تشغيل جاهزة: `docker-compose.*.yml`
  - ملف متغيرات مثال: `docker-compose.env`

أهم ملفين بالنسبة لك:

- **تشغيل تطوير (Dev)**: `docker/compose/docker-compose.dev.yml`
  - يشغّل:
    - Backend + Celery + Celery Beat
    - Postgres
    - Redis
    - Frontend Angular على `4200` مع hot reload
    - Tika + Gotenberg

- **تشغيل Production (أمثلة)**:
  - `docker/compose/docker-compose.postgres.yml`
  - `docker/compose/docker-compose.postgres-tika.yml`
  - أو ملفات sqlite/mariadb حسب اختيارك

---

## 4) تشغيل Dev محليًا (Postgres + 8000 + 4200 Hot Reload)

### 4.1 ابدأ من المجلد الصحيح

نفّذ الأوامر من داخل:

`<project>\docker\compose`

في مشروعك الحالي مثلًا:

`e:\Projects\paperless-ngx\docker\compose`

### 4.2 تشغيل كل الخدمات

```powershell
docker compose -f docker-compose.dev.yml up -d
```

### 4.3 تحقق من الحالة

```powershell
docker compose -f docker-compose.dev.yml ps
```

يُفترض أن ترى خدمات مثل:

- `webserver` على `0.0.0.0:8000->8000`
- `frontend` على `0.0.0.0:4200->4200`
- `postgres` healthy
- `broker` (redis) healthy

### 4.4 الوصول للتطبيق

- Backend/API:
  - `http://localhost:8000`
- Frontend Dev Server:
  - `http://localhost:4200`

### 4.5 مشاهدة الـ logs

Backend:

```powershell
docker compose -f docker-compose.dev.yml logs -f --tail 200 webserver
```

Frontend:

```powershell
docker compose -f docker-compose.dev.yml logs -f --tail 200 frontend
```

> ملاحظة: أول تشغيل للـ frontend قد يأخذ وقتًا لأن `pnpm install` كبير.

---

## 5) تعديل الكود أثناء التشغيل (Hot Reload)

### 5.1 عند تعديل Back-end (Django)

في وضع dev يتم عمل mount لـ `src/` داخل الحاوية، وبالتالي تغييراتك عادة تظهر مباشرة.

إذا لم تظهر التغييرات أو أردت إعادة تشغيل الخدمة فقط:

```powershell
docker compose -f docker-compose.dev.yml restart webserver
```

مشاهدة اللوج بعد إعادة التشغيل:

```powershell
docker compose -f docker-compose.dev.yml logs -f --tail 200 webserver
```

### 5.2 عند تعديل Front-end (Angular)

التغييرات في `src-ui/` تُتابع عبر `ng serve`.

إذا توقّف الـ frontend أو حصل خطأ، أعد تشغيله:

```powershell
docker compose -f docker-compose.dev.yml restart frontend
```

ثم راقب اللوج:

```powershell
docker compose -f docker-compose.dev.yml logs -f --tail 200 frontend
```

---

## 6) التحقق من الإعدادات (للمبتدئين)

### 6.1 اعرض إعدادات compose النهائية (بعد الدمج)

هذا يعرض config النهائي بعد تطبيق كل المتغيرات:

```powershell
docker compose -f docker-compose.dev.yml config
```

### 6.2 تأكد أن المنافذ محجوزة أم لا

إذا لم تعمل `8000` أو `4200` قد يكون هناك برنامج آخر يستخدم نفس المنفذ.

على Windows:

```powershell
netstat -ano | findstr :8000
netstat -ano | findstr :4200
```

---

## 7) إعادة بناء (Rebuild) عند تغييرات Docker أو مشاكل Dependencies

### 7.1 إعادة بناء Dev images بدون حذف البيانات

```powershell
docker compose -f docker-compose.dev.yml down --remove-orphans

docker compose -f docker-compose.dev.yml build --no-cache

docker compose -f docker-compose.dev.yml up -d
```

### 7.2 تنظيف كامل (يمسح قاعدة البيانات أيضًا)

> استخدمه فقط إذا لا تحتاج بيانات Postgres.

```powershell
docker compose -f docker-compose.dev.yml down -v --remove-orphans

docker builder prune -f

docker compose -f docker-compose.dev.yml build --no-cache

docker compose -f docker-compose.dev.yml up -d
```

---

## 8) تشغيل Production (مفاهيم وخطوات عملية)

### 8.1 ما الفرق بين Dev و Prod؟

- **Dev**:
  - Frontend يعمل بـ `ng serve` على `4200` (hot reload)
  - مناسب للتطوير

- **Prod**:
  - عادة لا تحتاج `4200`
  - يتم تشغيل webserver لتقديم الـ UI والـ API معًا على `8000`
  - تركز على الاستقرار

### 8.2 اختيار ملف compose للـ Production

أفضل خيار شائع:
- `docker-compose.postgres-tika.yml` (Postgres + Tika + Gotenberg)

أو بدون Tika:
- `docker-compose.postgres.yml`

### 8.3 تجهيز ملفات البيئة

في مسار تشغيل production (غالبًا تنسخ ملفات compose إلى مجلد مستقل خارج الريبو)، تحتاج عادة:

- `docker-compose.yml` (تنسخ واحد من القوالب)
- `docker-compose.env`
- `.env`

ثم تعدّل `docker-compose.env` حسب إعداداتك (مثل `PAPERLESS_URL`, `PAPERLESS_SECRET_KEY`, `PAPERLESS_TIME_ZONE` …)

### 8.4 تشغيل Production

مثال (من داخل `docker/compose` في هذا الريبو):

```powershell
docker compose -f docker-compose.postgres-tika.yml --env-file docker-compose.env up -d
```

> في الإنتاج الحقيقي عادة ستنسخ الملفات لمجلد مستقل وتعيد تسميتها إلى `docker-compose.yml` ثم تشغل `docker compose up -d`.

### 8.5 تحديث نسخة Production

```powershell
docker compose pull

docker compose up -d
```

---

## 9) تشخيص الأعطال (Troubleshooting سريع)

### 9.1 أهم 3 أوامر

```powershell
docker compose -f docker-compose.dev.yml ps

docker compose -f docker-compose.dev.yml logs --tail 200 webserver

docker compose -f docker-compose.dev.yml logs --tail 200 frontend
```

### 9.2 قاعدة ذهبية

- إذا `postgres` أو `broker` غير healthy: الـ backend لن يعمل.
- إذا `webserver` لا يكمل migrations: راقب `webserver logs`.
- إذا `frontend` توقف أثناء `pnpm install` أو `ng serve`: راقب `frontend logs`.

### 9.3 حذف حاوية/خدمة واحدة وإعادتها

```powershell
docker compose -f docker-compose.dev.yml up -d --force-recreate webserver
```

أو للـ frontend:

```powershell
docker compose -f docker-compose.dev.yml up -d --force-recreate frontend
```

---

## 10) أسئلة شائعة

### هل لازم Postgres؟

لا، لكن Postgres هو الأفضل والأكثر استقرارًا مع تعدد المهام.

### لماذا يوجد `celery` و `celery-beat`؟

- `celery`: ينفذ المهام (استهلاك الملفات، OCR، …)
- `celery-beat`: يشغّل مهام مجدولة (تنظيف، تحسين index، …)

---

## 11) ملاحظات مهمة للأمان

- لا تشغّل إعدادات Dev على سيرفر عام.
- في Production اضبط `PAPERLESS_SECRET_KEY` و `PAPERLESS_URL` بشكل صحيح.

---

## 12) بناء نسخة Production محليًا (بدون Nginx/Proxy)

> ملاحظة: هذه الطريقة **تُشغّل Production محليًا** على نفس الجهاز (بدون Nginx أو reverse proxy).  
> إذا كنت تحتاج **Nginx/Proxy** للـ Production، انظر قسم “Production مع Nginx”.

### 12.1 متطلبات

- ملفات:
  - `docker/compose/docker-compose.postgres-tika.yml` (أو `docker-compose.postgres.yml` بدون Tika)
  - `docker/compose/docker-compose.env`
  - `.env`

- مجلدات:
  - `docker/compose/data/`
  - `docker/compose/media/`
  - `docker/compose/consume/`
  - `docker/compose/export/`

### 12.2 خطوات التشغيل

#### 12.2.1 إنشاء مجلد العمل والنسخ ملفات التشغيل

```powershell
# 1) إنشاء مجلد العمل (مرة واحدة)
mkdir -p e:\Projects\paperless-ngx\docker-prod

# 2) نسخ ملفات التشغيل إلى مجلد العمل
copy e:\Projects\paperless-ngx\docker\compose\docker-compose.postgres-tika.yml e:\Projects\paperless-ngx\docker-prod\
copy e:\Projects\paperless-ngx\docker\compose\docker-compose.env e:\Projects\paperless-ngx\docker-prod\
copy e:\Projects\paperless-ngx\docker\compose\.env e:\Projects\paperless-ngx\docker-prod\
```

#### 12.2.2 إنشاء مجلدات البيانات (مرة واحدة فقط)

```powershell
mkdir -p e:\Projects\paperless-ngx\docker-prod\data
mkdir -p e:\Projects\paperless-ngx\docker-prod\media
mkdir -p e:\Projects\paperless-ngx\docker-prod\consume
mkdir -p e:\Projects\paperless-ngx\docker-prod\export
```

> ملاحظة: لا تنسخ هذه المجلدات إذا كانت موجودة مسبقًا لتجنب حذف بيانات قديمة.

#### 12.2.3 تشغيل Production

```powershell
cd e:\Projects\paperless-ngx\docker-prod
docker compose -f docker-compose.postgres-tika.yml --env-file docker-compose.env up -d
```

#### 12.2.4 التحقق من الحالة

```powershell
docker compose -f docker-compose.postgres-tika.yml ps
docker compose -f docker-compose.postgres-tika.yml logs -f --tail 200 webserver
```

#### 12.2.5 الوصول للتطبيق

- Backend/API: `http://localhost:8000`
- Frontend (Production): `http://localhost:8000` (يخدم الـ UI والـ API معًا)

> ملاحظة: في هذه الطريقة، **الـ Frontend لا تعمل على 4200**، بل تُخدم من `8000` معًا.

---

## 13) Production مع Nginx/Proxy (إضافي)

إذا أردت تشغيل Production خلف Nginx أو Reverse Proxy (مثل Traefik)، أضف قسم “Production مع Nginx” في النهاية.

---

## 14) التبديل بين Dev و Production

| الخاصية | Dev | Production |
|---|---|---|
| **الغرض** | تطوير محلي (Backend + Frontend منفصلين) | تشغيل مستقر (Backend + Frontend معًا) |
| **الـ Frontend** | Angular Dev Server على `4200` مع Hot Reload | Django يخدم الـ UI من `8000` |
| **الـ Backend** | Django Development Server مع Hot Reload | Django يخدم الـ API والـ UI من `8000` |
| **قاعدة بيانات** | Postgres (حجم صغير) | Postgres (حجم كبير) |
| **الـ Logs** | مفصلة ومفصلة | مفصلة وموجهة للإنتاج |
| **الـ Hot Reload** | نعم (تغييرات الكود تظهر فورًا) | لا (لا يوجد Hot Reload) |
| **الـ Ports** | `8000` (Backend) + `4200` (Frontend) | `8000` (Backend + Frontend معًا) |

---

## 15) أسئلة شائعة

### س: كيف أوقف كل الحاويات؟

```powershell
docker compose -f docker-compose.dev.yml down
```

### س: كيف أعد بناء الـ images فقط؟

```powershell
docker compose -f docker-compose.dev.yml build --no-cache
```

### س: كيف أحذف كل الـ images القديمة؟

```powershell
docker rmi paperless-webserver:latest paperless-celery:latest paperless-celery-beat:latest
```

### س: كيف أحذف volumes القديمة؟

```powershell
docker volume prune -f
```

---

## 16) ملاحظات هامة

- **لا تخلط بين `docker compose -f docker-compose.dev.yml`** (Dev) و **`docker compose -f docker-compose.postgres-tika.yml`** (Prod).  
- **استخدم دليل منفصل للـ Production** (`docker-prod/`) لتجنب تداخل البيانات مع Dev.

---

## 16.1 اختبار التشغيل (Validation)

قبل تشغيل Production لأول مرة، قم بالتحقق:

```powershell
# 1) تأكد من المنافذ
netstat -ano | findstr :8000
netstat -ano | findstr :4200

# 2) تأكد من الحاويات
docker compose -f docker-compose.postgres-tika.yml ps

# 3) جرب الوصول
curl http://localhost:8000/api/
curl http://localhost:8000/admin/
```

إذا عمل كل شيء، تابع بالخطوة التالية.

---

## 16.2 خطوات تشغيل Production (بدون Nginx)

```powershell
# 1) انتقل إلى مجلد Production
cd e:\Projects\paperless-ngx\docker-prod

# 2) تشغيل (أول مرة)
docker compose -f docker-compose.postgres-tika.yml --env-file docker-compose.env up -d

# 3) التحقق
docker compose -f docker-compose.postgres-tika.yml ps
docker compose -f docker-compose.postgres-tika.yml logs -f --tail 200 webserver
```

---

## 16.3 خطوات تشغيل Production مع Nginx/Proxy

إذا أردت تشغيل Production خلف Nginx/Proxy، استخدم قسم “Production مع Nginx/Proxy”.

---

## 16.4 التبديل بين Dev و Production

| الخاصية | Dev | Production |
|---|---|---|
| **الغرض** | تطوير محلي (Backend + Frontend منفصلين) | تشغيل مستقر (Backend + Frontend معًا) |
| **الـ Frontend** | Angular Dev Server على `4200` مع Hot Reload | Django يخدم الـ UI والـ API من `8000` |
| **الـ Backend** | Django Development Server مع Hot Reload | Django يخدم الـ API والـ UI من `8000` |
| **قاعدة بيانات** | Postgres (حجم صغير، سريع) | Postgres (حجم كبير، استقرار) |
| **الـ Ports** | `8000` + `4200` | `8000` فقط |
| **الـ Logs** | مفصلة وموجهة للإنتاج | مفصلة وموجهة للإنتاج |
| **الـ Hot Reload** | نعم (تغييرات الكود تظهر فورًا) | لا (لا يوجد Hot Reload) |
| **الـ Data** | volumes Docker (تُحفظ بين إعادة التشغيل) | volumes Docker (تُحفظ بين إعادة التشغيل) |

---

## 16.5 التحويل من Dev إلى Production

```powershell
# 1) نسخة احتياطية من قاعدة بيانات Dev
docker compose -f docker-compose.dev.yml down -v

# 2) نسخها إلى مجلد Production
docker compose -f docker-compose.dev.yml down -v
docker run --rm -v paperless-postgres_data:/backup postgres:16 -c "COPY (SELECT * FROM public.documents)" TO /tmp/dump.sql' postgres:16
docker run --rm -v paperless-postgres_data:/backup -c "COPY (SELECT * FROM public.auth_user)" TO /tmp/users.sql' postgres:16
docker run --rm -v paperless-postgres_data:/backup -c "COPY (SELECT * FROM public.documents_document_tags)" TO /tmp/tags.sql' postgres:16
docker run --rm -v paperless-postgres_data:/backup -c "COPY (SELECT * FROM public.documents_savedviewfilterrule)" TO /tmp/filters.sql' postgres:16
docker run --rm -v paperless-postgres_data:/backup -c "COPY (SELECT * FROM public.documents_correspondent)" TO /tmp/correspondents.sql' postgres:16
docker run --rm -v paperless-postgres_data:/backup -c "COPY (SELECT * FROM public.documents_documenttype)" TO /tmp/types.sql' postgres:16

# 3) إعادة تشغيل Production مع البيانات المنسوخة
docker compose -f docker-compose.postgres-tika.yml --env-file docker-compose.env up -d
```

---

## 17) أوامر الصيانة والتعديل

### 17.1 إعادة بناء كامل (Clean Rebuild)

```powershell
# من مجلد Dev
cd e:\Projects\paperless-ngx\docker\compose
docker compose -f docker-compose.dev.yml down -v --remove-orphans
docker builder prune -f
docker compose -f docker-compose.dev.yml build --no-cache
docker compose -f docker-compose.dev.yml up -d

# من مجلد Production
cd e:\Projects\paperless-ngx\docker-prod
docker compose -f docker-compose.postgres-tika.yml down -v --remove-orphans
docker compose -f docker-compose.postgres-tika.yml build --no-cache
docker compose -f docker-compose.postgres-tika.yml up -d
```

### 17.2 إعادة بناء الصور فقط

```powershell
# من مجلد Dev
cd e:\Projects\paperless-ngx\docker\compose
docker compose -f docker-compose.dev.yml build --no-cache

# من مجلد Production
cd e:\Projects\paperless-ngx\docker-prod
docker compose -f docker-compose.postgres-tika.yml build --no-cache
```

### 17.3 تنظيف Docker كامل

```powershell
docker system prune -af
docker volume prune -f
docker network prune -f
```

---

## 18) أوامر سريعة (Quick Commands)

```powershell
# إعادة تشغيل Dev
docker compose -f docker-compose.dev.yml up -d

# إعادة تشغيل Production
docker compose -f docker-compose.postgres-tika.yml up -d

# عرض حالة Dev
docker compose -f docker-compose.dev.yml ps

# عرض حالة Production
docker compose -f docker-compose.postgres-tika.yml ps

# عرض logs Backend (Dev)
docker compose -f docker-compose.dev.yml logs -f --tail 200 webserver

# عرض logs Frontend (Dev)
docker compose -f docker-compose.dev.yml logs -f --tail 200 frontend

# عرض logs Backend (Prod)
docker compose -f docker-compose.postgres-tika.yml logs -f --tail 200 webserver

# إعادة تشغيل خدمة واحدة
docker compose -f docker-compose.dev.yml restart webserver
docker compose -f docker-compose.dev.yml restart frontend

# إيقاف كل الخدمات
docker compose -f docker-compose.dev.yml down

# حذف volumes (تنظيف كامل)
docker compose -f docker-compose.dev.yml down -v --remove-orphans
docker volume prune -f
```

---

## 19) خلاصات وأفضل الممارسات

- **دوامًا استخدم `docker compose down -v`** عند تغييرات مهمة (مثل تغيير قاعدة البيانات).
- **احتفظ بنسخ احتياطية** قبل أي تغييرات كبيرة.
- **استخدم volumes منفصلة** للبيانات الحساسة (مثل `postgres_data`).
- **راقب الـ logs بانتظام**، استخدم `docker compose logs -f`.
- **للتطوير، استخدم `docker compose.dev.yml`**.
- **للإنتاج، استخدم `docker-compose.postgres-tika.yml`**.
- **لا تشغّل Production على نفس المنافذ مثل Dev** لتجنب التعارض.
- **استخدم `PAPERLESS_SECRET_KEY` قوية وفريدة في Production**.
- **احتفظ بنسخة احتياطية من قاعدة البيانات** قبل التحديثات الكبيرة.
