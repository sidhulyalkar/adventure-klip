from adventure_intelligence.plugins import detector_named
import pytest


def test_default_detector_contract():
    plugin=detector_named("baseline")
    assert plugin.version
    assert plugin.detect("abc",[],{})==[]


def test_unknown_detector_is_not_silently_fallback():
    with pytest.raises(ValueError,match="No detector"):
        detector_named("made-up-plugin")
