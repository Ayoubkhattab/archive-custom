# حزمة تثبيت الأرشيف على سيرفر محلي

هذه الحزمة تحتوي كل ما يحتاجه الأرشيف ليعمل على سيرفر **دون اتصال بالإنترنت**: صور Docker كاملة، وملف التشغيل، والإعدادات، وسكربت التثبيت. لا يُنزَّل أي شيء أثناء التثبيت أو التشغيل.

## محتويات الحزمة

| الملف | الوصف |
|---|---|
| `images/*.tar` | صور Docker الست (انظر الجدول أدناه) |
| `docker-compose.yml` | تعريف الخدمات، جاهز للتشغيل |
| `.env.example` | قالب الإعدادات (ينسخه `install.sh` إلى `.env`) |
| `install.sh` | التثبيت والتحديث |
| `SHA256SUMS` | للتحقق من سلامة الملفات بعد النقل |
| `IMAGES.txt` | أرقام الصور ورقم نسخة الكود التي بُنيت منها |

| الصورة | دورها |
|---|---|
| `paperless-ngx-custom` | التطبيق نفسه: الواجهة العربية، OCR العربي (نماذج Tesseract الدقيقة)، نماذج البحث الدلالي، الرفع المقسّم |
| `paperless-ollama:gemma4-e4b` | الذكاء الاصطناعي مع النموذج `gemma4:e4b` بداخله |
| `postgres:16` | قاعدة البيانات |
| `redis:8` | طابور المهام |
| `gotenberg` | تحويل ملفات Office والبريد إلى PDF |
| `tika` | استخراج النص من ملفات Office |

> صورة واحدة `paperless-ngx-custom` تشغّل ثلاث حاويات: `webserver` و`celery` (معالجة المستندات) و`celery-beat` (المهام المجدولة).

## متطلبات السيرفر

- **نظام:** Linux على معالج x86_64 (مثل Ubuntu 22.04 أو 24.04).
- **Docker Engine** مع إضافة **Docker Compose v2**. إن لم يكن السيرفر متصلاً بالإنترنت فثبّتهما من حزم `.deb` منزّلة مسبقاً (https://docs.docker.com/engine/install/ubuntu/#install-from-a-package).
- **الذاكرة:** 16 جيغا على الأقل (النموذج وحده نحو 10 جيغا)، و32 جيغا أفضل.
- **القرص:** نحو 45 جيغا للصور، إضافة إلى مساحة المستندات. الرفع الكبير يحتاج مؤقتاً ضعف حجم الملف.

## التثبيت

1. انسخ مجلد الحزمة كاملاً إلى السيرفر، مثلاً إلى `/opt/archive`.
2. شغّل:
   ```bash
   cd /opt/archive
   sudo ./install.sh 192.168.1.50
   ```
   ضع مكان `192.168.1.50` عنوان السيرفر الذي سيفتحه المستخدمون. إن تركته فارغاً يُستخدم أول عنوان IP للسيرفر.
3. في النهاية يظهر رابط الأرشيف واسم المستخدم وكلمة المرور الأولى. هذه البيانات محفوظة أيضاً في `.env`، **غيّر كلمة المرور بعد أول دخول**.

ما يفعله `install.sh`:
- يتحقق من سلامة الملفات.
- يحمّل الصور إلى Docker.
- ينشئ `.env` بكلمات سر عشوائية (في التثبيت الأول فقط).
- يشغّل الخدمات وينتظر حتى يعمل الموقع.

التشغيل الأول يأخذ بضع دقائق، لأنه ينشئ قاعدة البيانات ويحمّل النموذج.

## الاستخدام اليومي

```bash
cd /opt/archive
sudo docker compose ps                  # حالة الخدمات
sudo docker compose logs -f webserver   # السجلات
sudo docker compose restart             # إعادة التشغيل
sudo docker compose down                # إيقاف (البيانات تبقى)
```

**تعديل الإعدادات:** عدّل `.env` ثم شغّل `sudo docker compose up -d`.

## التحديث إلى نسخة أحدث

انسخ الحزمة الجديدة **فوق** المجلد نفسه، مع الإبقاء على ملف `.env`، ثم شغّل:
```bash
sudo ./install.sh
```
تُحدَّث الصور وتُعاد تشغيل الحاويات. قاعدة البيانات والمستندات والإعدادات تبقى كما هي، وتحديثات قاعدة البيانات تُطبَّق تلقائياً.

## أين تُحفظ البيانات

في Volumes تابعة لـ Docker:

| الـ Volume | المحتوى |
|---|---|
| `archive_postgres_data` | قاعدة البيانات |
| `archive_paperless_media` | المستندات الأصلية ونسخ الأرشفة |
| `archive_paperless_data` | الفهارس وملفات التطبيق |

**لا تحذفها**: الأمر `docker compose down -v` يمسح كل شيء.

## النسخ الاحتياطي

```bash
sudo docker compose exec webserver python src/manage.py document_exporter /usr/src/paperless/export --zip
sudo docker compose cp webserver:/usr/src/paperless/export/. ./backups/
```
احفظ مجلد `backups` خارج السيرفر.

## نقل المستندات من السيرفر الحالي

**على السيرفر القديم** (من مجلد `docker/compose`):
```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod exec webserver \
  python src/manage.py document_exporter /usr/src/paperless/export --zip --zip-name archive-move
docker compose -f docker-compose.prod.yml --env-file .env.prod cp \
  webserver:/usr/src/paperless/export/archive-move.zip .
```

**انقل `archive-move.zip` إلى السيرفر الجديد**، ثم شغّل بعد التثبيت:
```bash
sudo docker compose cp archive-move.zip webserver:/usr/src/paperless/export/
sudo docker compose exec webserver python src/manage.py document_importer /usr/src/paperless/export/archive-move.zip
sudo docker compose exec webserver python src/manage.py document_llmindex rebuild
```

ملاحظتان:
- الاستيراد يكون إلى أرشيف **فارغ**، أي مباشرة بعد التثبيت.
- المستخدمون والوسوم والإعدادات تُنقل مع المستندات.

## الوصول عبر HTTPS أو اسم نطاق

إن وضعت nginx أمام الأرشيف:
1. عدّل `PAPERLESS_URL` و`PAPERLESS_ALLOWED_HOSTS` في `.env`.
2. أضف إلى إعداد nginx (مطلوب للرفع المقسّم):
   ```nginx
   client_max_body_size 64m;
   proxy_request_buffering off;
   proxy_read_timeout 300s;
   proxy_send_timeout 300s;
   ```
3. أضف إلى `.env`:
   ```
   NUM_PROXIES=1
   PROXY_SSL_HEADER='["HTTP_X_FORWARDED_PROTO", "https"]'
   ```

## سرعة الذكاء الاصطناعي

لقياس سرعة النموذج على هذا السيرفر:
```bash
sudo docker compose exec ollama ollama run gemma4:e4b --verbose "اكتب جملة واحدة عن الأرشفة"
```
- **السرعة المقبولة:** `eval rate` فوق 5 tokens/s تقريباً.
- **إن كانت أقل:** خفّض `AI_FAST_CONTEXT_CHARS` في `.env`، أو اطلب حزمة بنموذج أصغر.

## حل المشكلات

| المشكلة | الحل |
|---|---|
| `Some image files are damaged` | الملفات لم تُنسخ كاملة: انسخ مجلد `images` مرة أخرى |
| الموقع لا يفتح من جهاز آخر | تأكد أن المنفذ (`PAPERLESS_PORT`، افتراضياً 8000) مفتوح في الجدار الناري، وأن العنوان موجود في `PAPERLESS_ALLOWED_HOSTS` |
| `CSRF verification failed` عند الدخول | `PAPERLESS_URL` في `.env` يجب أن يطابق تماماً العنوان المكتوب في المتصفح |
| الذكاء الاصطناعي لا يجيب | `sudo docker compose logs ollama`، وتأكد من وجود ذاكرة حرة كافية للنموذج |
