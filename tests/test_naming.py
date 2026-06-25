from wbs_sync.naming import assign_slugs, slugify


def test_slugify_special_chars():
    assert slugify("R&D / Engineering") == "r_d_engineering"
    assert slugify("Sales & Marketing") == "sales_marketing"
    assert slugify("QA") == "qa"
    assert slugify("  IT/Dev  ") == "it_dev"
    assert slugify("A.B/C") == "a_b_c"


def test_slugify_empty_or_punct_only():
    assert slugify("") == "unnamed"
    assert slugify("///") == "unnamed"
    assert slugify("   ") == "unnamed"


def test_slugify_lowercases_and_collapses_runs():
    assert slugify("Planning   &   Design") == "planning_design"


def test_assign_slugs_unique_for_duplicate_names():
    assert assign_slugs(["QA", "QA", "Sales"]) == ["qa", "qa_2", "sales"]


def test_assign_slugs_same_slug_different_names():
    assert assign_slugs(["R&D", "R D", "QA"]) == ["r_d", "r_d_2", "qa"]


def test_assign_slugs_no_collision():
    assert assign_slugs(["Sales & Marketing", "IT/Dev"]) == ["sales_marketing", "it_dev"]
