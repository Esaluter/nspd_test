import json
from pathlib import Path

import pytest

from nspd_test.address_normalization import split_full_address
from nspd_test.candidate_filter import candidate_rejection_reason, filter_candidates_with_reasons
from nspd_test.client import extract_candidates
from nspd_test.search import CadastralSearchService


DIAGNOSTIC_JSON = Path(__file__).parents[1] / "diagnostics" / "nspd_response_20261102T095335_001.json"


def candidate(number="16:50:010208:25", address="Республика Татарстан, г. Казань, ул. Баумана, д. 52", category="Здания", **extra):
    return {
        "cadastral_number": number,
        "address": address,
        "category": "36369",
        "category_name": category,
        "building_name": "",
        "purpose": "",
        "permitted_use": "",
        "cadastral_quarter": "",
        **extra,
    }


def reason(item, source="ул. Баумана 52", region="Республика Татарстан", locality="Казань"):
    return candidate_rejection_reason(item, region=region, locality=locality, address=source)


def test_web_address_is_split_into_bot2_fields_without_duplication():
    assert split_full_address("Казань, ул. Баумана 52") == ("", "Казань", "ул. Баумана 52")
    assert split_full_address("Республика Татарстан, Казань, ул. Баумана 52") == (
        "Республика Татарстан", "Казань", "ул. Баумана 52"
    )
    assert split_full_address("Москва, Профсоюзная улица 123") == (
        "Москва", "Москва", "Профсоюзная улица 123"
    )


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        (candidate(address="Самарская область, г. Казань, ул. Баумана, д. 52"), "регион"),
        (candidate(address="Республика Татарстан, г. Зеленодольск, ул. Баумана, д. 52"), "населённый пункт"),
        (candidate(address="Республика Татарстан, г. Казань, ул. Кремлёвская, д. 52"), "улица"),
        (candidate(address="Республика Татарстан, г. Казань, ул. Баумана, д. 36, кв. 52"), "дом"),
        (candidate(address="Республика Татарстан, г. Казань, ул. Баумана, д. 9, кв. 52"), "дом"),
        (candidate(address="Республика Татарстан, г. Казань, ул. Баумана, д. 52/7"), "дом"),
    ],
)
def test_original_rejection_order_and_special_cases(item, expected):
    assert reason(item) == expected


def test_exact_fractional_house_corpus_and_building():
    exact = candidate(address="Республика Татарстан, г. Казань, ул. Баумана, д. 52/7")
    assert reason(exact, source="ул. Баумана 52/7") is None
    assert reason(candidate(address="Республика Татарстан, г. Казань, ул. Баумана, д. 52, корп. 3"), source="ул. Баумана, д. 52, корп. 2") == "корпус"
    assert reason(candidate(address="Республика Татарстан, г. Казань, ул. Баумана, д. 52, стр. 3"), source="ул. Баумана, д. 52, стр. 2") == "строение"


def test_categories_are_preserved_and_not_artificially_filtered():
    items = [
        candidate("16:50:1:1", category="Здания"),
        candidate("16:50:1:2", category="Помещения"),
        candidate("16:50:1:3", category="Земельные участки ЕГРН"),
        candidate("16:50:1:4", category="Сооружения"),
    ]
    accepted, reasons, _ = filter_candidates_with_reasons(items, region="Республика Татарстан", locality="Казань", address="ул. Баумана 52")
    assert [item["category_name"] for item in accepted] == ["Здания", "Помещения", "Земельные участки ЕГРН", "Сооружения"]
    assert reasons == {}


def diagnostic_candidates():
    document = json.loads(DIAGNOSTIC_JSON.read_text(encoding="utf-8"))
    return extract_candidates(document["response"]["data"]["features"])


def test_real_diagnostic_json_matches_bot2_eight_received_zero_accepted():
    items = diagnostic_candidates()
    accepted, reasons, rejected = filter_candidates_with_reasons(items, region="Республика Татарстан", locality="Казань", address="ул. Баумана 52")
    assert len(items) == 8
    assert accepted == []
    assert reasons == {"дом": 8}
    assert len(rejected) == 8
    assert [item.score for item in rejected] == [5, 4, 3, 2, 1, 0, 0, 0]
    assert all(item.checks["house"] == "ОШИБКА" for item in rejected)


def test_score_is_retained_but_does_not_override_address_filter():
    items = diagnostic_candidates()
    assert [item["score"] for item in items] == [5, 4, 3, 2, 1, 0, 0, 0]
    assert items[0]["cadastral_quarter"] == ""  # same extraction stage as Bot 2
    accepted, _, rejected = filter_candidates_with_reasons(items, region="Республика Татарстан", locality="Казань", address="ул. Баумана 52")
    assert accepted == [] and rejected[0].score == 5


class Responses:
    def __init__(self, values):
        self.values = list(values)
        self.calls = []
    def search(self, query, **kwargs):
        self.calls.append((query, kwargs["region"], kwargs["locality"], kwargs["address"]))
        return self.values.pop(0)


def test_multiple_numbers_are_ambiguous_without_first_selection():
    searcher = Responses([[candidate("16:50:1:1"), candidate("16:50:1:2", category="Помещения")]])
    result = CadastralSearchService(searcher, attempts=1, request_delay=0).run("Республика Татарстан, Казань, ул. Баумана 52")
    assert result.status == "НЕОДНОЗНАЧНО"
    assert result.candidates_accepted == 2
    assert [item["cadastral_number"] for item in result.candidates] == ["16:50:1:1", "16:50:1:2"]
    assert result.ambiguity_reason


def test_each_variant_is_filtered_against_original_address_and_rejections_are_deduplicated():
    wrong = candidate("16:50:1:1", address="Республика Татарстан, Казань, ул. Баумана, д. 36, кв. 52")
    right = candidate("16:50:1:2")
    searcher = Responses([[wrong], [wrong, right]])
    result = CadastralSearchService(searcher, attempts=1, request_delay=0).run("Республика Татарстан, Казань, ул. Баумана 52")
    assert result.status == "НАЙДЕНО"
    assert result.candidates[0]["cadastral_number"] == "16:50:1:2"
    assert len(result.rejected) == 1
    assert len(searcher.calls) == 2
    assert all(call[3] == "ул. Баумана 52" for call in searcher.calls)


def test_web_query_rejects_same_street_and_house_from_another_region():
    foreign = candidate(
        "50:01:1:1",
        address="Московская область, г. Одинцово, ул. Баумана, д. 52",
    )
    searcher = Responses([[foreign], [], []])
    result = CadastralSearchService(searcher, attempts=1, request_delay=0).run(
        "Казань, ул. Баумана 52"
    )
    assert result.status == "НЕ НАЙДЕНО"
    assert result.candidates_accepted == 0
    assert result.rejected[0].reason == "населённый пункт"
    assert all(call[2] == "Казань" for call in searcher.calls)


def test_same_number_is_deduplicated_and_reason_is_preserved():
    duplicate = candidate("16:50:1:1")
    accepted, reasons, rejected = filter_candidates_with_reasons([duplicate, duplicate], region="Республика Татарстан", locality="Казань", address="ул. Баумана 52")
    assert len(accepted) == 1
    assert reasons == {"недостаточно данных": 1}
    assert rejected[0].reason == "недостаточно данных"
