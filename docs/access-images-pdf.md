# الصلاحيات الهرمية، رفع الصور الآمن، وعارض PDF

يوثّق هذا الملف ثلاث إضافات على المشروع (Django + Angular) دون تغيير بنية الموقع:

1. نظام صلاحيات هرمي (RBAC + ReBAC) مع صلاحية عامة `system.manage.all`.
2. نظام رفع صور آمن (إعادة ترميز، إزالة EXIF/GPS، نسخ متجاوبة، مهام خلفية).
3. إصلاح عارض PDF الذي كان يبقى على «جاري التحميل…».

---

## 1. البنية والافتراضات

البنية الحالية `src/` (Django) و`src-ui/` (Angular) **بقيت كما هي** بناءً على طلبك؛
الوحدات المطلوبة وُضعت داخل التطبيقات الموجودة:

| المطلوب | مكانه في المشروع |
|---|---|
| `permissions/constants.py` | `src/paperless/access/constants.py` |
| `permissions/policy.py` — `can()` | `src/paperless/access/policy.py` |
| `permissions/classes.py` — `HasPermission`, `HasRelationship`, `IsGlobalAdmin` | `src/paperless/access/drf.py` |
| `permissions/mixins.py` — `PermissionScopedMixin` | `src/paperless/access/drf.py` |
| `permissions/services.py` / users service | `src/paperless/access/service.py` |
| `audit/` (models, services, views) | `SecurityAuditLog` في `src/paperless/models.py`، `access/audit.py`، `access/views.py` |
| `common/exceptions.py` | `src/paperless/access/exceptions.py` |
| `images/` (storage, processing, tasks, views…) | `src/paperless/images/` |
| `pdfs/` (views, services) | `src/paperless/pdfs/` |
| Range/ETag streaming | `src/paperless/streaming.py` |
| Angular `PermissionsService` | `src-ui/src/app/services/permissions.service.ts` |
| Angular `PdfViewerComponent` | `src-ui/src/app/components/common/pdf-viewer/` |
| Angular `ImageUploaderComponent` | `src-ui/src/app/components/common/image-uploader/` |
| صفحة الصور (lazy route `/images`) | `src-ui/src/app/components/images/` |

**نماذج البيانات المطلوبة وما يقابلها** (لم نُنشئ نظام مصادقة موازياً لنظام Django):

| المطلوب | المستخدم |
|---|---|
| `User` | `django.contrib.auth.models.User` |
| `Role` / `UserRole` | `Group` / `User.groups` |
| `Permission` / `RolePermission` | `Permission` / `Group.permissions` (+ `AccessPermission` لحمل الصلاحيات الجديدة) |
| `UserRelationship` (الأب/الابن) | `UserOwnership.created_by` (موجود سابقاً: من أنشأ الحساب هو «الأب») |
| `AuditLog` | `SecurityAuditLog` (append-only) |
| `Image`, `ImageVariant`, `JobRecord` | نماذج جديدة في `paperless/models.py` (الهجرة `0007`) |

**افتراضات وانحرافات مقصودة:**

- **أخطاء التحقق 422** تُطبَّق على الواجهات الجديدة (`/api/images/`, `/api/pdfs/`, `/api/audit_logs/`).
  الواجهات القديمة تبقى على 400 لأن تغييرها يكسر نماذج Angular الحالية ومئات الاختبارات.
- **حسابات superuser محمية**: حامل `system.manage.all` يعدّل كل شيء *عدا* حسابات Django superuser
  (وإلا لاستطاع إعادة تعيين كلمة مرور الجذر والاستيلاء عليه) وعدا سجلات التدقيق.
- **لا أحد يرفع صلاحياته بنفسه**: تغيير `is_staff/is_superuser/groups/user_permissions/parent`
  على الحساب الشخصي مرفوض حتى لحامل `system.manage.all` (المستثنى فقط superuser).
- **قراءة الأب**: الابن الذي يملك `user.read.any` يستطيع *رؤية* حساب أبيه (ليختاره في نوافذ المشاركة)،
  لكنه لا يستطيع أي تعديل مهما كانت صلاحياته.
- **Cache-Control للصور**: `private, max-age=31536000, immutable` بدلاً من `public`، لأن الصور تُخدم عبر
  واجهة مصادقة؛ `public` كان سيسمح لـ CDN مشترك بتقديم صورة خاصة لشخص آخر. الرابط يحوي `?v=<checksum>`
  فهو content-addressed ويصح تخزينه للأبد.
- **SVG مرفوض دائماً**: لا يوجد sanitizer موثوق في هذا النشر.
- **S3**: `S3StorageProvider` يستخدم `boto3` مباشرة (المكتبة التي يغلّفها django-storages) لدعم القراءة
  الجزئية Range دون تنزيل الملف كاملاً. ثبّت `boto3` لتفعيله.
- **401 في عارض PDF**: المشروع يستخدم جلسات (cookies) وليس refresh tokens، لذلك يُعاد الطلب مرة واحدة
  ثم تظهر رسالة «انتهت الجلسة» مع زر تسجيل الدخول.

---

## 2. محرّك السياسات

```python
from paperless.access.policy import can, Resource
from paperless.access.constants import Action

can(actor=request.user, action=Action.UPDATE, resource=Resource.user(target))
can(actor=request.user, action=Action.UPLOAD, resource=Resource.image(owner_id=owner.pk))
can(actor=request.user, action=Action.VIEW,   resource=Resource.pdf(document))
```

ترتيب القرار (`decide()` يعيد أيضاً سبب القرار والقاعدة):

0. غير مسجّل / حساب معطّل ← رفض. ثم حمايات لا تُرفع بأي صلاحية:
   رفع صلاحيات النفس، وتعديل حساب superuser من غير superuser.
1. `system.manage.all` (أو superuser) ← سماح.
2. نطاق الذات `*.self` ← سماح.
3. ابن مباشر `*.child` ← سماح.
4. **الابن يعدّل أحد أسلافه (الأب، الجد…) ← رفض فوري** حتى مع `*.any`.
   هذا الفحص يعتمد على العلاقة فقط، مستقلاً عن الأدوار.
5. غير مرتبط (أو حفيد) يحتاج `*.any`.
6. رفض افتراضي.

الصلاحيات الضمنية (يملكها كل حساب نشط دون منح):
`user.read.self, user.update.self, pdf.view.self, image.upload.self, image.delete.self`.
للوضع الصارم: `PAPERLESS_ACCESS_IMPLICIT_PERMISSIONS=""`.

الصلاحيات القديمة تبقى فعّالة: `auth.change_user` = `user.update.child`، `auth.add_user` = `user.create.child`،
`auth.delete_user` = `user.delete.child`، `auth.view_user` = `user.read.any`.

### مصفوفة الصلاحيات

| الفاعل ← الهدف | قراءة مستخدم | تعديل مستخدم | حذف مستخدم | تغيير الأب/الصلاحيات | عرض PDF | رفع صورة | حذف صورة |
|---|---|---|---|---|---|---|---|
| نفسه | `user.read.self` ✔ (ضمني) | `user.update.self` ✔ (حقول عادية فقط) | ✘ دائماً | ✘ دائماً | `pdf.view.self` ✔ | `image.upload.self` ✔ | `image.delete.self` ✔ |
| الأب ← ابنه المباشر | `user.read.child` | `user.update.child` | `user.delete.child` | الصلاحيات: `user.update.child` + لا يمنح ما لا يملك؛ الأب: `system.manage.all` | `pdf.view.child` | `image.upload.child` | `image.delete.child` |
| الأب ← حفيده | `user.read.any` | `user.update.any` | `user.delete.any` | كالسابق مع `.any` | `pdf.view.any` | `image.upload.any` | `image.delete.any` |
| **الابن ← أبوه/جده** | `user.read.any` | **✘ 403 دائماً** | **✘ 403 دائماً** | **✘ 403 دائماً** | مشاركة صريحة فقط | **✘ 403 دائماً** | **✘ (404/403)** |
| غير مرتبط | `user.read.any` | `user.update.any` | `user.delete.any` | `system.manage.all` للأب | `pdf.view.any` أو مشاركة | `image.upload.any` | `image.delete.any` |
| `system.manage.all` | ✔ | ✔ (عدا superuser) | ✔ (عدا superuser ونفسه) | ✔ (عدا نفسه؛ مع منع الحلقات) | ✔ | ✔ | ✔ |
| أي أحد ← سجل التدقيق | عرض: `system.manage.all` فقط | ✘ | ✘ | — | — | — | — |

المورد الذي لا يحق لك رؤيته يُعاد كـ **404** (لا تسريب لوجوده)؛ المورد المرئي الممنوع تعديله ← **403**.

### أين يُطبَّق

- `/api/users/` و `/api/groups/` و Django admin (`/admin/auth/user/`) كلها عبر نفس المحرّك.
  لوحة الإدارة كانت ثغرة: ابن موظف (staff) لديه `change_user` كان يستطيع تعديل أبيه منها.
- المجموعات: الابن لا يستطيع تعديل أو حذف مجموعة ينتمي إليها أحد أسلافه (لأن ذلك يغيّر صلاحيات الأب).
- إلغاء 2FA لحساب آخر: `system.manage.all` فقط، ولا يُسمح للابن على أبيه.
- `system.manage.all` تعمل فعلياً عبر `SystemManageAllBackend` (يجيب «نعم» على كل `has_perm` عدا
  صلاحيات سجلات التدقيق) ومن خلال `is_system_admin()` في فلاتر رؤية المستندات/المجلدات/البحث.

### سجل التدقيق

`SecurityAuditLog { actor_id, actor_username, action, target_type, target_id, outcome, before_json,
after_json, ip, user_agent, request_id, created_at }`. يُكتب من طبقة الخدمة (وليس من signals) داخل
نفس المعاملة، فإن فشل التدقيق تُلغى العملية. غير قابل للتعديل أو الحذف عبر الـ ORM (model + queryset)،
وعلى PostgreSQL عبر trigger في قاعدة البيانات (الهجرة `0008`). القراءة: `GET /api/audit_logs/`.
كلمات المرور والرموز تُحجب تلقائياً (`[redacted]`).

---

## 3. رفع الصور

| الطريقة | المسار | الصلاحية |
|---|---|---|
| `POST` (multipart) | `/api/images/` حقول: `file`، `owner` اختياري، `alt_text`، `idempotency_key` | `image.upload.*` + العلاقة |
| `GET` | `/api/images/` (مُرقّم) و `/api/images/{id}/` | `image.view.*` |
| `GET` | `/api/images/{id}/variants/{thumbnail\|small\|medium\|large}/` | `image.view.*` |
| `GET` | `/api/images/{id}/file/` (الأصل إن حُفظ، وإلا أكبر نسخة) | `image.view.*` |
| `DELETE` | `/api/images/{id}/` (حذف ناعم + تنظيف في الخلفية) | `image.delete.*` + العلاقة |

خط المعالجة:

1. حدود أثناء البث نفسه (`LimitedTemporaryFileUploadHandler`): الحجم (413) وعدد الملفات (422)،
   ويُقطع الطلب فور تجاوز الحد بدل تخزينه كاملاً.
2. نوع الملف من **magic bytes** وليس من ترويسة العميل؛ قائمة سماح؛ SVG/HTML مرفوض (415).
3. Pillow `verify()` + تطابق الصيغة الفعلية مع البايتات + حد البكسلات ضد decompression bombs (422).
4. ClamAV اختياري (`PAPERLESS_IMAGES_CLAMAV_ENABLED`)، يفشل مغلقاً (503) إن تعذّر الوصول.
5. التخزين باسم UUID عشوائي (`incoming/<uuid>`)، لا يُستخدم اسم العميل على القرص أبداً،
   ومفاتيح التخزين تُتحقق ضد path traversal.
6. Celery: فك الترميز مرة واحدة ← تطبيق اتجاه EXIF ← نسخ البكسلات إلى صورة جديدة (يحذف EXIF/GPS/XMP/ICC)
   ← نسخ 200/640/1280/2048 بكسل دون تكبير ← WebP أو AVIF. idempotency عبر `JobRecord` و`(image, kind)` فريد.
   إعادة المحاولة بـ exponential backoff + jitter، وحدود زمنية soft/hard.
7. الحذف: `deleted_at` فوراً، والملفات تُحذف في الخلفية؛ مهمة `sweep_images` كل 30 دقيقة كشبكة أمان.
   حذف المستخدم يحذف صوره أيضاً (لا ملفات يتيمة).
8. حد معدّل لكل مستخدم: `image_upload` 30/دقيقة، `image_delete` 60/دقيقة (Redis في الإنتاج).

### المتغيرات

| المتغير | الافتراضي |
|---|---|
| `PAPERLESS_IMAGES_ENABLED` | `yes` |
| `PAPERLESS_IMAGES_MAX_UPLOAD_BYTES` | `10485760` (10 MB) |
| `PAPERLESS_IMAGES_MAX_FILES_PER_REQUEST` | `1` |
| `PAPERLESS_IMAGES_ALLOWED_TYPES` | `image/jpeg,image/png,image/webp,image/avif,image/gif` |
| `PAPERLESS_IMAGES_MAX_PIXELS` | `50000000` |
| `PAPERLESS_IMAGES_OUTPUT_FORMAT` | `webp` (أو `avif`) |
| `PAPERLESS_IMAGES_OUTPUT_QUALITY` | `82` |
| `PAPERLESS_IMAGES_KEEP_ORIGINAL` | `no` |
| `PAPERLESS_IMAGES_STORAGE` | `local` (أو `s3`) |
| `PAPERLESS_IMAGES_STORAGE_DIR` | `<MEDIA_ROOT>/images` |
| `PAPERLESS_IMAGES_S3_BUCKET` / `_PREFIX` / `_ENDPOINT_URL` / `_REGION` | — (اعتماد AWS من البيئة القياسية) |
| `PAPERLESS_IMAGES_X_ACCEL_PREFIX` | فارغ (Django يبث الملف بنفسه) |
| `PAPERLESS_IMAGES_CLAMAV_ENABLED` / `_HOST` / `_PORT` / `_TIMEOUT` | `no` / `clamav` / `3310` / `30` |
| `PAPERLESS_IMAGE_UPLOAD_RATE_LIMIT` / `PAPERLESS_IMAGE_DELETE_RATE_LIMIT` | `30/min` / `60/min` |
| `PAPERLESS_IMAGES_SWEEP_TASK_CRON` | `*/30 * * * *` (أو `disable`) |
| `PAPERLESS_ACCESS_IMPLICIT_PERMISSIONS` | الصلاحيات الذاتية الخمس |

---

## 4. عارض PDF — سبب «جاري التحميل» الدائم والإصلاح

**الأسباب التي وُجدت في الكود:**

1. `onError()` في `document-detail` كان يعالج `PasswordException` فقط؛ أي خطأ آخر (403، 404، 500،
   انقطاع الشبكة، ملف تالف، فشل الـ worker) كان يترك `previewLoaded=false` فتبقى طبقة «جاري التحميل» للأبد.
2. فشل جلب الـ metadata كان يترك نفس الطبقة للأنواع غير PDF.
3. لا يوجد أي timeout: طلب معلّق = تحميل لا ينتهي.
4. `ng2-pdf-viewer` يستخدم نسخة pdf.js الحديثة بينما الـ worker من نسخة legacy؛ المتصفحات الأقدم ترمي خطأً
   لم يكن يُلتقط (نفس النتيجة: تحميل لا ينتهي).
5. الخادم كان يقرأ الملف كاملاً في الذاكرة، دون `Accept-Ranges`، و`CompressionMiddleware` يضغط ملفات PDF
   (وهي مضغوطة أصلاً) ويحذف `Content-Length`، ما يفسد أي استجابة 206.

**الإصلاح:**

- مكوّن `pngx-pdf-viewer` بحالات صريحة: `idle → loading → rendering → success | password | error`.
  - الجلب عبر `HttpClient` (جلسة المستخدم)، **timeout عدم نشاط 20 ثانية** (كل حدث تقدّم يعيد العدّاد)،
    ومؤقت حراسة 20 ثانية للرسم. لا يمكن البقاء في «جاري التحميل» دون مؤقت فعّال.
  - 401 يُعاد مرة واحدة؛ 403 «ليس لديك صلاحية لعرض هذا الملف»؛ 404؛ 416؛ 5xx؛ ملف فارغ؛ ملف تالف
    (فحص `%PDF-`)؛ ملف محمي بكلمة مرور (مع رسالة كلمة مرور خاطئة).
  - زر إعادة المحاولة، ورابط تنزيل كبديل.
  - عند الإغلاق: إلغاء الطلب، `PDFDocumentProxy.destroy()`، و`URL.revokeObjectURL`.
  - الرسم عبر `ng2-pdf-viewer`/`PDFViewer` الذي يحترم `devicePixelRatio`.
- `document-detail` يزيل طبقة التحميل عند **أي** حالة نهائية، ويعيدها عند فتح مستند آخر.
- الخادم: `GET /api/pdfs/{id}/file/` جديد، ودعم Range في `/api/documents/{id}/preview/` الحالي:
  `206` + `Content-Range` + `Accept-Ranges: bytes`، `416` مع `bytes */size`، `If-Range`، `ETag`/`304`،
  `Content-Type: application/pdf`، `Content-Disposition: inline`.
- `SafeCompressionMiddleware`: لا يضغط PDF/الصور/استجابات 206.

---

## 5. النشر

### Nginx

```nginx
gzip on;
gzip_types text/css application/javascript application/json image/svg+xml;
# brotli on;  brotli_types ... (with ngx_brotli)
http2 on;

location /static/ {
    alias /usr/src/paperless/static/;
    expires 1y;
    add_header Cache-Control "public, max-age=31536000, immutable";
}

# Only reachable through X-Accel-Redirect from Django (after authorization).
location /_protected_images/ {
    internal;
    alias /usr/src/paperless/media/images/;
    add_header Cache-Control "private, max-age=31536000, immutable";
}

location / {
    proxy_pass http://paperless:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Request-ID $request_id;
    proxy_read_timeout 60s;
    client_max_body_size 12m;   # a little above PAPERLESS_IMAGES_MAX_UPLOAD_BYTES
}
```

ثم `PAPERLESS_IMAGES_X_ACCEL_PREFIX=/_protected_images`. Nginx يخدم Range بنفسه في هذه الحالة.

### CSP / CORS / CSRF

- الـ worker يُنسخ من `node_modules/pdfjs-dist/legacy/build/` (نفس الإصدار 4.8.69 الذي يستخدمه
  ng2-pdf-viewer) إلى `assets/js/`، ويُحمَّل من نفس الأصل؛ الـ CSP الحالية لا تقيّد `worker-src`.
  إن أضفت `script-src` في الـ proxy فأضف `worker-src 'self' blob:`.
- الجلسات (cookies) تفرض CSRF على الطرق غير الآمنة (Angular يرسل `X-CSRFToken`)؛ الطلبات بالرمز
  تستخدم `Authorization: Token …`.
- `PAPERLESS_CORS_ALLOWED_HOSTS` لأصل Angular المنفصل إن وُجد.

### Celery

العامل الحالي يلتقط المهام تلقائياً (`paperless.images.tasks`)، و`Sweep images` مضافة لجدول beat.

### الهجرات والتراجع (rollback)

- `python3 manage.py migrate` يطبّق `paperless.0007` و `0008`.
- للتراجع عن الصور: `PAPERLESS_IMAGES_ENABLED=no` (الواجهة تختفي من القائمة إن أزلت الصلاحيات).
- للتراجع عن الهجرات: `python3 manage.py migrate paperless 0006` (يحذف الجداول الجديدة وسجل التدقيق).
- عارض PDF: إعداد «استخدام عارض PDF الأصلي للمتصفح» الموجود في الإعدادات يتجاوز المكوّن الجديد كلياً.

---

## 6. الأداء: ما تغيّر فعلاً وما لم يُقَس

**تغييرات منفّذة:**

- PDF: قراءة جزئية Range ← يبدأ pdf.js دون انتظار الملف كاملاً عند استخدام الرابط المباشر؛ إيقاف ضغط
  ملفات PDF/الصور (توفير CPU دون فقد شيء)؛ `ETag`/`304`.
- الصور: WebP/AVIF، `srcset`/`sizes`، `loading="lazy"`، `decoding="async"`، `width/height` صريحة
  (تمنع CLS)، `fetchpriority="high"` لأول الصور، تخزين `immutable` للنسخ، المعالجة الثقيلة في Celery.
- فهارس قاعدة البيانات: `image_owner_live_idx (owner, deleted_at, -created_at)`، `image_purge_idx`،
  `audit_target_idx`، `jobrecord_kind_status_idx`، وفهارس `created_at/actor_id/action` للتدقيق.
- `select_related("ownership")` في قائمة المستخدمين، و`Prefetch` للنسخ في قائمة الصور (لا N+1)، وترقيم
  للقوائم الجديدة.
- Angular: صفحة الصور lazy (`loadComponent`)، كل المكوّنات الجديدة `OnPush`، `@for … track`،
  `takeUntilDestroyed`، timeouts على كل طلبات الصور.

**لم يُقَس في هذه البيئة:** Lighthouse/LCP/INP/TTFB وp95 تتطلب تشغيل النظام كاملاً بقاعدة بيانات
وبيانات حقيقية ومتصفح على شبكة جوال؛ لذلك **لا توجد أرقام قبل/بعد** في هذا التقرير، ولن نخترعها.
الخطوة التالية المقترحة: تشغيل `lighthouse-ci` على `/dashboard` و`/documents/<id>` مع ميزانيات
(Performance ≥ 90، LCP < 2.5s، CLS < 0.1)، و`EXPLAIN ANALYZE` على استعلامات قائمة المستندات.

---

## 7. الاختبارات

```bash
cd src && pytest paperless/tests/test_access_policy.py paperless/tests/test_images.py paperless/tests/test_pdfs.py
```

```bash
cd src-ui && npx jest src/app/components/common/pdf-viewer src/app/components/common/image-uploader src/app/services/permissions.service.spec.ts
```

تغطي: الابن لا يعدّل/يحذف/يغيّر أب أبيه/يرفع صلاحياته؛ الأب يعدّل ابنه المباشر؛ المدير العام يعدّل الجميع؛
غير المرتبط 403؛ نقص الصلاحية 403؛ لوحة Django admin؛ عدم قابلية تعديل سجل التدقيق؛ أنواع الصور
المسموحة/المرفوضة، تزوير magic bytes، الحجم الزائد، path traversal، الابن لا يرفع/يحذف لأبيه، حد المعدّل،
التنظيف بعد الحذف، idempotency للنسخ؛ PDF: 401/403/404/416/206/304؛ واجهة: timeout يظهر خطأ،
401/403/404/500، التنظيف عند الإغلاق، حالات أخطاء الرفع، إخفاء الأزرار عند المنع.
