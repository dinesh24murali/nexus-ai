import app


def test_package_importable() -> None:
    assert app.__doc__ is None or isinstance(app.__doc__, str)
