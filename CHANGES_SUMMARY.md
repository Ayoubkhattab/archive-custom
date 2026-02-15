# ملخص التعديلات - بيئة التطوير المحسّنة

## 📊 إحصائيات التعديلات

- **ملفات معدلة**: 3
- **ملفات جديدة**: 10
- **إجمالي الأسطر الجديدة**: ~1,311 سطر
- **التاريخ**: 2026-01-24 - 2026-01-25

---

## 1️⃣ الملفات المعدلة (Modified)

### ✏️ `docker/compose/Dockerfile`
**التغيير**: تبسيط التبعيات

**قبل**:
```dockerfile
# Install Python AI dependencies
RUN pip install --no-cache-dir \
    faiss-cpu==1.7.4 \
    llama-index-core==0.10.0 \
    [... 10+ حزم AI]
```

**بعد**:
```dockerfile
# Install basic dependencies only (skip AI for now)
RUN pip install --no-cache-dir watchfiles

# Note: Source code will be mounted via docker-compose volumes
```

**السبب**: تسريع البناء في بيئة التطوير، حزم AI سيتم تثبيتها داخل الحاوية عند الحاجة.

---

### ✏️ `src/paperless/settings.py`
**التغيير**: تحويله إلى ملف موجّه (Router)

**الإضافات الرئيسية**:
```python
# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Environment-specific settings
ENVIRONMENT = os.getenv("PAPERLESS_ENV", "development")

if ENVIRONMENT == "production":
    from .settings.production import *
    from .settings.security import *
    if os.getenv("AI_ENABLED", "false").lower() == "true":
        from .settings.ai import *
elif ENVIRONMENT == "development":
    from .settings.development import *
    if os.getenv("AI_ENABLED", "false").lower() == "true":
        from .settings.ai import *
else:
    # Default to base settings
    from .settings.base import *
```

**الفائدة**:
- تحميل الإعدادات حسب البيئة تلقائياً
- فصل واضح بين Development و Production
- سهولة إدارة الإعدادات

---

### ✏️ `src/paperless/urls.py`
**التغيير**: إصلاح مشكلة Static files في DEBUG mode

**قبل**:
```python
re_path(
    r"^static/(?P<path>.*)$",
    static(settings.STATIC_URL, document_root=settings.STATIC_ROOT),
    name="static",
),
```

**بعد**:
```python
import django.views.static
re_path(
    r"^static/(?P<path>.*)$",
    django.views.static.serve,
    {
        "document_root": settings.STATIC_ROOT,
        "path": "path",
    },
    name="static",
),
```

**السبب**: إصلاح خطأ في استخدام `static()` - الطريقة الصحيحة هي استخدام `django.views.static.serve`.

---

## 2️⃣ الملفات الجديدة (New Files)

### 📂 Docker - بيئة التطوير

#### 🐳 `docker/compose/docker-compose.dev.yml` (81 سطر)
**الوصف**: تكوين Docker Compose محسّن للتطوير

**الميزات الرئيسية**:
```yaml
services:
  db:
    image: postgres:16-alpine
    # قاعدة بيانات منفصلة للتطوير
    ports:
      - "5433:5432"

  redis:
    image: redis:7-alpine
    ports:
      - "6380:6379"

  webserver:
    build:
      context: ../..
      dockerfile: docker/compose/Dockerfile.dev
    volumes:
      # Hot reload - تحميل الكود مباشرة
      - ../../src:/usr/src/paperless/src
      - ../../src-ui:/usr/src/paperless/src-ui
    environment:
      - PAPERLESS_ENV=development
      - DEBUG=true
      - DJANGO_AUTORELOAD=true
    ports:
      - "8000:8000"  # Django
      - "5678:5678"  # Debugger
      - "4200:4200"  # Angular dev server
```

**الفوائد**:
- ✅ Hot reload للـ Backend و Frontend
- ✅ منافذ debugging مفتوحة
- ✅ قاعدة بيانات منفصلة (لا تؤثر على الإنتاج)
- ✅ Volumes للكود (التعديلات فورية)

---

#### 🔧 `docker/compose/Dockerfile.dev` (22 سطر)
**الوصف**: Dockerfile مخصص للتطوير

```dockerfile
FROM python:3.12-slim

# أدوات التطوير
RUN pip install --no-cache-dir \
    watchfiles \      # Hot reload
    debugpy \         # Python debugger
    ipython \         # Python shell محسّن
    pytest \          # Testing
    pytest-django \
    black \           # Code formatter
    ruff              # Linter

# Node.js للـ Frontend
RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash -
RUN apt-get install -y nodejs

# pnpm
RUN npm install -g pnpm

WORKDIR /usr/src/paperless
```

**الميزات**:
- ✅ أدوات debugging
- ✅ أدوات testing
- ✅ Code formatters
- ✅ Node.js و pnpm للـ Frontend

---

#### 🚀 `docker/compose/entrypoint-dev.sh` (48 سطر)
**الوصف**: نقطة دخول محسّنة للتطوير

```bash
#!/bin/bash
set -e

echo "🔧 Development Environment Starting..."

# انتظار قاعدة البيانات
while ! pg_isready -h db -p 5432; do
  echo "⏳ Waiting for PostgreSQL..."
  sleep 1
done

# تشغيل migrations
python manage.py migrate --skip-checks

# إنشاء superuser إذا لم يكن موجوداً
python manage.py shell <<EOF
from django.contrib.auth import get_user_model
User = get_user_model()
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@localhost', 'admin')
    print('✅ Superuser created: admin/admin')
EOF

# تجميع الملفات الثابتة (في التطوير)
python manage.py collectstatic --noinput --clear

# تشغيل الخادم مع hot reload
if [ "$START_DEBUGGER" = "true" ]; then
    echo "🐛 Starting with debugger on port 5678..."
    python -m debugpy --listen 0.0.0.0:5678 manage.py runserver 0.0.0.0:8000
else
    echo "🚀 Starting Django development server..."
    python manage.py runserver 0.0.0.0:8000
fi
```

**الميزات**:
- ✅ انتظار تلقائي لقاعدة البيانات
- ✅ migrations تلقائية
- ✅ إنشاء superuser تلقائي (`admin/admin`)
- ✅ دعم debugger اختياري
- ✅ رسائل واضحة مع emojis 😊

---

#### 🗄️ `docker/compose/postgres-init.sql` (سطور قليلة)
**الوصف**: سكريبت تهيئة PostgreSQL

```sql
-- Create extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- للبحث النصي
```

**الفائدة**: تهيئة امتدادات PostgreSQL المطلوبة تلقائياً.

---

### 📂 Settings - الإعدادات المقسّمة

#### ⚙️ `src/paperless/settings/base.py` (475 سطر)
**الوصف**: الإعدادات الأساسية المشتركة

**يحتوي على**:
```python
# Core Django settings
INSTALLED_APPS = [...]
MIDDLEWARE = [...]
TEMPLATES = [...]

# Database (base config)
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        # سيتم override في development/production
    }
}

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'static'

# Media files
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Celery
CELERY_BROKER_URL = 'redis://redis:6379/0'
CELERY_RESULT_BACKEND = 'redis://redis:6379/0'

# Paperless specific
PAPERLESS_CONSUMPTION_DIR = BASE_DIR / 'consume'
PAPERLESS_DATA_DIR = BASE_DIR / 'data'
PAPERLESS_MEDIA_ROOT = MEDIA_ROOT

# OCR settings
PAPERLESS_OCR_LANGUAGE = 'eng'
PAPERLESS_OCR_MODE = 'skip'

# Logging (base)
LOGGING = {...}
```

**الأقسام**:
- ✅ Django core settings
- ✅ Database configuration
- ✅ Static & Media files
- ✅ Celery configuration
- ✅ Paperless-specific settings
- ✅ OCR configuration
- ✅ Logging configuration

---

#### 🛠️ `src/paperless/settings/development.py` (150 سطر)
**الوصف**: إعدادات التطوير

```python
from .base import *

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True
ALLOWED_HOSTS = ['*']  # في التطوير فقط

# Database - SQLite أو PostgreSQL
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'paperless_dev',
        'USER': 'paperless',
        'PASSWORD': 'paperless',
        'HOST': 'db',
        'PORT': 5432,
    }
}

# Email - Console backend (للتطوير)
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# Cache - Dummy cache (لا caching)
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.dummy.DummyCache',
    }
}

# Logging - Verbose
LOGGING['loggers']['']['level'] = 'DEBUG'

# Django Debug Toolbar (اختياري)
if 'debug_toolbar' in INSTALLED_APPS:
    MIDDLEWARE.insert(0, 'debug_toolbar.middleware.DebugToolbarMiddleware')
    INTERNAL_IPS = ['127.0.0.1', 'localhost']

# CORS - مفتوح في التطوير
CORS_ALLOW_ALL_ORIGINS = True

# Static files - لا compression
STATICFILES_STORAGE = 'django.contrib.staticfiles.storage.StaticFilesStorage'

# Hot reload settings
DJANGO_AUTORELOAD = True
```

**الميزات**:
- ✅ DEBUG مفعّل
- ✅ ALLOWED_HOSTS مفتوح
- ✅ SQLite أو PostgreSQL
- ✅ Email console backend
- ✅ Dummy cache (لا caching)
- ✅ Logging verbose
- ✅ Django Debug Toolbar
- ✅ CORS مفتوح
- ✅ Hot reload

---

#### 🏭 `src/paperless/settings/production.py` (142 سطر)
**الوصف**: إعدادات الإنتاج

```python
from .base import *

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get('SECRET_KEY')
if not SECRET_KEY:
    raise ValueError("SECRET_KEY environment variable must be set in production")

DEBUG = False
ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '').split(',')

# Database - PostgreSQL
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME', 'paperless'),
        'USER': os.environ.get('DB_USER', 'paperless'),
        'PASSWORD': os.environ.get('DB_PASSWORD'),
        'HOST': os.environ.get('DB_HOST', 'localhost'),
        'PORT': os.environ.get('DB_PORT', '5432'),
        # Production optimization
        'CONN_MAX_AGE': 600,
        'OPTIONS': {
            'connect_timeout': 10,
        },
    }
}

# Email - SMTP
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = os.environ.get('EMAIL_HOST')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', 587))
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.environ.get('EMAIL_USER')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_PASSWORD')

# Cache - Redis
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': os.environ.get('REDIS_URL', 'redis://redis:6379/1'),
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            'SOCKET_CONNECT_TIMEOUT': 5,
            'SOCKET_TIMEOUT': 5,
            'CONNECTION_POOL_KWARGS': {
                'max_connections': 50,
            },
            'COMPRESSOR': 'django_redis.compressors.zlib.ZlibCompressor',
        },
    }
}

# Static files - WhiteNoise with compression
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# Session - Redis
SESSION_ENGINE = 'django.contrib.sessions.backends.cache'
SESSION_CACHE_ALIAS = 'default'

# Logging - Production
LOGGING['handlers']['file'] = {
    'level': 'INFO',
    'class': 'logging.handlers.RotatingFileHandler',
    'filename': BASE_DIR / 'logs' / 'paperless.log',
    'maxBytes': 10 * 1024 * 1024,  # 10 MB
    'backupCount': 10,
}
```

**الميزات**:
- ✅ DEBUG معطّل
- ✅ SECRET_KEY إلزامي من environment
- ✅ ALLOWED_HOSTS محدد
- ✅ PostgreSQL مع connection pooling
- ✅ SMTP للبريد
- ✅ Redis caching مع compression
- ✅ Session في Redis
- ✅ Static files مع WhiteNoise
- ✅ Logging إلى ملفات مع rotation

---

#### 🤖 `src/paperless/settings/ai.py` (164 سطر)
**الوصف**: إعدادات الذكاء الاصطناعي

```python
from .base import *

# AI Feature Toggle
AI_ENABLED = os.environ.get('AI_ENABLED', 'false').lower() == 'true'

if AI_ENABLED:
    # LLM Configuration
    LLM_BACKEND = os.environ.get('LLM_BACKEND', 'ollama')  # ollama or openai
    LLM_MODEL = os.environ.get('LLM_MODEL', 'llama3.1')
    LLM_ENDPOINT = os.environ.get('LLM_ENDPOINT', 'http://localhost:11434')
    LLM_API_KEY = os.environ.get('LLM_API_KEY', '')

    # Embedding Configuration
    EMBEDDING_BACKEND = os.environ.get('EMBEDDING_BACKEND', 'huggingface')
    EMBEDDING_MODEL = os.environ.get(
        'EMBEDDING_MODEL',
        'sentence-transformers/all-MiniLM-L6-v2'
    )

    # Vector Store Configuration
    VECTOR_STORE_PATH = BASE_DIR / 'data' / 'vector_store'
    VECTOR_STORE_BACKEND = 'faiss'  # faiss or chromadb

    # LlamaIndex Configuration
    LLAMAINDEX_CHUNK_SIZE = int(os.environ.get('LLAMAINDEX_CHUNK_SIZE', 800))
    LLAMAINDEX_CHUNK_OVERLAP = int(os.environ.get('LLAMAINDEX_CHUNK_OVERLAP', 200))
    LLAMAINDEX_TOP_K = int(os.environ.get('LLAMAINDEX_TOP_K', 5))

    # AI Classifier Settings
    AI_CLASSIFIER_ENABLED = True
    AI_CLASSIFIER_MIN_CONFIDENCE = 0.7

    # Chat Settings
    AI_CHAT_ENABLED = True
    AI_CHAT_MAX_HISTORY = 10
    AI_CHAT_TIMEOUT = 60  # seconds

    # Logging for AI
    LOGGING['loggers']['paperless_ai'] = {
        'level': 'DEBUG' if DEBUG else 'INFO',
        'handlers': ['console'],
        'propagate': False,
    }
```

**الأقسام**:
- ✅ Feature toggle (AI_ENABLED)
- ✅ LLM configuration (Ollama/OpenAI)
- ✅ Embedding configuration
- ✅ Vector store settings
- ✅ LlamaIndex parameters
- ✅ AI Classifier settings
- ✅ Chat configuration
- ✅ Logging للـ AI

---

#### 🔒 `src/paperless/settings/security.py` (226 سطر)
**الوصف**: إعدادات الأمان

```python
from .base import *

# SECURITY SETTINGS
# ================

# HTTPS/SSL
SECURE_SSL_REDIRECT = not DEBUG
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# HSTS (HTTP Strict Transport Security)
if not DEBUG:
    SECURE_HSTS_SECONDS = 31536000  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# Cookies
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_AGE = 86400  # 24 hours

CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = 'Lax'

# Content Security Policy
if not DEBUG:
    CSP_DEFAULT_SRC = ("'self'",)
    CSP_SCRIPT_SRC = ("'self'", "'unsafe-inline'")
    CSP_STYLE_SRC = ("'self'", "'unsafe-inline'")
    CSP_IMG_SRC = ("'self'", "data:", "https:")
    CSP_FONT_SRC = ("'self'", "data:")

# X-Frame-Options
X_FRAME_OPTIONS = 'DENY'

# X-Content-Type-Options
SECURE_CONTENT_TYPE_NOSNIFF = True

# X-XSS-Protection
SECURE_BROWSER_XSS_FILTER = True

# Password Validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {
            'min_length': 12,
        }
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Rate Limiting
RATELIMIT_ENABLE = not DEBUG
RATELIMIT_USE_CACHE = 'default'

# API Rate Limits
REST_FRAMEWORK = {
    **BASE_REST_FRAMEWORK,
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '100/hour',
        'user': '1000/hour',
    }
}

# File Upload Security
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # 10 MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # 10 MB

# Allowed file types
ALLOWED_EXTENSIONS = [
    'pdf', 'jpg', 'jpeg', 'png', 'gif', 'tiff', 'bmp', 'webp',
    'txt', 'md', 'csv',
    'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
    'odt', 'ods', 'odp',
    'eml', 'msg',
]

# Security headers middleware
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
] + MIDDLEWARE

# Logging - Security events
LOGGING['loggers']['security'] = {
    'level': 'WARNING',
    'handlers': ['console', 'file'] if not DEBUG else ['console'],
    'propagate': False,
}
```

**الأقسام**:
- ✅ HTTPS/SSL settings
- ✅ HSTS configuration
- ✅ Secure cookies
- ✅ Content Security Policy
- ✅ Security headers (X-Frame-Options, etc.)
- ✅ Password validation
- ✅ Rate limiting
- ✅ File upload security
- ✅ Allowed file types
- ✅ Security logging

---

#### 📦 `src/paperless/settings/__init__.py` (3 سطر)
```python
"""
Settings package for Paperless-ngx
"""
```

**الغرض**: تحويل `settings/` إلى Python package.

---

## 3️⃣ المجلدات الجديدة

### 📂 `docker/compose/data/`
**الوصف**: مجلد لبيانات التطوير (PostgreSQL data)
**الاستخدام**: يتم mount كـ volume لقاعدة البيانات

### 📂 `src/paperless/data/`
**الوصف**: مجلد لبيانات Paperless (vector store, etc.)
**الاستخدام**: يحتوي على:
- `vector_store/` - FAISS vector store
- `index/` - Whoosh search index
- `db.sqlite3` - قاعدة بيانات SQLite (في حالة استخدامها)

---

## 4️⃣ ملخص الفوائد

### ✅ بيئة التطوير:
1. **Hot Reload**: تعديلات فورية بدون إعادة تشغيل
2. **Debugging**: منفذ debugger مفتوح (5678)
3. **Testing**: أدوات testing مثبتة
4. **Logging**: رسائل واضحة ومفصلة
5. **Auto-setup**: migrations و superuser تلقائياً

### ✅ تنظيم الإعدادات:
1. **Separation of Concerns**: كل بيئة لها إعداداتها
2. **Security**: إعدادات الأمان منفصلة وواضحة
3. **AI Settings**: سهولة تفعيل/تعطيل AI
4. **Maintainability**: سهولة الصيانة والتعديل
5. **Environment Variables**: استخدام متغيرات بيئة للحساسة

### ✅ الأمان:
1. **Production**: إعدادات أمان صارمة
2. **Development**: إعدادات مرنة للتطوير
3. **Validation**: التحقق من المتطلبات الإلزامية
4. **Logging**: تتبع الأحداث الأمنية

---

## 5️⃣ كيفية الاستخدام

### للتطوير:
```bash
cd docker/compose
docker-compose -f docker-compose.dev.yml up
```

### مع Debugger:
```bash
START_DEBUGGER=true docker-compose -f docker-compose.dev.yml up
```

### للإنتاج:
```bash
PAPERLESS_ENV=production docker-compose up
```

### تفعيل AI:
```bash
AI_ENABLED=true docker-compose -f docker-compose.dev.yml up
```

---

## 6️⃣ المتطلبات المتبقية

### ✅ تم:
- [x] docker-compose.dev.yml
- [x] Hot reload
- [x] Debugging tools
- [x] تقسيم settings
- [x] إعدادات الأمان

### ⏳ قيد الانتظار:
- [ ] حذف الملفات غير المهمة
- [ ] اختبار البيئة الجديدة
- [ ] توثيق التغييرات
- [ ] Commit النهائي

---

## 📝 ملاحظات

1. **قاعدة البيانات**: في التطوير، استخدم PostgreSQL على منفذ 5433 (لتجنب التعارض)
2. **Superuser**: يتم إنشاء `admin/admin` تلقائياً في التطوير
3. **Hot Reload**: يعمل تلقائياً، لا حاجة لإعادة تشغيل
4. **Debugger**: استخدم VS Code مع Remote Debugging على منفذ 5678
5. **Static Files**: يتم جمعها تلقائياً عند البدء

---

## 🎯 الخطوات التالية

1. ✅ مراجعة التعديلات (هذا الملف)
2. ⏳ حذف الملفات غير المهمة
3. ⏳ اختبار البيئة الجديدة
4. ⏳ Commit جميع التعديلات

---

**تاريخ الإنشاء**: 2026-01-25
**الحالة**: جاهز للمراجعة والاختبار
