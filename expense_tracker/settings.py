"""
Django settings for expense_tracker project.
"""

from pathlib import Path

import dj_database_url
from decouple import Csv, config
from django.core.exceptions import ImproperlyConfigured

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# SECURITY WARNING: don't run with debug turned on in production!
# Secure by default: DEBUG is off unless DJANGO_DEBUG=True is set (local .env does this).
DEBUG = config('DJANGO_DEBUG', default=False, cast=bool)

# SECURITY WARNING: keep the secret key used in production secret!
# Only local development (DEBUG) may fall back to a throwaway key; production must set it.
if DEBUG:
    SECRET_KEY = config('DJANGO_SECRET_KEY', default='django-insecure-local-development-only')
else:
    SECRET_KEY = config('DJANGO_SECRET_KEY', default='')
    if not SECRET_KEY:
        raise ImproperlyConfigured('Set the DJANGO_SECRET_KEY environment variable.')

ALLOWED_HOSTS = config('DJANGO_ALLOWED_HOSTS', default='127.0.0.1,localhost', cast=Csv())

# The web app (GitHub Pages) calls this API from another origin. An origin is scheme + host
# only, with no path: https://renumittal.github.io (not .../ExpenseTracking/).
# Login uses a token in the Authorization header, not cookies, so no credentials are needed.
CORS_ALLOWED_ORIGINS = config('CORS_ALLOWED_ORIGINS', default='https://renumittal.github.io', cast=Csv())

# Only needed for the /admin/ login over https on the hosting domain, e.g. https://myapp.onrender.com
CSRF_TRUSTED_ORIGINS = config('CSRF_TRUSTED_ORIGINS', default='', cast=Csv())


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'corsheaders',
    'rest_framework',
    'rest_framework.authtoken',
    'core',
]

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
}

MIDDLEWARE = [
    'expense_tracker.middleware.HealthCheckMiddleware',  # /health/ only; must stay first
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',  # serves the admin's CSS/JS in production
    'corsheaders.middleware.CorsMiddleware',  # must come before CommonMiddleware
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'expense_tracker.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'expense_tracker.wsgi.application'


# Database
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases
#
# DATABASE_URL is expected to be the Supabase POOLED (transaction-mode, port 6543)
# connection string for normal app runtime.
#
# For `manage.py migrate` / `makemigrations` against Supabase, set DIRECT_DATABASE_URL
# (port 5432, db.<project-ref>.supabase.co) instead — run those commands with:
#   DATABASE_URL=$DIRECT_DATABASE_URL python manage.py migrate
# or just define DIRECT_DATABASE_URL in .env and export it before migrating.
#
# If DATABASE_URL is not set at all (e.g. first-time local setup before Supabase
# credentials are available), fall back to local SQLite so the project remains runnable.
DATABASE_URL = config('DATABASE_URL', default='')

if DATABASE_URL:
    DATABASES = {
        'default': dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=600,
            conn_health_checks=True,  # reconnect if Supabase dropped an idle connection
            disable_server_side_cursors=True,  # required by Supabase's transaction pooler (port 6543)
            ssl_require=True,
        )
    }
elif not DEBUG:
    # A hosted server's disk is wiped on restart; never silently use a throwaway SQLite file.
    raise ImproperlyConfigured('Set the DATABASE_URL environment variable (Supabase connection string).')
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }


# Password validation
# https://docs.djangoproject.com/en/4.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/4.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.2/howto/static-files/

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'  # `manage.py collectstatic` writes here (admin CSS/JS)
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage'},
}

# Production-only security settings (the host terminates https and forwards the request).
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = config('DJANGO_HSTS_SECONDS', default=3600, cast=int)  # raise once stable
    SECURE_CONTENT_TYPE_NOSNIFF = True

# Supplier bills: private Supabase Storage. The service-role key stays on the server only.
# If these are not set, the bill endpoints answer 503 and nothing else is affected.
SUPABASE_URL = config('SUPABASE_URL', default='').rstrip('/')
SUPABASE_SERVICE_ROLE_KEY = config('SUPABASE_SERVICE_ROLE_KEY', default='')
SUPABASE_BILLS_BUCKET = config('SUPABASE_BILLS_BUCKET', default='supplier-bills')
BILL_MAX_BYTES = config('BILL_MAX_BYTES', default=5 * 1024 * 1024, cast=int)
BILL_URL_EXPIRY_SECONDS = config('BILL_URL_EXPIRY_SECONDS', default=60, cast=int)

# Default primary key field type
# https://docs.djangoproject.com/en/4.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
