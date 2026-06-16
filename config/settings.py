import os
from pathlib import Path
from datetime import timedelta
from dotenv import load_dotenv
import dj_database_url

# =====================
# LOAD ENV
# =====================
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# =====================
# SECURITY
# =====================
SECRET_KEY = os.getenv("SECRET_KEY", "unsafe-secret-key")

# DEBUG = True
# DEBUG = os.getenv("DEBUG") == "1"

DEBUG = os.getenv("DEBUG", "False").lower() == "true"

ALLOWED_HOSTS = ["*"]

# =====================
# CUSTOM USER
# =====================
AUTH_USER_MODEL = "accounts.User"

# =====================
# INSTALLED APPS
# =====================
# INSTALLED_APPS = [
#     # "jazzmin",
#     "django.contrib.admin",
#     "django.contrib.auth",
#     "django.contrib.contenttypes",
#     "django.contrib.sessions",
#     "django.contrib.messages",
#     "django.contrib.staticfiles",

#     # Third-party
#     "rest_framework",
#     "jazzmin",
#     "drf_spectacular",
#     # "django_celery_results",

#     # Local apps
#     "accounts",
#     "fraud",
#     "ingestion",
#     "django_celery_beat"
# ]

INSTALLED_APPS = [
    "jazzmin",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    "rest_framework",
    # "drf_yasg",
    "drf_spectacular",
    "django_celery_beat",


    # Local apps
    "accounts",
    "fraud",
    "ingestion",

]


# =====================
# MIDDLEWARE
# =====================
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

# =====================
# TEMPLATES
# =====================
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# =====================
# DATABASE
# =====================
# DATABASES = {
#     "default": dj_database_url.config(
#         default=os.getenv("DATABASE_URL")
#     )
# }


# DATABASES = {
#     "default": {
#         "ENGINE": "django.db.backends.sqlite3",
#         "NAME": BASE_DIR / "db.sqlite3",
#     }
# }


# DATABASES = {
#     'default': {
#         'ENGINE': 'django.db.backends.postgresql',
#         'NAME': 'mydb',
#         'USER': 'myuser',
#         'PASSWORD': 'mypassword',
#         'HOST': 'db',
#         'PORT': '5432',
#     }
# }

# DATABASES = {
#     'default': dj_database_url.config(default=os.getenv('DATABASE_URL')) or {
#         'ENGINE': 'django.db.backends.postgresql',
#         'NAME': os.getenv('POSTGRES_DB'),
#         'USER': os.getenv('POSTGRES_USER'),
#         'PASSWORD': os.getenv('POSTGRES_PASSWORD'),
#         'HOST': os.getenv('DB_HOST', 'db'),
#         'PORT': os.getenv('DB_PORT', '5432'),
#     }
# }

DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.parse(DATABASE_URL)
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("POSTGRES_DB"),
            "USER": os.getenv("POSTGRES_USER"),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD"),
            "HOST": os.getenv("DB_HOST", "db"),
            "PORT": os.getenv("DB_PORT", "5432"),
        }
    }

# =====================
# PASSWORD VALIDATION
# =====================
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# =====================
# INTERNATIONALIZATION
# =====================
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# =====================
# STATIC FILES
# =====================
STATIC_URL = "/static/"

STATIC_ROOT = os.path.join(BASE_DIR, "staticfiles")

STATICFILES_DIRS = [
    os.path.join(BASE_DIR, "static"),
]

# =====================
# DRF + JWT
# =====================
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# =====================
# SWAGGER (SPECTACULAR)
# =====================
SPECTACULAR_SETTINGS = {
    "TITLE": "AxiomVault API",
    "DESCRIPTION": "Fraud Detection Backend System",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# =====================
# CELERY + REDIS
# =====================

REDIS_URL = os.getenv('REDIS_URL') or f"redis://{os.getenv('REDIS_HOST', 'redis')}:{os.getenv('REDIS_PORT', '6379')}"

CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', f"{REDIS_URL}/0")
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', f"{REDIS_URL}/0")

CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'

# =====================
# CACHE (REDIS)
# =====================
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": os.getenv('REDIS_CACHE_URL', f"{REDIS_URL}/1")
    }
}

# =====================
# EMAIL CONFIG
# =====================
# EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
# EMAIL_HOST = "smtp.gmail.com"
# EMAIL_PORT = 587
# EMAIL_USE_TLS = True

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "smtp.gmail.com"
EMAIL_PORT = 465
EMAIL_USE_SSL = True
EMAIL_USE_TLS = False


EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL")


# =====================
# DEFAULT PRIMARY KEY
# =====================
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# SETTING UP THE JAZZMIN FOR ADMIN PAGE

JAZZMIN_SETTINGS = {
    "site_title": "AxiomVault Admin",
    "site_header": "AxiomVault Control Panel",
    "site_brand": "AxiomVault",
    "welcome_sign": "Welcome to AxiomVault 🚀",

    "theme": "darkly",  # try: flatly, cosmo, lumen

    "icons": {
        "accounts.User": "fas fa-users",
        "accounts.OTP": "fas fa-key",
        "accounts.Session": "fas fa-clock",
    },
}