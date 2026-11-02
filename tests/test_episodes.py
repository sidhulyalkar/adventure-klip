from adventure_intelligence.episodes import build_episodes


def ev(kind,start,idx,score=0.8,evidence_json="{}"):
    return {"id":f"e{idx}","kind":kind,"start_ms":start,"end_ms":start+1000,
            "score":score,"confidence":1.0,"evidence_json":evidence_json}


def test_jump_chain_and_carving_are_distinct_overlapping_hierarchies():
    events=[ev("jump",0,1),ev("jump",5000,2),ev("jump",9500,3),
            ev("turn",13000,4),ev("turn",15500,5),ev("turn",18100,6),ev("turn",20600,7)]
    episodes=build_episodes("a",events,24000)
    kinds={e["kind"] for e in episodes}
    assert "jump_chain" in kinds
    assert "carving_flow" in kinds
    assert "recording_summary" in kinds
    assert len(next(e for e in episodes if e["kind"]=="jump_chain")["event_ids"])==3


def test_no_false_jump_labels_from_generic_motion():
    events=[ev("visual_activity",0,1),ev("visual_activity",2600,2)]
    kinds={e["kind"] for e in build_episodes("a",events,9000)}
    assert "active_sequence" in kinds
    assert "jump_chain" not in kinds
    assert "carving_flow" not in kinds


def test_subject_identity_prevents_mixing_different_animals():
    events=[ev("animal_presence",0,1,evidence_json='{"subject_id":"manta-A"}'),
            ev("animal_presence",5000,2,evidence_json='{"subject_id":"shark-B"}'),
            ev("animal_presence",9000,3,evidence_json='{"subject_id":"manta-A"}')]
    episodes=build_episodes("a",events,12000)
    wildlife=[e for e in episodes if e["kind"]=="wildlife_encounter"]
    assert len(wildlife)==1
    assert wildlife[0]["event_ids"]==["e1","e3"]


def test_turn_interval_must_be_consistent():
    events=[ev("turn",t,i) for i,t in enumerate([0,2000,4000,35000],1)]
    assert "carving_flow" not in {x["kind"] for x in build_episodes("a",events,40000)}
