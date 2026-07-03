# tests/test_main.py
def test_main_importable():
    import app.__main__
    assert hasattr(app.__main__, "main")
