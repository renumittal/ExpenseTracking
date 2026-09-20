"""
Supplier bill files: validation and private Supabase Storage access.

Only the server talks to Supabase (with the service-role key). The browser never gets the key
or a permanent URL; it gets a signed URL that expires in BILL_URL_EXPIRY_SECONDS.
"""

import json
import logging
import os
import re
import uuid
from urllib import error, parse, request as urlrequest

from django.conf import settings

logger = logging.getLogger(__name__)

# content type -> (allowed extensions, magic-byte check). The type is decided from the file's
# own bytes, never from what the browser claims.
ALLOWED_TYPES = {
    'image/jpeg': ({'.jpg', '.jpeg'}, lambda head: head.startswith(b'\xff\xd8\xff')),
    'image/png': ({'.png'}, lambda head: head.startswith(b'\x89PNG\r\n\x1a\n')),
    'application/pdf': ({'.pdf'}, lambda head: head.startswith(b'%PDF-')),
}
STORAGE_EXT = {'image/jpeg': '.jpg', 'image/png': '.png', 'application/pdf': '.pdf'}
HTTP_TIMEOUT = 15


class BillValidationError(Exception):
    pass


class StorageNotConfigured(Exception):
    pass


class StorageError(Exception):
    pass


def validate_bill_file(uploaded):
    """Return (content_type, safe_filename) for an acceptable JPG/PNG/PDF, else raise BillValidationError."""
    if uploaded is None:
        raise BillValidationError('Attach the bill in the "file" field.')
    if uploaded.size == 0:
        raise BillValidationError('The file is empty.')
    if uploaded.size > settings.BILL_MAX_BYTES:
        raise BillValidationError(f'The file is too large (maximum {settings.BILL_MAX_BYTES // (1024 * 1024)} MB).')

    head = uploaded.read(16)
    uploaded.seek(0)
    content_type = next((ct for ct, (_, check) in ALLOWED_TYPES.items() if check(head)), None)
    if content_type is None:
        raise BillValidationError('Only JPG, JPEG, PNG or PDF files are allowed.')

    filename = clean_filename(uploaded.name)
    if os.path.splitext(filename)[1].lower() not in ALLOWED_TYPES[content_type][0]:
        raise BillValidationError('The file extension does not match the file content.')
    return content_type, filename


def clean_filename(name):
    """Display-only name: no path, no control characters, bounded length. Never used as a storage key."""
    base = os.path.basename((name or '').replace('\\', '/'))
    base = re.sub(r'[\x00-\x1f\x7f]', '', base).strip()
    stem, ext = os.path.splitext(base)
    return (stem[:200] + ext[:20]) or 'bill'


def new_object_path(project_id, transaction_id, content_type):
    """Server-generated storage key; the user's filename is never part of it."""
    return f'bills/{int(project_id)}/{int(transaction_id)}/{uuid.uuid4().hex}{STORAGE_EXT[content_type]}'


# ---------------------------------------------------------------------------
# Supabase Storage REST calls
# ---------------------------------------------------------------------------

def _config():
    if not (settings.SUPABASE_URL and settings.SUPABASE_SERVICE_ROLE_KEY and settings.SUPABASE_BILLS_BUCKET):
        raise StorageNotConfigured()
    return settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY, settings.SUPABASE_BILLS_BUCKET


def _call(method, url, key, *, data=None, content_type=None):
    headers = {'Authorization': f'Bearer {key}', 'apikey': key}
    if content_type:
        headers['Content-Type'] = content_type
    req = urlrequest.Request(url, data=data, method=method, headers=headers)
    try:
        with urlrequest.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return resp.read()
    except error.HTTPError as exc:
        logger.error('Supabase Storage %s failed: HTTP %s', method, exc.code)   # never log the URL or key
        raise StorageError() from exc
    except (error.URLError, TimeoutError, OSError) as exc:
        logger.error('Supabase Storage %s failed: %s', method, type(exc).__name__)
        raise StorageError() from exc


def _object_url(base, bucket, path):
    return f'{base}/storage/v1/object/{parse.quote(bucket)}/{parse.quote(path)}'


def upload_object(path, data, content_type):
    base, key, bucket = _config()
    # x-upsert is off by default: an existing object is never overwritten.
    _call('POST', _object_url(base, bucket, path), key, data=data, content_type=content_type)


def delete_object(path):
    """Best-effort cleanup of an object that never got recorded in the database."""
    try:
        base, key, bucket = _config()
        _call('DELETE', _object_url(base, bucket, path), key)
    except (StorageNotConfigured, StorageError):
        logger.error('Could not remove orphaned bill object; remove it manually from the bucket.')


def signed_url(path, expires_in=None):
    base, key, bucket = _config()
    expires_in = expires_in or settings.BILL_URL_EXPIRY_SECONDS
    url = f'{base}/storage/v1/object/sign/{parse.quote(bucket)}/{parse.quote(path)}'
    raw = _call('POST', url, key, data=json.dumps({'expiresIn': expires_in}).encode(), content_type='application/json')
    try:
        relative = json.loads(raw)['signedURL']
    except (ValueError, KeyError, TypeError) as exc:
        raise StorageError() from exc
    if not relative.startswith('/'):
        relative = '/' + relative
    return f'{base}/storage/v1{relative}'
