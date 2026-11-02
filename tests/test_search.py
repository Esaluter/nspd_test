from nspd_test.search import CadastralSearchService


class MockSearcher:
    def __init__(self, responses): self.responses=list(responses); self.calls=[]
    def search(self, query, **kwargs): self.calls.append(query); value=self.responses.pop(0); return value


def item(number, address="Белгородская область, Белгород, ул. Садовая, д. 5"):
    return {"cadastral_number":number,"address":address,"category_name":"Здание","cadastral_quarter":"31:16:1"}


def test_unambiguous_result():
    result=CadastralSearchService(MockSearcher([[item("31:16:1:1")]]),attempts=1,request_delay=0).run("Белгородская область, г. Белгород, ул. Садовая, д. 5")
    assert result.status=="НАЙДЕНО" and result.candidates_accepted==1


def test_multiple_candidates_remain_ambiguous():
    result=CadastralSearchService(MockSearcher([[item("31:16:1:1"),item("31:16:1:2")]]),attempts=1,request_delay=0).run("Белгородская область, г. Белгород, ул. Садовая, д. 5")
    assert result.status=="НЕОДНОЗНАЧНО"
    assert len(result.candidates)==2
    assert result.ambiguity_reason


def test_api_error_has_error_status():
    class Broken:
        def search(self,*args,**kwargs): raise TimeoutError
    result=CadastralSearchService(Broken(),attempts=1).run("Москва, ул. Тверская, д. 1")
    assert result.status=="ОШИБКА" and result.error=="TimeoutError"

