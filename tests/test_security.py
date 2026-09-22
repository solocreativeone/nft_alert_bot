import security


CONTRACT = "0x" + "a" * 40


class Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError("provider failure")

    def json(self):
        return self.payload


def test_inspect_uses_honeypot_for_supported_chain_and_normalizes_response():
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response({
            "token": {"name": "Example", "symbol": "EX"},
            "summary": {"risk": "medium", "riskLevel": 35, "flags": [{"flag": "high_tax"}]},
            "honeypotResult": {"isHoneypot": False},
            "contractCode": {"openSource": True, "isProxy": False},
        })

    result = security.inspect_contract(CONTRACT.upper(), "base", "honey", "goplus", http_get=get)
    assert result["chain"] == "base"
    assert result["risk"] == "suspicious"
    assert result["details"]["token"]["name"] == "Example"
    assert calls[0][0].endswith("IsHoneypot")
    assert calls[0][1]["headers"]["X-API-KEY"] == "honey"


def test_inspect_uses_goplus_for_other_configured_chain():
    def get(url, **kwargs):
        assert "token_security/42161" in url
        assert kwargs["headers"]["Authorization"] == "Bearer key"
        return Response({"result": {CONTRACT: {"is_open_source": "1", "is_proxy": "0"}}})

    result = security.inspect_contract(CONTRACT, "arbitrum", "honey", "key", http_get=get)
    assert result["risk"] == "looks_legit"


def test_invalid_contract_and_provider_failure():
    try:
        security.inspect_contract("0xinvalid")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid contract accepted")

    def fail(*args, **kwargs):
        raise RuntimeError("offline")

    try:
        security.inspect_contract(CONTRACT, http_get=fail)
    except RuntimeError:
        pass
    else:
        raise AssertionError("provider failure was hidden from the command layer")
