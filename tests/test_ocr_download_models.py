import io

from src.ocr import download_models as dm


def test_builds_both_tiers_in_order():
    calls = []
    out = io.StringIO()
    rc = dm.download_models(builder=calls.append, available=True, out=out)
    assert rc == 0
    assert calls == ["light", "heavy"]


def test_failure_returns_nonzero_and_continues():
    calls = []

    def builder(tier):
        calls.append(tier)
        if tier == "light":
            raise RuntimeError("network down")

    out = io.StringIO()
    rc = dm.download_models(builder=builder, available=True, out=out)
    assert rc == 1
    assert calls == ["light", "heavy"]
    assert "network down" in out.getvalue()


def test_not_installed_returns_nonzero_without_building():
    calls = []
    out = io.StringIO()
    rc = dm.download_models(builder=calls.append, available=False, out=out)
    assert rc == 1
    assert calls == []
    assert "uv sync" in out.getvalue()


def test_default_builder_uses_service_engine(monkeypatch):
    from src.ocr.rapidocr_service import RapidOCRService

    seen = []
    monkeypatch.setattr(
        RapidOCRService, "_get_engine", lambda self: seen.append(self._tier)
    )
    dm._build_engine("heavy")
    dm._build_engine("light")
    assert seen == ["heavy", "light"]
