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


def test_inspect_unsupported_chains_arc_and_robinhood():
    result_arc = security.inspect_contract(CONTRACT, "arc")
    assert result_arc["unsupported_chain"] is True
    assert result_arc["risk"] == "not_assessed"
    assert result_arc["chain"] == "arc"

    result_rh = security.inspect_contract(CONTRACT, "robinhood")
    assert result_rh["unsupported_chain"] is True
    assert result_rh["risk"] == "not_assessed"
    assert result_rh["chain"] == "robinhood"


def test_inspect_goplus_normalizes_fields_and_evidence():
    def get(url, **kwargs):
        return Response({
            "code": 1,
            "message": "OK",
            "result": {
                CONTRACT: {
                    "token_name": "TestToken",
                    "token_symbol": "TT",
                    "is_open_source": "1",
                    "is_proxy": "0",
                    "is_honeypot": "0",
                }
            }
        })

    result = security.inspect_contract(CONTRACT, "arbitrum", "honey", "key", http_get=get)
    assert result["risk"] == "looks_legit"
    assert result["details"]["token"]["name"] == "TestToken"
    assert result["details"]["verified"] is True
    assert result["details"]["proxy"] is False
    assert result["details"]["honeypot"] is False


def test_inspect_empty_provider_response():
    # GoPlus returns empty dict when contract is not indexed
    def get_goplus(url, **kwargs):
        return Response({"code": 1, "message": "OK", "result": {}})

    result = security.inspect_contract(CONTRACT, "arbitrum", "honey", "key", http_get=get_goplus)
    assert result["risk"] == "not_assessed"
    assert result["details"]["empty_response"] is True

    # Honeypot returns 404 when pair not found
    def get_honeypot(url, **kwargs):
        return Response({"code": 404, "error": "Token not found"}, status=404)

    res_hp = security.inspect_contract(CONTRACT, "base", "honey", "key", http_get=get_honeypot)
    assert res_hp["risk"] == "not_assessed"
    assert res_hp["details"]["empty_response"] is True


def test_inspect_goplus_system_error_raises_runtime_error():
    def get(url, **kwargs):
        return Response({"code": 5000, "message": "system error", "result": None})

    try:
        security.inspect_contract(CONTRACT, "arbitrum", "honey", "key", http_get=get)
    except RuntimeError as exc:
        assert "system error" in str(exc)
    else:
        raise AssertionError("GoPlus system error did not raise RuntimeError")


def test_inspect_dynamic_unsupported_chain_response():
    def get(url, **kwargs):
        return Response({"code": 2022, "message": "The main chain is not supported", "result": None})

    result = security.inspect_contract(CONTRACT, "arbitrum", "honey", "key", http_get=get)
    assert result["unsupported_chain"] is True
    assert result["risk"] == "not_assessed"

