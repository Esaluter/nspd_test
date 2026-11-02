from nspd_test.candidate_filter import filter_candidates_with_reasons


def candidate(number, address):
    return {"cadastral_number": number, "address": address}


def test_filter_requires_geo_street_house_and_qualifier():
    items = [
        candidate("31:16:1:1", "Белгородская область, Белгород, ул Садовая, д 5, корп 2"),
        candidate("31:16:1:2", "Белгородская область, Белгород, ул Садовая, д 5, корп 3"),
        candidate("46:00:1:3", "Курская область, Курск, ул Садовая, д 5, корп 2"),
    ]
    accepted, reasons, rejected = filter_candidates_with_reasons(items, region="Белгородская область", locality="Белгород", address="ул. Садовая, д. 5, корпус 2")
    assert [x["cadastral_number"] for x in accepted] == ["31:16:1:1"]
    assert reasons == {"корпус": 1, "регион": 1}
    assert len(rejected) == 2

