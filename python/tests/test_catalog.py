from revolverelate.catalog import get_engine, list_engines


def test_catalog_loads_from_spec():
    engines = list_engines()
    assert len(engines) >= 100
    pg = get_engine("postgresql")
    assert pg.emit_family == "postgres"
    assert pg.execute_tier == "A"
    sf = get_engine("snowflake")
    assert sf.connection_family == "snowflake"
    assert get_engine("postgres").id == "postgresql"
