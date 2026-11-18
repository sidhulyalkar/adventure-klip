"""Description pass: decoding, storage, concept peaks, activity proposals, and use in ranking.

Real ffmpeg on a generated video; stand-in encoders with known behaviour so the
test can say where each concept must peak. The real SigLIP2 and CLAP calls are
the same methods (images, windows, texts) checked separately against random-
weight models.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import numpy as np
import pytest

from adventure_intelligence import moment_evidence as me
from adventure_intelligence import value_eval, value_features as features
from adventure_intelligence import value_pairs as pairs_mod

from test_value_learning import owner_ledger, sha, write_experiment

COLORS = {"red": (230, 20, 20), "blue": (20, 20, 230), "green": (20, 230, 20)}


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg required")
    path = tmp_path_factory.mktemp("v") / "clip.mp4"
    frames = b"".join(bytes(COLORS[c]) * (64 * 36) for c in ("red", "blue", "green")
                      for _ in range(40))  # 4 s of each colour at 10 fps
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                    "-s", "64x36", "-r", "10", "-i", "-", "-f", "lavfi", "-i", "sine=f=300:d=12",
                    "-shortest", "-g", "10", "-keyint_min", "10", "-sc_threshold", "0",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], input=frames, check=True)
    return path


class FakeImage:
    model_id, revision, scale, bias = "fake-image", "r1", 20.0, -10.0

    def images(self, frames):
        out = np.zeros((len(frames), 8), np.float32)
        for i, f in enumerate(frames):
            out[i, int(np.argmax(f.reshape(-1, 3).mean(axis=0)))] = 1.0
        return out

    def texts(self, texts):
        out = np.zeros((len(texts), 8), np.float32)
        for i, t in enumerate(texts):
            if "turtle" in t:
                out[i, 0] = 1.0          # turtles look red here
            elif "reef" in t:
                out[i, 2] = 1.0          # reefs look blue (channel 2)
            elif "skiing on a snowy slope" in t:
                out[i, 1] = 1.0          # ski looks green (channel 1)
            else:
                out[i, 3 + i % 5] = 1.0
        return out


class FakeAudio:
    model_id, revision, scale = "fake-audio", "a1", 10.0

    def windows(self, clips):
        out = np.zeros((len(clips), 4), np.float32)
        for i, c in enumerate(clips):
            out[i, 0 if np.abs(c).mean() > 0.01 else 1] = 1.0
        return out

    def texts(self, texts):
        out = np.zeros((len(texts), 4), np.float32)
        for i, t in enumerate(texts):
            out[i, 0 if "laughing" in t else 1 if t == "silence" else 2] = 1.0
        return out


def test_describe_stores_embeddings_and_resumes(video, tmp_path):
    store = tmp_path / "evidence"
    first = me.describe(sha("v"), video, store, image=FakeImage(), audio=FakeAudio(), step_s=2.0)
    assert first["frames"] == 6 and first["audioWindows"] == 6
    with np.load(store / sha("v") / "frames.npz") as f:
        assert f["times"].tolist() == [0, 2000, 4000, 6000, 8000, 10000]
        assert f["embeddings"].dtype == np.float16
    again = me.describe(sha("v"), video, store, image=FakeImage(), audio=FakeAudio(), step_s=2.0)
    assert again["skipped"] is True
    redo = me.describe(sha("v"), video, store, image=FakeImage(), audio=FakeAudio(), step_s=4.0)
    assert "skipped" not in redo and redo["frames"] == 3


def test_concepts_peak_where_they_appear_and_vocabulary_changes_need_no_footage(video, tmp_path):
    store = tmp_path / "evidence"
    me.describe(sha("v"), video, store, image=FakeImage(), audio=FakeAudio())
    me.encode_vocabulary(store, me.vocabulary(), image=FakeImage(), audio=FakeAudio())
    s = me.Store(store)
    whole = s.interval(sha("v"), 0, 12000)
    assert whole["concepts"]["see:a sea turtle"] > 0.99 and whole["peaksMs"]["see:a sea turtle"] < 4000
    assert 4000 <= whole["peaksMs"]["see:a coral reef underwater"] < 8000
    assert whole["concepts"]["hear:people laughing"] > 0.5  # the tone is "not silence"
    late = s.interval(sha("v"), 8000, 12000)
    assert late["concepts"]["see:a sea turtle"] < 0.01
    votes = s.activity(sha("v"))["votes"]
    assert votes["snorkeling"] == pytest.approx(2 / 6, abs=1e-3)  # the two blue frames
    assert s.activity(sha("v"))["isModelProposalNotOwnerJudgment"] is True
    # A new owner term: re-encode the vocabulary only; the stored footage evidence is reused.
    me.encode_vocabulary(store, me.vocabulary(["a sea turtle close up"]), image=FakeImage(), audio=None)
    s2 = me.Store(store)
    assert "see:a sea turtle close up" in s2.interval(sha("v"), 0, 4000)["concepts"]


def _plant_store(folder, made, *, concept="see:a sea turtle"):
    """Evidence where only valuable segments show the concept; nothing else differs."""
    vocab = me.vocabulary()
    folder.mkdir()
    k = vocab["see"].index(concept)
    see = np.eye(len(vocab["see"]), 64, dtype=np.float32)
    np.savez(folder / "vocabulary.npz", see=see, activity=np.eye(len(vocab["activity"]), 64,
             k=len(vocab["see"]) % 32, dtype=np.float32), hear=np.zeros((0, 4), np.float32))
    (folder / "vocabulary.json").write_text(json.dumps({
        "version": me.VERSION, "vocabulary": vocab, "digest": me.vocabulary_digest(vocab),
        "imageModel": "fake", "imageScale": 20.0, "imageBias": -10.0, "audioModel": None,
        "audioScale": None}))
    rng = np.random.default_rng(1)
    for source in made["shas"]:
        times, emb = [], []
        for (s_, start, end), keep in made["truth"].items():
            if s_ != source:
                continue
            for t in range(start, end, 2000):
                v = rng.normal(0, 0.05, 64).astype(np.float32)
                v[k] += 1.0 if keep else 0.0
                v[63] += 1.0
                times.append(t)
                emb.append(v / np.linalg.norm(v))
        (folder / source).mkdir()
        np.savez(folder / source / "frames.npz", times=np.array(times), embeddings=np.array(emb, np.float16))
        (folder / source / "meta.json").write_text(json.dumps({"imageModel": "fake", "audioModel": None}))


def test_full_model_learns_a_value_only_visible_as_a_concept(tmp_path):
    made = write_experiment(tmp_path / "exp", outings=6, value_in="none")
    _plant_store(tmp_path / "evidence", made)
    store = me.Store(tmp_path / "evidence")
    sources = features.load_experiment(tmp_path / "exp")
    assert features.attach_described(sources, store) == len(made["shas"])

    def group_of(s):
        return made["mapping"][s]

    pool = features.segment_intervals(sources)
    items = features.build_items(sources, pool, group_of, evidence=store)
    first = next(iter(items.values()))
    assert first["concepts"] and first["peaksMs"]["see:a sea turtle"] >= first["startMs"]
    rows = owner_ledger(made["truth"])
    report = value_eval.evaluate(items, pairs_mod.from_ledger(rows, group_of), rows,
                                 pool_ids={features.item_id(*p) for p in pool}, roster_confirmed=True)
    acc = report["pairAccuracy"]
    assert acc["head_full"]["macro"] > 0.9
    assert acc["audio_peak_rel"]["macro"] < 0.7 and acc["motion_mean"]["macro"] < 0.7


def test_describe_command_skips_accidental_and_outside_recordings(video, tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    from adventure_intelligence import catalog, outing_roster as roster
    spec = importlib.util.spec_from_file_location(
        "cli", Path(__file__).parents[1] / "scripts" / "klip_value_experiment.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(me, "ImageEncoder", lambda model: FakeImage())
    monkeypatch.setattr(me, "AudioEncoder", lambda model: FakeAudio())
    ws, ssd, elsewhere = tmp_path / "ws", tmp_path / "ssd", tmp_path / "other"
    ssd.mkdir()
    elsewhere.mkdir()
    for d, n in ((ssd, "a"), (ssd, "b"), (elsewhere, "c")):
        shutil.copy(video, d / f"{n}.mp4")
    with catalog.connect(ws) as db:
        for i, (d, n) in enumerate(((ssd, "a"), (ssd, "b"), (elsewhere, "c"))):
            db.execute("INSERT INTO assets (id,sha256,stored_path,size_bytes,suffix,duration,"
                       "capture_time,activity) VALUES (?,?,?,?,?,?,?,?)",
                       (sha(n), sha(n), str(d / f"{n}.mp4"), 1, ".mp4", 12.0,
                        f"2026-01-02T09:0{i}:00", "unknown"))
    doc = roster.propose(roster.catalog_rows(ws))
    for o in doc["outings"]:
        for r in o["sources"]:
            if r["sourceSha256"] == sha("b"):
                r["activity"] = "accidental"
    (tmp_path / "roster.json").write_text(json.dumps(doc))
    assert cli.main(["describe", "--workspace", str(ws), "--source-root", str(ssd),
                     "--roster", str(tmp_path / "roster.json")]) == 0
    store = ws / "reports" / "evidence"
    assert (store / sha("a") / "meta.json").is_file()
    assert not (store / sha("b")).exists() and not (store / sha("c")).exists()
    (tmp_path / "terms.txt").write_text("my blue kayak\n")
    assert cli.main(["describe", "--workspace", str(ws), "--source-root", str(ssd),
                     "--terms", str(tmp_path / "terms.txt"), "--vocabulary-only"]) == 0
    assert "see:my blue kayak" in json.loads((store / "vocabulary.json").read_text())["vocabulary"]["see"]
