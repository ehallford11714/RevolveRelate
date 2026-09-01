"""Any catalogued DB: bind atoms from the schema, chain RelOps, ask causal/semantic questions."""

from __future__ import annotations

import sqlite3

from revolverelate.analytics.bind import bind_analytics_goal
from revolverelate.analytics.causal_plan import fallback_causal_plan, match_causal_composite
from revolverelate.revolverelate import RevolveRelate
from revolverelate.samples.superstore import write_superstore
from revolverelate.vector.overlay import OVERLAY


def write_disease_study(path):
    """Tiny public-style study table: text abstract + numeric cases + cohort."""
    dest = path
    conn = sqlite3.connect(str(dest))
    conn.executescript(
        """
        CREATE TABLE Disease (
            DiseaseId INTEGER PRIMARY KEY,
            DiseaseName TEXT,
            Abstract TEXT
        );
        CREATE TABLE Study (
            StudyId INTEGER PRIMARY KEY,
            DiseaseId INTEGER NOT NULL,
            Cases REAL,
            Exposure REAL,
            Cohort TEXT,
            FOREIGN KEY (DiseaseId) REFERENCES Disease(DiseaseId)
        );
        """
    )
    conn.executemany(
        "INSERT INTO Disease VALUES (?,?,?)",
        [
            (
                1,
                "Alpha fever",
                "Alpha fever is caused by a mutation. Smoking increases risk. Therefore cases rose in the adult cohort.",
            ),
            (
                2,
                "Beta rash",
                "Beta rash follows exposure. Because the toxin persisted, incidence stayed high.",
            ),
        ],
    )
    conn.executemany(
        "INSERT INTO Study VALUES (?,?,?,?,?)",
        [
            (1, 1, 40.0, 2.5, "adult"),
            (2, 1, 12.0, 0.4, "child"),
            (3, 2, 9.0, 1.1, "adult"),
        ],
    )
    conn.commit()
    conn.close()
    return dest


def test_superstore_goal_still_binds_known_columns(tmp_path):
    live = write_superstore(tmp_path / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build(rows_per_entity=4)
    bound = bind_analytics_goal(rr.schema, "why did West sales fall because discounting")
    assert bound["measure"] == "Sales"
    assert bound["dimension"] == "Category"
    assert bound["column"] == "ProductName"
    assert bound["slice"] == {"column": "Region", "value": "West"}
    rr.close()


def test_disease_db_binds_atoms_and_answers_what_causes(tmp_path):
    live = write_disease_study(tmp_path / "disease.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build(rows_per_entity=4)
    assert "Cases" in {a.name for e in rr.schema.all_entities() for a in e.attributes}
    bound = bind_analytics_goal(rr.schema, "what causes this disease")
    assert bound["measure"] == "Cases"
    assert bound["column"] in {"Abstract", "DiseaseName"}
    assert bound["dimension"] in {"DiseaseName", "Cohort", "Abstract"}
    assert match_causal_composite("what causes this disease") == "rag_causal_pair"
    plan = fallback_causal_plan("what causes this disease", rr.schema)
    assert plan["grammar"]["ok"]
    assert any(s.get("op") == "chunk_causal" and s.get("column") == bound["column"] for s in plan["steps"])
    assert any(s.get("op") == "knn" and "causes" in str(s.get("query") or "") for s in plan["steps"])
    ran = rr.analytics.run_chain(plan["steps"], plan_id="disease-causes")
    assert ran["status"] == "sandbox_ok"
    live_out = rr.replay_live(plan_id=ran["id"])
    assert live_out["ran"] is True
    blob = " ".join(
        str(v)
        for row in rr.sandbox.execute(f'SELECT Text, Cue, Role FROM "{OVERLAY}" WHERE Strategy = ?', ["causal"])[1]
        for v in row
    ).casefold()
    assert "because" in blob or "therefore" in blob or "caused" in blob
    causal = rr.causal("what causes this disease", live=True)
    assert causal["relop"]["status"] == "sandbox_ok"
    assert causal["goal"]["column"] in {"Abstract", "DiseaseName"}
    assert causal["live"]["ran"] is True
    rr.close()
