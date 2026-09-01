"""100 composite RelOp cases on Superstore. Deterministic algebra, not SQL."""

from __future__ import annotations


def col(entity: str, attr: str) -> dict:
    return {"expr": "col", "entity": entity, "attr": attr}


def lit(value) -> dict:
    return {"expr": "lit", "value": value}


def binop(op: str, left: dict, right: dict) -> dict:
    return {"expr": "bin", "op": op, "left": left, "right": right}


def item(entity: str, attr: str, alias: str | None = None) -> dict:
    row = {"expr": col(entity, attr)}
    if alias:
        row["alias"] = alias
    return row


def scan(entity: str) -> dict:
    return {"op": "scan", "entity": entity, "alias": entity}


def project(inp: dict, *items: dict) -> dict:
    return {"op": "project", "items": list(items), "input": inp}


def filt(inp: dict, pred: dict) -> dict:
    return {"op": "filter", "predicate": pred, "input": inp}


def join(left: dict, right: dict, *ons: dict, join_type: str = "inner") -> dict:
    return {"op": "join", "joinType": join_type, "left": left, "right": right, "on": list(ons)}


def on_eq(a_ent: str, a_attr: str, b_ent: str, b_attr: str) -> dict:
    return binop("=", col(a_ent, a_attr), col(b_ent, b_attr))


def agg(inp: dict, groups: list, *aggs: dict) -> dict:
    return {"op": "aggregate", "groups": groups, "aggs": list(aggs), "input": inp}


def sort(inp: dict, *keys: dict) -> dict:
    return {"op": "sort", "keys": list(keys), "input": inp}


def lim(inp: dict, n: int, offset: int | None = None) -> dict:
    op = {"op": "limit", "count": n, "input": inp}
    if offset:
        op["offset"] = offset
    return op


def q(op: dict) -> dict:
    return {"kind": "query", "op": op}


def star_join() -> dict:
    return join(
        join(
            join(scan("OrderLine"), scan("Orders"), on_eq("OrderLine", "OrderId", "Orders", "OrderId")),
            scan("Customer"),
            on_eq("Orders", "CustomerId", "Customer", "CustomerId"),
        ),
        scan("Product"),
        on_eq("OrderLine", "ProductId", "Product", "ProductId"),
    )


def _cases() -> list[dict]:
    cases: list[dict] = []

    def add(name: str, ir: dict, *, min_rows: int = 0, sql_has: str = "SELECT"):
        cases.append({"name": name, "ir": ir, "min_rows": min_rows, "sql_has": sql_has})

    # 1-12 filters
    add("filter_region_west", q(lim(project(filt(scan("Customer"), binop("=", col("Customer", "Region"), lit("West"))), item("Customer", "CustomerName")), 50)), min_rows=1)
    add("filter_region_south", q(project(filt(scan("Customer"), binop("=", col("Customer", "Region"), lit("South"))), item("Customer", "CustomerName"))), min_rows=1)
    add("filter_region_central", q(project(filt(scan("Customer"), binop("=", col("Customer", "Region"), lit("Central"))), item("Customer", "CustomerName"))), min_rows=1)
    add("filter_state_california", q(project(filt(scan("Customer"), binop("=", col("Customer", "State"), lit("California"))), item("Customer", "CustomerName"))), min_rows=1)
    add("filter_state_texas", q(project(filt(scan("Customer"), binop("=", col("Customer", "State"), lit("Texas"))), item("Customer", "CustomerName"))), min_rows=1)
    add("filter_segment_consumer", q(project(filt(scan("Customer"), binop("=", col("Customer", "Segment"), lit("Consumer"))), item("Customer", "Segment"))), min_rows=1)
    add("filter_segment_corporate", q(project(filt(scan("Customer"), binop("=", col("Customer", "Segment"), lit("Corporate"))), item("Customer", "Segment"))), min_rows=1)
    add("filter_neq_west", q(project(filt(scan("Customer"), binop("!=", col("Customer", "Region"), lit("West"))), item("Customer", "Region"))), min_rows=1)
    add("filter_city_seattle", q(project(filt(scan("Customer"), binop("=", col("Customer", "City"), lit("Seattle"))), item("Customer", "City"))), min_rows=1)
    add("filter_like_ca_order", q(project(filt(scan("Orders"), binop("like", col("Orders", "OrderCode"), lit("CA-%"))), item("Orders", "OrderCode"))), min_rows=1)
    add("filter_in_regions", q(project(filt(scan("Customer"), binop("in", col("Customer", "Region"), lit(["West", "East"]))), item("Customer", "Region"))), min_rows=1)
    add("filter_in_states", q(project(filt(scan("Customer"), binop("in", col("Customer", "State"), lit(["California", "Texas", "Utah"]))), item("Customer", "State"))), min_rows=1)

    # 13-24 boolean composites
    add("and_west_consumer", q(project(filt(scan("Customer"), binop("and", binop("=", col("Customer", "Region"), lit("West")), binop("=", col("Customer", "Segment"), lit("Consumer")))), item("Customer", "CustomerName"))), min_rows=1)
    add("or_west_central", q(project(filt(scan("Customer"), binop("or", binop("=", col("Customer", "Region"), lit("West")), binop("=", col("Customer", "Region"), lit("Central")))), item("Customer", "Region"))), min_rows=1)
    add("not_south", q(project(filt(scan("Customer"), {"expr": "un", "op": "not", "input": binop("=", col("Customer", "Region"), lit("South"))}), item("Customer", "Region"))), min_rows=1)
    add("and_or_nested", q(project(filt(scan("Customer"), binop("and", binop("or", binop("=", col("Customer", "Region"), lit("West")), binop("=", col("Customer", "Region"), lit("South"))), binop("=", col("Customer", "Country"), lit("United States")))), item("Customer", "CustomerName"))), min_rows=1)
    add("sales_gt_200", q(project(filt(scan("OrderLine"), binop(">", col("OrderLine", "Sales"), lit(200))), item("OrderLine", "Sales"))), min_rows=1)
    add("sales_ge_731", q(project(filt(scan("OrderLine"), binop(">=", col("OrderLine", "Sales"), lit(731.94))), item("OrderLine", "Sales"))), min_rows=1)
    add("sales_lt_20", q(project(filt(scan("OrderLine"), binop("<", col("OrderLine", "Sales"), lit(20))), item("OrderLine", "Sales"))), min_rows=1)
    add("sales_le_22_37", q(project(filt(scan("OrderLine"), binop("<=", col("OrderLine", "Sales"), lit(22.37))), item("OrderLine", "Sales"))), min_rows=1)
    add("profit_negative", q(project(filt(scan("OrderLine"), binop("<", col("OrderLine", "Profit"), lit(0))), item("OrderLine", "Profit"))), min_rows=1)
    add("qty_ge_5", q(project(filt(scan("OrderLine"), binop(">=", col("OrderLine", "Quantity"), lit(5))), item("OrderLine", "Quantity"))), min_rows=1)
    add("discount_gt_0", q(project(filt(scan("OrderLine"), binop(">", col("OrderLine", "Discount"), lit(0))), item("OrderLine", "Discount"))), min_rows=1)
    add("discount_eq_0", q(project(filt(scan("OrderLine"), binop("=", col("OrderLine", "Discount"), lit(0.0))), item("OrderLine", "Discount"))), min_rows=1)

    # 25-40 joins
    add("join_orders_customer", q(project(join(scan("Orders"), scan("Customer"), on_eq("Orders", "CustomerId", "Customer", "CustomerId")), item("Orders", "OrderCode"), item("Customer", "CustomerName"))), min_rows=1, sql_has="JOIN")
    add("join_line_orders", q(project(join(scan("OrderLine"), scan("Orders"), on_eq("OrderLine", "OrderId", "Orders", "OrderId")), item("OrderLine", "Sales"), item("Orders", "OrderCode"))), min_rows=1, sql_has="JOIN")
    add("join_line_product", q(project(join(scan("OrderLine"), scan("Product"), on_eq("OrderLine", "ProductId", "Product", "ProductId")), item("Product", "Category"), item("OrderLine", "Sales"))), min_rows=1, sql_has="JOIN")
    add("join_three_orders_cust_line", q(project(join(join(scan("OrderLine"), scan("Orders"), on_eq("OrderLine", "OrderId", "Orders", "OrderId")), scan("Customer"), on_eq("Orders", "CustomerId", "Customer", "CustomerId")), item("Customer", "CustomerName"), item("OrderLine", "Sales"))), min_rows=1, sql_has="JOIN")
    add("join_star_four", q(project(star_join(), item("Customer", "Region"), item("Product", "Category"), item("OrderLine", "Sales"))), min_rows=1, sql_has="JOIN")
    add("left_join_customer_orders", q(project(join(scan("Customer"), scan("Orders"), on_eq("Customer", "CustomerId", "Orders", "CustomerId"), join_type="left"), item("Customer", "CustomerName"), item("Orders", "OrderCode"))), min_rows=1, sql_has="LEFT JOIN")
    add("join_west_customers_orders", q(project(filt(join(scan("Orders"), scan("Customer"), on_eq("Orders", "CustomerId", "Customer", "CustomerId")), binop("=", col("Customer", "Region"), lit("West"))), item("Orders", "OrderCode"))), min_rows=1)
    add("join_california_lines", q(project(filt(star_join(), binop("=", col("Customer", "State"), lit("California"))), item("OrderLine", "Sales"), item("Customer", "City"))), min_rows=1)
    add("join_technology_sales", q(project(filt(join(scan("OrderLine"), scan("Product"), on_eq("OrderLine", "ProductId", "Product", "ProductId")), binop("=", col("Product", "Category"), lit("Technology"))), item("OrderLine", "Sales"), item("Product", "SubCategory"))), min_rows=1)
    add("join_furniture_west", q(project(filt(star_join(), binop("and", binop("=", col("Product", "Category"), lit("Furniture")), binop("=", col("Customer", "Region"), lit("West")))), item("Product", "ProductName"), item("Customer", "Region"))), min_rows=0)
    add("join_second_class", q(project(filt(join(scan("Orders"), scan("Customer"), on_eq("Orders", "CustomerId", "Customer", "CustomerId")), binop("=", col("Orders", "ShipMode"), lit("Second Class"))), item("Orders", "ShipMode"))), min_rows=1)
    add("join_standard_class", q(project(filt(join(scan("Orders"), scan("Customer"), on_eq("Orders", "CustomerId", "Customer", "CustomerId")), binop("=", col("Orders", "ShipMode"), lit("Standard Class"))), item("Orders", "ShipMode"))), min_rows=1)
    add("join_year_2016", q(project(filt(scan("Orders"), binop("like", col("Orders", "OrderDate"), lit("2016-%"))), item("Orders", "OrderDate"))), min_rows=1)
    add("join_year_2014", q(project(filt(scan("Orders"), binop("like", col("Orders", "OrderDate"), lit("2014-%"))), item("Orders", "OrderDate"))), min_rows=1)
    add("join_home_office", q(project(filt(join(scan("Orders"), scan("Customer"), on_eq("Orders", "CustomerId", "Customer", "CustomerId")), binop("=", col("Customer", "Segment"), lit("Home Office"))), item("Customer", "Segment"))), min_rows=1)
    add("join_phones", q(project(filt(join(scan("OrderLine"), scan("Product"), on_eq("OrderLine", "ProductId", "Product", "ProductId")), binop("=", col("Product", "SubCategory"), lit("Phones"))), item("Product", "SubCategory"))), min_rows=1)

    # 41-55 aggregates + having
    add("agg_count_customers", q(agg(scan("Customer"), [], {"expr": {"expr": "agg", "fn": "count", "input": {"expr": "star"}}, "alias": "n"})), min_rows=1)
    add("agg_count_by_region", q(agg(scan("Customer"), [col("Customer", "Region")], {"expr": {"expr": "agg", "fn": "count", "input": {"expr": "star"}}, "alias": "n"})), min_rows=1, sql_has="GROUP BY")
    add("agg_count_by_segment", q(agg(scan("Customer"), [col("Customer", "Segment")], {"expr": {"expr": "agg", "fn": "count", "input": {"expr": "star"}}, "alias": "n"})), min_rows=1)
    add("agg_sum_sales", q(agg(scan("OrderLine"), [], {"expr": {"expr": "agg", "fn": "sum", "input": col("OrderLine", "Sales")}, "alias": "sales"})), min_rows=1)
    add("agg_avg_sales", q(agg(scan("OrderLine"), [], {"expr": {"expr": "agg", "fn": "avg", "input": col("OrderLine", "Sales")}, "alias": "avg_sales"})), min_rows=1)
    add("agg_min_profit", q(agg(scan("OrderLine"), [], {"expr": {"expr": "agg", "fn": "min", "input": col("OrderLine", "Profit")}, "alias": "min_p"})), min_rows=1)
    add("agg_max_sales", q(agg(scan("OrderLine"), [], {"expr": {"expr": "agg", "fn": "max", "input": col("OrderLine", "Sales")}, "alias": "max_s"})), min_rows=1)
    add("agg_sum_sales_by_product", q(agg(join(scan("OrderLine"), scan("Product"), on_eq("OrderLine", "ProductId", "Product", "ProductId")), [col("Product", "Category")], {"expr": {"expr": "agg", "fn": "sum", "input": col("OrderLine", "Sales")}, "alias": "sales"})), min_rows=1, sql_has="GROUP BY")
    add("agg_sum_sales_by_region", q(agg(star_join(), [col("Customer", "Region")], {"expr": {"expr": "agg", "fn": "sum", "input": col("OrderLine", "Sales")}, "alias": "sales"})), min_rows=1)
    add("agg_sum_profit_by_category", q(agg(join(scan("OrderLine"), scan("Product"), on_eq("OrderLine", "ProductId", "Product", "ProductId")), [col("Product", "Category")], {"expr": {"expr": "agg", "fn": "sum", "input": col("OrderLine", "Profit")}, "alias": "profit"})), min_rows=1)
    add("agg_count_orders_by_ship", q(agg(scan("Orders"), [col("Orders", "ShipMode")], {"expr": {"expr": "agg", "fn": "count", "input": {"expr": "star"}}, "alias": "n"})), min_rows=1)
    add("having_region_count_ge_2", q(filt(agg(scan("Customer"), [col("Customer", "Region")], {"expr": {"expr": "agg", "fn": "count", "input": {"expr": "star"}}, "alias": "n"}), binop(">=", {"expr": "col", "attr": "n"}, lit(2)))), min_rows=1, sql_has="HAVING")
    add("having_category_sales_gt_100", q(filt(agg(join(scan("OrderLine"), scan("Product"), on_eq("OrderLine", "ProductId", "Product", "ProductId")), [col("Product", "Category")], {"expr": {"expr": "agg", "fn": "sum", "input": col("OrderLine", "Sales")}, "alias": "sales"}), binop(">", {"expr": "col", "attr": "sales"}, lit(100)))), min_rows=1, sql_has="HAVING")
    add("agg_qty_by_category", q(agg(join(scan("OrderLine"), scan("Product"), on_eq("OrderLine", "ProductId", "Product", "ProductId")), [col("Product", "Category")], {"expr": {"expr": "agg", "fn": "sum", "input": col("OrderLine", "Quantity")}, "alias": "qty"})), min_rows=1)
    add("agg_avg_discount_by_ship", q(agg(join(scan("OrderLine"), scan("Orders"), on_eq("OrderLine", "OrderId", "Orders", "OrderId")), [col("Orders", "ShipMode")], {"expr": {"expr": "agg", "fn": "avg", "input": col("OrderLine", "Discount")}, "alias": "d"})), min_rows=1)

    # 56-68 sort / limit / distinct
    add("sort_sales_desc", q(lim(sort(project(scan("OrderLine"), item("OrderLine", "Sales")), {"expr": col("OrderLine", "Sales"), "direction": "DESC"}), 5)), min_rows=1, sql_has="ORDER BY")
    add("sort_profit_asc", q(lim(sort(project(scan("OrderLine"), item("OrderLine", "Profit")), {"expr": col("OrderLine", "Profit"), "direction": "ASC"}), 5)), min_rows=1)
    add("sort_name", q(sort(project(scan("Customer"), item("Customer", "CustomerName")), {"expr": col("Customer", "CustomerName"), "direction": "ASC"})), min_rows=1)
    add("limit_3_customers", q(lim(project(scan("Customer"), item("Customer", "CustomerId")), 3)), min_rows=1)
    add("limit_offset_customers", q(lim(sort(project(scan("Customer"), item("Customer", "CustomerId")), {"expr": col("Customer", "CustomerId"), "direction": "ASC"}), 3, offset=2)), min_rows=1)
    add("distinct_regions", q({"op": "distinct", "input": project(scan("Customer"), item("Customer", "Region"))}), min_rows=1, sql_has="DISTINCT")
    add("distinct_categories", q({"op": "distinct", "input": project(scan("Product"), item("Product", "Category"))}), min_rows=1)
    add("distinct_shipmodes", q({"op": "distinct", "input": project(scan("Orders"), item("Orders", "ShipMode"))}), min_rows=1)
    add("sort_join_sales", q(lim(sort(project(star_join(), item("OrderLine", "Sales"), item("Customer", "CustomerName")), {"expr": col("OrderLine", "Sales"), "direction": "DESC"}), 5)), min_rows=1)
    add("limit_west", q(lim(project(filt(scan("Customer"), binop("=", col("Customer", "Region"), lit("West"))), item("Customer", "CustomerName")), 2)), min_rows=1)
    add("sort_orders_date", q(sort(project(scan("Orders"), item("Orders", "OrderDate"), item("Orders", "OrderCode")), {"expr": col("Orders", "OrderDate"), "direction": "DESC"})), min_rows=1)
    add("distinct_states", q({"op": "distinct", "input": project(scan("Customer"), item("Customer", "State"))}), min_rows=1)
    add("limit_technology", q(lim(project(filt(scan("Product"), binop("=", col("Product", "Category"), lit("Technology"))), item("Product", "ProductName")), 10)), min_rows=1)

    # 69-80 set ops
    west = project(filt(scan("Customer"), binop("=", col("Customer", "Region"), lit("West"))), item("Customer", "CustomerId", "id"))
    south = project(filt(scan("Customer"), binop("=", col("Customer", "Region"), lit("South"))), item("Customer", "CustomerId", "id"))
    central = project(filt(scan("Customer"), binop("=", col("Customer", "Region"), lit("Central"))), item("Customer", "CustomerId", "id"))
    furn = project(filt(scan("Product"), binop("=", col("Product", "Category"), lit("Furniture"))), item("Product", "ProductId", "id"))
    tech = project(filt(scan("Product"), binop("=", col("Product", "Category"), lit("Technology"))), item("Product", "ProductId", "id"))
    office = project(filt(scan("Product"), binop("=", col("Product", "Category"), lit("Office Supplies"))), item("Product", "ProductId", "id"))
    add("union_west_south", q({"op": "setop", "set": "union", "all": False, "left": west, "right": south}), min_rows=1, sql_has="UNION")
    add("union_all_west_south", q({"op": "setop", "set": "union", "all": True, "left": west, "right": south}), min_rows=1, sql_has="UNION ALL")
    add("union_west_central", q({"op": "setop", "set": "union", "left": west, "right": central}), min_rows=1)
    add("except_all_minus_west", q({"op": "setop", "set": "except", "left": project(scan("Customer"), item("Customer", "CustomerId", "id")), "right": west}), min_rows=1, sql_has="EXCEPT")
    add("intersect_all_and_west", q({"op": "setop", "set": "intersect", "left": project(scan("Customer"), item("Customer", "CustomerId", "id")), "right": west}), min_rows=1, sql_has="INTERSECT")
    add("union_furniture_tech", q({"op": "setop", "set": "union", "left": furn, "right": tech}), min_rows=1)
    add("union_all_three_cats", q({"op": "setop", "set": "union", "left": {"op": "setop", "set": "union", "left": furn, "right": tech}, "right": office}), min_rows=1)
    add("except_office_from_all", q({"op": "setop", "set": "except", "left": project(scan("Product"), item("Product", "ProductId", "id")), "right": office}), min_rows=1)
    add("intersect_tech_all", q({"op": "setop", "set": "intersect", "left": project(scan("Product"), item("Product", "ProductId", "id")), "right": tech}), min_rows=1)
    add("union_ca_and_us_orders", q({"op": "setop", "set": "union", "left": project(filt(scan("Orders"), binop("like", col("Orders", "OrderCode"), lit("CA-%"))), item("Orders", "OrderId", "id")), "right": project(filt(scan("Orders"), binop("like", col("Orders", "OrderCode"), lit("US-%"))), item("Orders", "OrderId", "id"))}), min_rows=1)
    add("except_second_class", q({"op": "setop", "set": "except", "left": project(scan("Orders"), item("Orders", "OrderId", "id")), "right": project(filt(scan("Orders"), binop("=", col("Orders", "ShipMode"), lit("Second Class"))), item("Orders", "OrderId", "id"))}), min_rows=1)
    add("union_seattle_la", q({"op": "setop", "set": "union", "left": project(filt(scan("Customer"), binop("=", col("Customer", "City"), lit("Seattle"))), item("Customer", "CustomerId", "id")), "right": project(filt(scan("Customer"), binop("=", col("Customer", "City"), lit("Los Angeles"))), item("Customer", "CustomerId", "id"))}), min_rows=1)

    # 81-90 CTE / with
    west_cte = {"name": "west_cust", "input": west}
    add("cte_west_scan", q({"op": "with", "ctes": [west_cte], "input": {"op": "scan", "entity": "west_cust", "alias": "west_cust"}}), min_rows=1, sql_has="WITH")
    add("cte_then_limit", q({"op": "with", "ctes": [west_cte], "input": lim(scan("west_cust"), 10)}), min_rows=1)
    high = {"name": "high_sales", "input": project(filt(scan("OrderLine"), binop(">", col("OrderLine", "Sales"), lit(200))), item("OrderLine", "LineId", "id"), item("OrderLine", "Sales", "sales"))}
    add("cte_high_sales", q({"op": "with", "ctes": [high], "input": scan("high_sales")}), min_rows=1)
    add("cte_two", q({"op": "with", "ctes": [west_cte, high], "input": scan("west_cust")}), min_rows=1)
    tech_cte = {"name": "tech", "input": tech}
    add("cte_tech", q({"op": "with", "ctes": [tech_cte], "input": scan("tech")}), min_rows=1)
    add("cte_union_body", q({"op": "with", "ctes": [west_cte], "input": {"op": "setop", "set": "union", "left": scan("west_cust"), "right": south}}), min_rows=1)
    add("cte_count", q({"op": "with", "ctes": [west_cte], "input": agg(scan("west_cust"), [], {"expr": {"expr": "agg", "fn": "count", "input": {"expr": "star"}}, "alias": "n"})}), min_rows=1)
    add("cte_sort", q({"op": "with", "ctes": [high], "input": sort(scan("high_sales"), {"expr": col("high_sales", "sales"), "direction": "DESC"})}), min_rows=1)

    # 91-96 windows
    add("window_rank_sales", q(project(scan("OrderLine"), item("OrderLine", "Sales"), {"expr": {"expr": "over", "fn": "rank", "order": [{"expr": col("OrderLine", "Sales"), "direction": "DESC"}]}, "alias": "rk"})), min_rows=1, sql_has="OVER")
    add("window_row_number", q(project(scan("Customer"), item("Customer", "CustomerName"), {"expr": {"expr": "over", "fn": "row_number", "order": [{"expr": col("Customer", "CustomerId"), "direction": "ASC"}]}, "alias": "rn"})), min_rows=1)
    add("window_sum_sales_by_order", q(project(scan("OrderLine"), item("OrderLine", "OrderId"), item("OrderLine", "Sales"), {"expr": {"expr": "over", "fn": "sum", "input": col("OrderLine", "Sales"), "partition": [col("OrderLine", "OrderId")]}, "alias": "order_sales"})), min_rows=1)
    add("window_dense_rank_profit", q(project(scan("OrderLine"), item("OrderLine", "Profit"), {"expr": {"expr": "over", "fn": "dense_rank", "order": [{"expr": col("OrderLine", "Profit"), "direction": "ASC"}]}, "alias": "dr"})), min_rows=1)
    add("window_rank_by_region_join", q(project(join(scan("Orders"), scan("Customer"), on_eq("Orders", "CustomerId", "Customer", "CustomerId")), item("Customer", "Region"), item("Orders", "OrderId"), {"expr": {"expr": "over", "fn": "rank", "partition": [col("Customer", "Region")], "order": [{"expr": col("Orders", "OrderId"), "direction": "ASC"}]}, "alias": "rk"})), min_rows=1)
    add("window_avg_sales", q(project(scan("OrderLine"), item("OrderLine", "Sales"), {"expr": {"expr": "over", "fn": "avg", "input": col("OrderLine", "Sales")}, "alias": "avg_all"})), min_rows=1)

    # 97-100 mixed composites
    add("composite_star_filter_agg_sort", q(lim(sort(agg(filt(star_join(), binop(">", col("OrderLine", "Sales"), lit(20))), [col("Customer", "Region"), col("Product", "Category")], {"expr": {"expr": "agg", "fn": "sum", "input": col("OrderLine", "Sales")}, "alias": "sales"}), {"expr": {"expr": "col", "attr": "sales"}, "direction": "DESC"}), 10)), min_rows=1)
    add("composite_or_join_limit", q(lim(project(filt(star_join(), binop("or", binop("=", col("Product", "Category"), lit("Technology")), binop("=", col("Customer", "Region"), lit("West")))), item("Product", "Category"), item("Customer", "Region"), item("OrderLine", "Sales")), 20)), min_rows=1)
    add("composite_having_after_star", q(filt(agg(star_join(), [col("Product", "Category")], {"expr": {"expr": "agg", "fn": "sum", "input": col("OrderLine", "Profit")}, "alias": "profit"}), binop(">", {"expr": "col", "attr": "profit"}, lit(-1000)))), min_rows=1, sql_has="HAVING")
    add("composite_union_then_sort", q(sort({"op": "setop", "set": "union", "left": west, "right": south}, {"expr": {"expr": "col", "attr": "id"}, "direction": "ASC"})), min_rows=1)
    add("composite_and_sales_qty", q(project(filt(scan("OrderLine"), binop("and", binop(">", col("OrderLine", "Sales"), lit(50)), binop(">=", col("OrderLine", "Quantity"), lit(2)))), item("OrderLine", "Sales"), item("OrderLine", "Quantity"))), min_rows=1)
    add("composite_join_filter_sort_limit", q(lim(sort(project(filt(join(scan("OrderLine"), scan("Product"), on_eq("OrderLine", "ProductId", "Product", "ProductId")), binop("!=", col("Product", "Category"), lit("Labels"))), item("Product", "Category"), item("OrderLine", "Sales")), {"expr": col("OrderLine", "Sales"), "direction": "DESC"}), 8)), min_rows=1)

    return cases


CASES = _cases()

assert len(CASES) == 100, len(CASES)

SLM_QUESTIONS = [
    "customers in West",
    "customers in South",
    "customers in Central",
    "customers in California",
    "customers in Texas",
    "customers in Washington",
    "customers in Florida",
    "customers in Kentucky",
    "corporate customers",
    "consumer customers",
    "home office customers",
    "orders in California",
    "orders in Texas",
    "orders in West",
    "orders in South",
    "second class orders",
    "standard class orders",
    "orders from 2016",
    "orders from 2014",
    "orders from 2015",
    "orderlines over 500",
    "orderlines over 200",
    "orderlines over 100",
    "orderlines over 20",
    "sales greater than 700",
    "profit less than 0",
    "quantity more than 4",
    "products in Technology",
    "products in Furniture",
    "products in Office Supplies",
    "phones products",
    "chairs products",
    "binders products",
    "bookcases products",
    "customers in Los Angeles",
    "customers in Seattle",
    "customers in San Francisco",
    "west consumer customers",
    "south consumer customers",
    "technology sales",
    "furniture sales",
    "office supplies sales",
    "west furniture orders",
    "california technology sales",
    "count customers by region",
    "count customers by segment",
    "sum sales by category",
    "sum sales by region",
    "average sales",
    "max sales",
    "min profit",
    "orders joined to customers",
    "orderlines joined to products",
    "orderlines joined to orders",
    "customers left joined to orders",
    "distinct regions",
    "distinct categories",
    "distinct ship modes",
    "top sales lines",
    "customers named Claire",
    "customers named Darrin",
    "utah customers",
    "wisconsin customers",
    "north carolina customers",
    "concord customers",
    "madison customers",
    "fort worth customers",
    "henderson customers",
    "west jordan customers",
    "fort lauderdale customers",
    "accessories products",
    "storage products",
    "tables products",
    "labels products",
    "2017 orders",
    "us orders",
    "ca orders",
    "high discount lines",
    "zero discount lines",
    "quantity 9 lines",
    "profit over 200",
    "sales over 900",
    "sales over 2500",
    "west corporate customers",
    "central home office customers",
    "technology in west",
    "furniture in south",
    "phones in california",
    "chairs in west",
    "orders with customer Claire",
    "orders with customer Irene",
    "orderlines for bookcases",
    "orderlines for chairs",
    "orderlines for phones",
    "orderlines for binders",
    "union of west and south customers",
    "customers not in West",
    "products not furniture",
    "orders not second class",
    "seattle or los angeles customers",
]

assert len(SLM_QUESTIONS) == 100, len(SLM_QUESTIONS)
