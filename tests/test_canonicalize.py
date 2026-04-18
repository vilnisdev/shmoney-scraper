from shmoney.canonicalize import canonical_key


def test_same_inputs_same_key():
    assert canonical_key("Joe's Pizza", "123 Main St") == canonical_key(
        "Joe's Pizza", "123 Main St"
    )


def test_case_insensitive():
    assert canonical_key("JOE'S PIZZA", "123 main st") == canonical_key(
        "joe's pizza", "123 MAIN ST"
    )


def test_llc_suffix_stripped():
    assert canonical_key("Acme LLC", "1 Baltimore Ave") == canonical_key(
        "Acme", "1 Baltimore Ave"
    )


def test_inc_suffix_stripped():
    assert canonical_key("Acme Inc.", "1 Baltimore Ave") == canonical_key(
        "Acme", "1 Baltimore Ave"
    )


def test_different_names_different_keys():
    assert canonical_key("Joe's Pizza", "123 Main St") != canonical_key(
        "Sam's Pizza", "123 Main St"
    )


def test_different_addresses_different_keys():
    assert canonical_key("Joe's Pizza", "123 Main St") != canonical_key(
        "Joe's Pizza", "456 Main St"
    )


def test_key_is_sha1_hex():
    k = canonical_key("Joe's", "1 Main")
    assert len(k) == 40
    assert all(c in "0123456789abcdef" for c in k)
