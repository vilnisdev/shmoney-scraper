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


def test_street_suffix_normalized():
    assert canonical_key("Noble", "3314 Elgin Avenue") == canonical_key(
        "Noble", "3314 Elgin Ave"
    )
    assert canonical_key("Biz", "1 Main Street") == canonical_key("Biz", "1 Main St")
    assert canonical_key("Biz", "1 Park Road") == canonical_key("Biz", "1 Park Rd")
    assert canonical_key("Biz", "1 Hill Boulevard") == canonical_key(
        "Biz", "1 Hill Blvd"
    )
    assert canonical_key("Biz", "1 Oak Drive") == canonical_key("Biz", "1 Oak Dr")
    assert canonical_key("Biz", "1 Elm Lane") == canonical_key("Biz", "1 Elm Ln")
    assert canonical_key("Biz", "1 High Highway") == canonical_key("Biz", "1 High Hwy")


def test_suite_and_hash_normalized():
    assert canonical_key("Biz", "10451 Twin Rivers Rd, #132") == canonical_key(
        "Biz", "10451 Twin Rivers Road Suite 132"
    )
    assert canonical_key("Biz", "1 Main St Apartment 2") == canonical_key(
        "Biz", "1 Main St Apt 2"
    )


def test_trailing_zip_dash_stripped():
    assert canonical_key("Biz", "1 Main St, Baltimore MD 21216-") == canonical_key(
        "Biz", "1 Main St, Baltimore MD 21216"
    )


def test_name_does_not_get_address_suffix_collapse():
    # A business literally named "Avenue Cafe" must not collapse to "Ave Cafe".
    # If it did, "Avenue Cafe" at 1 Main St would collide with "Ave Cafe".
    assert canonical_key("Avenue Cafe", "1 Main St") != canonical_key(
        "Ave Cafe", "1 Main St"
    )
