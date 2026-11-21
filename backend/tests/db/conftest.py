from collections.abc import Iterator

import pytest

from tests.db_support import database


@pytest.fixture
def empty_db_url() -> Iterator[str]:
    with database() as url:
        yield url
