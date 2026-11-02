from adventure_intelligence.sessions import group_session_candidates


def asset(name, when, activity="ski", duration=120):
    return {"id":name,"capture_time":when,"activity":activity,"duration":duration}


def test_candidate_session_never_asserts_continuous_ski_run():
    assets=[asset("a","2026-02-01T10:00:00Z"),
            asset("b","2026-02-01T10:03:00+00:00"),
            asset("c","2026-02-01T16:00:00Z"),
            asset("d","2026-02-01T10:04:00Z",activity="bike")]
    result=group_session_candidates(assets,gap_minutes=5)
    assert len(result["candidates"])==1
    assert result["candidates"][0]["assetIds"]==["a","b"]
    assert result["candidates"][0]["status"]=="requires_review"
    assert "Not proof" in result["candidates"][0]["warning"]
    assert result["otherSingletons"]==2


def test_untrusted_naive_or_missing_capture_times_never_get_grouped():
    assets=[asset("a","2026-02-01T10:00:00"),asset("b",None),
            asset("c","garbage"),asset("d","2026-02-01T10:00:00Z",duration=0)]
    result=group_session_candidates(assets)
    assert result["candidates"]==[]
    assert result["ungroupedAssetIds"]==["a","b","c","d"]


def test_timezones_and_invalid_gaps():
    assets=[asset("a","2026-02-01T10:00:00Z"),asset("b","2026-02-01T02:03:00-08:00")]
    assert len(group_session_candidates(assets,5)["candidates"])==1
    for n in (-2, 0, 300):
        try:
            group_session_candidates(assets,n)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid session gap accepted")
