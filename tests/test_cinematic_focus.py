"""Minimal cinema treatment: preserve review controls, accessibility and local-only media."""
from __future__ import annotations

from importlib.resources import files


def _asset(name):
    return files("adventure_intelligence").joinpath("studio_assets",name).read_text()


def test_cinema_stage_is_one_video_with_three_primary_decisions():
    html=_asset("focus.html")
    assert html.count('id="reviewPlayer"')==1
    assert 'id="keepClip"' in html
    assert 'id="skipClip"' in html
    assert 'id="wrongClip"' in html
    assert html.count('class="decision-buttons"')==1
    assert 'id="reviewProgress"' in html
    assert 'role="progressbar"' in html
    assert 'aria-valuemin="0"' in html and 'aria-valuemax="100"' in html
    assert 'id="cinemaToggle"' in html and 'aria-pressed="false"' in html
    assert 'id="replayClip"' in html and 'disabled' in html.split('id="replayClip"',1)[1][:180]
    assert 'id="stageState"' in html and 'role="status"' in html
    assert 'id="shortcutsDialog"' in html
    assert 'id="closeShortcuts"' in html
    assert 'id="libraryView"' in html and 'id="sequenceView"' in html
    assert 'id="detailsPanel" class="details-panel hidden"' in html
    assert 'id="feedbackForm" class="feedback-form hidden"' in html


def test_cinema_appearance_is_sparing_responsive_and_reduced_motion_safe():
    css=_asset("focus.css")
    assert "--navy:#0d1728" in css
    assert "--salmon:#f4aa9b" in css
    assert "--offwhite:#f6f8fb" in css
    assert "--fog:#b5c1d0" in css
    assert ".stage-corner" in css
    assert ".screen video{z-index:1}" in css
    assert ".screen video{position:relative" not in css
    assert ".review-progress" in css
    assert ".cinema-active" in css
    assert "@media(max-width:720px)" in css
    assert "prefers-reduced-motion:reduce" in css
    assert "pointer-events:none" in css
    assert "@import" not in css
    assert "https://" not in css


def test_replay_and_cinema_are_view_only_and_no_export_is_triggered():
    script=_asset("focus.js")
    assert "function cinema(enabled)" in script
    assert "function replay()" in script
    assert "function progress(index,total)" in script
    assert "cinema(false)" in script
    assert "'r'" in script and "'f'" in script
    assert "player.currentTime=Number($('clipIn').value)" in script
    assert "stageState" in script
    assert "KLIP_SESSION_TOKEN" not in script
    assert "https://" not in script
    assert "localStorage" not in script
    assert "sessionStorage" not in script
