from szlcsc_mcp.barcode import parse_szlcsc_barcode


def test_parse_szlcsc_bag_qr_payload():
    raw = "{pc:C6119790,pm:CGA0402X5R224K250GT,qty:100,mc:,cc:1,pdi:200616857,hp:null}"
    parsed = parse_szlcsc_barcode(raw)
    assert parsed["recognized"] is True
    assert parsed["lcsc_code"] == "C6119790"
    assert parsed["mpn"] == "CGA0402X5R224K250GT"
    assert parsed["quantity"] == 100
    assert parsed["fields"]["hp"] is None
