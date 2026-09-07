from linuxdo_monitor.matcher import KeywordMatcher, is_regex_keyword


def test_plain_keyword_is_case_insensitive():
    m = KeywordMatcher()
    assert m.match("Docker 部署求助", ["docker"]) == ["docker"]


def test_case_sensitive_mode():
    m = KeywordMatcher(case_sensitive=True)
    assert m.match("Docker", ["docker"]) == []
    assert m.match("Docker", ["Docker"]) == ["Docker"]


def test_wrapped_regex():
    m = KeywordMatcher()
    assert m.match("有没有人能求助一下", ["/求助|提问/"]) == ["/求助|提问/"]


def test_regex_prefix():
    m = KeywordMatcher()
    assert m.match("NAS 推荐", ["re:^NAS"]) == ["re:^NAS"]


def test_regex_flags():
    m = KeywordMatcher(case_sensitive=True)
    assert m.match("DOCKER", ["/docker/i"]) == ["/docker/i"]


def test_no_match_returns_empty():
    m = KeywordMatcher()
    assert m.match("随便一个标题", ["docker", "nas"]) == []


def test_invalid_regex_is_ignored():
    m = KeywordMatcher()
    assert m.match("abc", ["/[/"]) == []


def test_match_returns_unique_and_ordered():
    m = KeywordMatcher()
    assert m.match("docker nas docker", ["nas", "docker", "nas"]) == ["nas", "docker"]


def test_match_post_includes_summary_when_enabled():
    m = KeywordMatcher(match_summary=True)
    post = type("P", (), {"title": "无关标题", "summary": "其实讲的是 Docker"})()
    assert m.match_post(post, ["docker"]) == ["docker"]
    assert KeywordMatcher().match_post(post, ["docker"]) == []


def test_match_any():
    m = KeywordMatcher()
    assert m.match_any("Docker", ["docker"]) is True
    assert m.match_any("Docker", ["nas"]) is False


def test_is_regex_keyword():
    assert is_regex_keyword("/abc/") is True
    assert is_regex_keyword("re:abc") is True
    assert is_regex_keyword("abc") is False
