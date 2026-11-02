from nspd_test.address_normalization import (
    _street, build_search_variants, cadastral_quarter, normalize_address,
)


def test_moscow_address_and_street_fix():
    address = "Российская Федерация, г. Москва, вн.тер.г. муниципальный округ Ясенево, ул. Профсоюзная, д. 123А стр. 15"
    assert _street(address) == "профсоюзная"
    assert normalize_address(region="Москва", locality="Москва", address=address) == "москва профсоюзная 123а строение 15"


def test_street_suffix_and_abbreviations():
    assert _street("Шереметьевское шоссе 37") == "шереметьевское"
    assert _street("Москва, пр-т Ленинский, дом 10") == "ленинский"
    assert _street("Москва, Профсоюзная, дом 123А") == ""


def test_variants_are_unique_and_quarter_is_derived():
    variants = build_search_variants(region="Московская область", locality="Химки", address="Шереметьевское шоссе 37")
    assert variants[1] == (2, "московская химки шереметьевское 37")
    assert len({query for _, query in variants}) == len(variants)
    assert cadastral_quarter("31:16:0101001:25") == "31:16:0101001"

