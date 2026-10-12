from app import gateway


def test_ini_written_privately_then_scrubbed(tmp_path, monkeypatch):
    ini = tmp_path / "run" / "ibc.ini"
    monkeypatch.setattr(gateway, "INI", ini)
    gateway._write_private(ini, gateway._ini("eric", "s3cret!", "paper", takeover=False))
    text = ini.read_text()
    assert "IbPassword=s3cret!" in text and "TradingMode=paper" in text
    assert "ExistingSessionDetectedAction=secondary" in text

    gateway._scrub()
    text = ini.read_text()
    assert "s3cret!" not in text and "eric" not in text
    assert "TradingMode=paper" in text and "CommandServerPort=" in text  # IBC restarts still need these


def test_classify_real_gateway_1045_lines():
    # Lines copied from a real failed login (bad password) on Gateway 10.45 / IBC 3.24.2.
    seen = [gateway.classify(l) for l in [
        "2026-10-12 13:44:07:877 IBC: Login dialog WINDOW_OPENED: LoginState is LOGGED_OUT",
        "2026-10-12 13:44:07:979 IBC: Setting password",
        "2026-10-12 13:44:08:755 IBC: detected frame entitled: Authenticating...; event=Opened",
        "2026-10-12 13:44:08:982 IBC: detected dialog entitled: Unrecognized Username or Password; event=Opened",
    ]]
    assert seen == ["logging_in", None, None, "error:bad_login"]
    m = gateway.DIALOG.search("IBC: detected dialog entitled: Some New Dialog; event=Opened")
    assert m and m.group(1) == "Some New Dialog"


def test_takeover_uses_primaryoverride():
    assert "ExistingSessionDetectedAction=primaryoverride" in gateway._ini("u", "p", "live", takeover=True)
