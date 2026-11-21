import os
from urllib.parse import parse_qs, unquote, urlparse

import pytest

URL = os.getenv('GB5305_TEST_DATABASE_URL', '')
os.environ.setdefault('AUTH_KEY', 'test-key')

if URL:
    # Point src.config.settings at the test database *before* any src module is
    # imported, so the engine used by routes and services is the test engine.
    parsed = urlparse(URL.replace('postgresql+psycopg://', 'postgresql://'))
    os.environ['DB_DRIVER'] = 'postgresql+psycopg'
    os.environ['DB_HOST'] = parsed.hostname or 'localhost'
    os.environ['DB_PORT'] = str(parsed.port or 5432)
    os.environ['DB_NAME'] = (parsed.path or '/workflow').lstrip('/')
    os.environ['DB_USER'] = unquote(parsed.username or 'app')
    if parsed.password:
        os.environ['DB_PASSWORD'] = unquote(parsed.password)
    query = parse_qs(parsed.query)
    if 'sslmode' in query:
        # settings builds its own URL; only the common local cases are needed.
        pass


def pytest_collection_modifyitems(config, items):
    if URL:
        return
    skip = pytest.mark.skip(reason='GB5305_TEST_DATABASE_URL is not configured')
    for item in items:
        item.add_marker(skip)
