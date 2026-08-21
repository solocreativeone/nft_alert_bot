from commands import ETH_ADDRESS_PATTERN


def test_accepts_valid_addresses():
    assert ETH_ADDRESS_PATTERN.match("0x" + "a" * 40)
    assert ETH_ADDRESS_PATTERN.match("0xBC4CA0EdA7647A8aB7C2061c2E118A18a936f13D")


def test_rejects_wrong_length():
    assert not ETH_ADDRESS_PATTERN.match("0x" + "a" * 39)
    assert not ETH_ADDRESS_PATTERN.match("0x" + "a" * 41)


def test_rejects_missing_prefix():
    assert not ETH_ADDRESS_PATTERN.match("a" * 40)


def test_rejects_non_hex():
    assert not ETH_ADDRESS_PATTERN.match("0x" + "g" * 40)
