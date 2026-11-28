"""Native world generation, phase 6 (#44): the Greywater demo lives on a generated region, and moves there."""
import json
import uuid

import pytest

from atlas.sample_world import DEMO_REGION, generate
from atlas.service import GREYWATER_PLACES, map_documents


def test_the_demo_region_is_a_generated_region():
    w = generate(1795, **DEMO_REGION)
    assert w["info"]["mesh"] == "voronoi" and "region" in w["info"]["description"]
    assert w["info"]["mapName"] == "Thessaly Reach"
    assert any(r["group"] == "searoutes" for r in w["pack"]["routes"]) or len(w["pack"]["routes"]) > 100


def test_a_map_takes_what_it_brought_and_what_points_at_it():
    docs = [
        {"@id": "WorldAtlas/old", "@type": "WorldAtlas"},
        {"@id": "Province/old-p1", "@type": "Province", "source_session": "import/azgaar-old"},
        {"@id": "Faction/old-s1", "@type": "Faction", "source_session": "import/azgaar-old"},
        {"@id": "Location/greywater", "@type": "Location", "province": "Province/old-p1", "source_session": "session-13"},
        {"@id": "RelationshipEdge/v", "@type": "RelationshipEdge", "from_id": "Faction/house-vane", "to_id": "Faction/old-s1"},
        {"@id": "Location/spot-1", "@type": "Location", "province": "Province/old-p1"},        # a zoom-in spot
        {"@id": "Map/m1", "@type": "Map", "place": "Location/spot-1"},                         # ...and its map
        {"@id": "EpistemicEdge/k", "@type": "EpistemicEdge", "agent_id": "Faction/party", "fact_or_event_id": "Location/spot-1"},
        {"@id": "Character/sela", "@type": "Character", "location": "Location/greywater"},   # untouched
    ]
    drop = map_documents(docs, ["old"])
    assert drop == {"WorldAtlas/old", "Province/old-p1", "Faction/old-s1", "RelationshipEdge/v", "Location/spot-1",
                    "Map/m1", "EpistemicEdge/k"}
    assert "Location/greywater" in GREYWATER_PLACES  # kept: the new map re-places it


def test_greywater_moves_from_the_old_planet_to_a_region(tmp_path, monkeypatch):
    from atlas.sample_world import generate as gen
    from atlas.service import import_world, move_demo_atlas
    from config import Settings, get_settings
    from graph.seed import seed
    from graph.store import GraphStore
    from graph.world import WorldView
    monkeypatch.setenv("ATLAS_DIR", str(tmp_path / "atlas"))
    get_settings.cache_clear()
    st = GraphStore(Settings(terminusdb_db=f"lwe_test_{uuid.uuid4().hex[:8]}", atlas_dir=str(tmp_path / "atlas"),
                             mock_llm=True))
    try:
        st.ping()
    except Exception:
        pytest.skip("TerminusDB not reachable")
    try:
        seed(st, reset=True)
        old = tmp_path / "old.json"
        old.write_text(json.dumps(gen(3)), encoding="utf-8")  # the old whole-planet sample
        import_world(st, old, slug="thessaly-reach")
        before = {d["@id"]: d for d in st.all_documents("main")}
        old_province = before["Location/greywater"]["province"]
        st.upsert([{"@type": "Location", "@id": "Location/spot-x", "uid": "spot-x", "name": "A spot", "in_world_start": 0,
                    "province": old_province, "lon": 1.0, "lat": 1.0}], "main", "test: a zoom-in spot on the old map")
        dry = move_demo_atlas(st, dry_run=True)
        assert dry["old_maps"] == ["thessaly-reach"] and dry["remove"] > 100 and "imported" not in dry
        out = move_demo_atlas(st)
        assert out["imported"]["greywater"]  # placed on a port of the region
        docs = st.all_documents("main")
        by = {d["@id"]: d for d in docs}
        assert old_province not in by and "Location/spot-x" not in by
        gw = by["Location/greywater"]
        assert gw["province"] in by and by[gw["province"]]["@type"] == "Province" and gw["province"] != old_province
        assert all(by[p]["province"] == gw["province"] for p in GREYWATER_PLACES if p in by)
        # the demo's own story is untouched, and House Vane is a vassal of a realm of the new map
        assert sum(d.get("source_session") == "session-13" for d in docs) == \
            sum(d.get("source_session") == "session-13" for d in before.values())
        w = WorldView(docs, 10)
        vassal = [e for e in w.of_type("RelationshipEdge") if e["from_id"] == "Faction/house-vane" and e["label"] == "vassal"]
        assert vassal and all(e["to_id"] in by for e in vassal)
        atlas = [d for d in docs if d["@type"] == "WorldAtlas"]
        assert len(atlas) == 1 and atlas[0]["@id"] == "WorldAtlas/thessaly-reach"
    finally:
        st._root().delete_database(st.db, team=st.team, force=True)
        get_settings.cache_clear()
