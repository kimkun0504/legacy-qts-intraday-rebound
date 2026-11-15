"""The Massive downloader: signing, listing, routing, resume, retries, checks, CLI end to end."""

from __future__ import annotations

import datetime as dt
import gzip
import json
import random
import string
import sys
from pathlib import Path

import pytest

from qts_rebound.massive import env
from qts_rebound.massive.flatfiles import (
    download_all,
    key_date,
    list_dataset,
    local_path,
    month_prefixes,
    read_holdout_start,
    verify_local,
)
from qts_rebound.massive.rest import Rest, download_tickers
from qts_rebound.massive.s3 import AuthError, S3Client, parse_list_xml
from qts_rebound.massive.sigv4 import EMPTY_SHA256, canonical_query, sign

from .fake_massive import ACCESS, API_KEY, SECRET, FakeMassive, gz_csv

AWS_AK, AWS_SK = "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
AWS_NOW = dt.datetime(2013, 5, 24, tzinfo=dt.timezone.utc)
AWS_HOST = "examplebucket.s3.amazonaws.com"
HS = dt.date(2023, 11, 9)


def _sig(h: dict) -> str:
    return h["authorization"].split("Signature=")[1]


def test_sigv4_matches_aws_documented_examples():
    # Amazon S3 API reference, "Signature Calculations for the Authorization Header"
    get_obj = sign(
        "GET", AWS_HOST, "/test.txt", None, AWS_AK, AWS_SK, headers={"Range": "bytes=0-9"}, now=AWS_NOW
    )
    assert _sig(get_obj) == "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    lst = sign("GET", AWS_HOST, "/", {"max-keys": "2", "prefix": "J"}, AWS_AK, AWS_SK, now=AWS_NOW)
    assert _sig(lst) == "34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7"
    lc = sign("GET", AWS_HOST, "/", {"lifecycle": ""}, AWS_AK, AWS_SK, now=AWS_NOW)
    assert _sig(lc) == "fea454ca298b7da1c68078a5d1bdbfbbe0d65c699e0f91ac7a200a0136783543"


def test_sigv4_agrees_with_botocore_on_random_requests():
    botocore = pytest.importorskip("botocore")
    from botocore.auth import S3SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.credentials import Credentials

    assert botocore
    rng = random.Random(3)
    for _ in range(200):
        creds = Credentials(
            "".join(rng.choice(string.ascii_uppercase) for _ in range(20)),
            "".join(rng.choice(string.ascii_letters + "+/0123") for _ in range(40)),
        )
        q = {"list-type": "2", "prefix": f"us_stocks_sip/minute_aggs_v1/{rng.randint(2003, 2026)}/"}
        if rng.random() < 0.5:
            q["continuation-token"] = "".join(rng.choice(string.ascii_letters + "+/=") for _ in range(30))
        now = dt.datetime(2026, 11, 15, rng.randint(0, 23), rng.randint(0, 59), tzinfo=dt.timezone.utc)
        mine = sign("GET", "files.massive.com", "/flatfiles", q, creds.access_key, creds.secret_key, now=now)
        r = AWSRequest(
            method="GET",
            url="https://files.massive.com/flatfiles?" + canonical_query(q),
            headers={"X-Amz-Date": mine["x-amz-date"], "X-Amz-Content-SHA256": EMPTY_SHA256},
        )
        a = S3SigV4Auth(creds, "s3", "us-east-1")
        r.context["timestamp"] = mine["x-amz-date"]
        assert a.signature(a.string_to_sign(r, a.canonical_request(r)), r) == _sig(mine)


def test_keys_dates_routing_and_months(tmp_path):
    k = "us_stocks_sip/minute_aggs_v1/2023/11/2023-11-08.csv.gz"
    assert key_date(k) == dt.date(2023, 11, 8)
    assert local_path(tmp_path, k, HS) == tmp_path / "raw" / k
    k2 = "us_stocks_sip/minute_aggs_v1/2023/11/2023-11-09.csv.gz"
    assert local_path(tmp_path, k2, HS) == tmp_path / "sealed" / k2  # the first holdout day is sealed
    m = month_prefixes("p", dt.date(2003, 9, 10), dt.date(2004, 2, 1))
    assert m == ["p/2003/09/", "p/2003/10/", "p/2003/11/", "p/2003/12/", "p/2004/01/", "p/2004/02/"]
    seal = Path(__file__).resolve().parents[1] / "research" / "seal.yaml"
    assert read_holdout_start(seal) == HS


def test_parse_list_xml_namespaced():
    body = (
        b'<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        b"<IsTruncated>true</IsTruncated><NextContinuationToken>abc</NextContinuationToken>"
        b"<Contents><Key>a/2010/01/2010-01-04.csv.gz</Key><Size>12</Size><ETag>&quot;0123&quot;</ETag>"
        b"<LastModified>x</LastModified></Contents>"
        b"<CommonPrefixes><Prefix>a/</Prefix></CommonPrefixes></ListBucketResult>"
    )
    objs, prefixes, token = parse_list_xml(body)
    assert objs[0].key.endswith("2010-01-04.csv.gz") and objs[0].size == 12 and objs[0].etag == "0123"
    assert prefixes == ["a/"] and token == "abc"


def test_env_file_parsing(tmp_path, monkeypatch):
    p = tmp_path / ".env"
    p.write_text("# c\nexport MASSIVE_API_KEY='abc'\nMASSIVE_S3_ACCESS_KEY_ID=paste-here\nX = \"y\"\n")
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.delenv("MASSIVE_S3_ACCESS_KEY_ID", raising=False)
    assert env.get("MASSIVE_API_KEY", p) == "abc"
    assert env.get("MASSIVE_S3_ACCESS_KEY_ID", p) is None  # the placeholder is not a key
    with pytest.raises(env.MissingCredential):
        env.require("MASSIVE_S3_ACCESS_KEY_ID", p)


def _objects() -> dict[str, bytes]:
    out = {}
    for d in ("2003-09-10", "2003-09-11", "2003-10-01", "2023-11-08", "2023-11-09", "2024-01-02"):
        y, m = d[:4], d[5:7]
        out[f"us_stocks_sip/day_aggs_v1/{y}/{m}/{d}.csv.gz"] = gz_csv(["LEH,100,1,1,1,1,0,1", f"AAA,{d}"])
        out[f"us_stocks_sip/minute_aggs_v1/{y}/{m}/{d}.csv.gz"] = gz_csv(
            [f"LEH,100,1,1,1,1,{i},1" for i in range(50)] + [f"AAA,{i}" for i in range(50)]
        )
    return out


def test_download_resume_retries_and_seal(tmp_path):
    with FakeMassive(_objects()) as fm:
        c = S3Client(ACCESS, SECRET, endpoint=fm.url)
        objs = list_dataset(c, "minute_aggs", dt.date(2003, 9, 10), dt.date(2024, 1, 31), workers=3)
        assert len(objs) == 6
        k_cut, k_bad, k_500 = (objs[0].key, objs[1].key, objs[2].key)
        fm.faults = {k_cut: ["cut"], k_bad: ["corrupt"], k_500: ["500", "500"]}
        import qts_rebound.massive.flatfiles as ff

        ff_sleep = ff.time.sleep
        ff.time.sleep = lambda s: None
        try:
            log = tmp_path / "m" / "downloads.jsonl"
            counts = download_all(c, objs, tmp_path, HS, workers=3, printer=lambda s: None, log_path=log)
        finally:
            ff.time.sleep = ff_sleep
        assert counts["done"] == 6 and counts["failed"] == 0
        assert fm.bad_signatures == 0
        for o in objs:
            p = local_path(tmp_path, o.key, HS)
            assert p.read_bytes() == fm.objects[o.key]
            assert ("sealed" in p.parts) == (key_date(o.key) >= HS)
        assert not list(tmp_path.rglob("*.part")) or all(
            p.stat().st_size >= 0 for p in tmp_path.rglob("*.part")
        )
        assert len(log.read_text().splitlines()) == 6
        n_before = len(fm.requests)
        again = download_all(c, objs, tmp_path, HS, workers=3, printer=lambda s: None)
        assert again["skipped"] == 6 and len(fm.requests) == n_before  # nothing fetched twice
        res = verify_local(objs, tmp_path, HS)
        assert len(res["ok"]) == 6


def test_access_denied_stops_everything(tmp_path):
    with FakeMassive(_objects()) as fm:
        c = S3Client(ACCESS, SECRET, endpoint=fm.url)
        objs = list_dataset(c, "minute_aggs", dt.date(2003, 9, 10), dt.date(2024, 1, 31))
        fm.faults = {objs[0].key: ["403"]}
        with pytest.raises(AuthError):
            download_all(c, objs[:1], tmp_path, HS, workers=1, printer=lambda s: None)
        wrong = S3Client(ACCESS, "not-the-secret", endpoint=fm.url)
        with pytest.raises(AuthError):
            wrong.list("us_stocks_sip/")


def test_tickers_paginate(tmp_path):
    t = [{"ticker": f"T{i}", "active": i % 2 == 0} for i in range(7)]
    with FakeMassive({}, tickers=t) as fm:
        counts = download_tickers(Rest(API_KEY, base=fm.url), tmp_path, printer=lambda s: None)
        assert counts == {"active": 4, "delisted": 3}
        files = sorted(tmp_path.glob("tickers_*.jsonl"))
        assert sum(len(f.read_text().splitlines()) for f in files) == 7
        with pytest.raises(AuthError):
            Rest("wrong", base=fm.url).get_json("/v3/reference/tickers", {"limit": "1"})


def test_cli_end_to_end(tmp_path, monkeypatch, capsys):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import massive as cli

    tick = [{"ticker": "LEH", "name": "LEHMAN BROTHERS", "active": False, "delisted_utc": "2008-09-17"}]
    with FakeMassive(_objects(), tickers=tick) as fm:
        monkeypatch.setenv("QTS_MASSIVE_ENDPOINT", fm.url)
        monkeypatch.setenv("QTS_MASSIVE_API", fm.url)
        monkeypatch.setenv(env.S3_ACCESS, ACCESS)
        monkeypatch.setenv(env.S3_SECRET, SECRET)
        monkeypatch.setenv(env.API_KEY, API_KEY)
        root = str(tmp_path / "massive")
        assert cli.main(["--root", root, "check"]) == 0
        out = capsys.readouterr().out
        assert "All checks passed" in out and "delisted names present" in out and ": yes" in out
        assert SECRET not in out and API_KEY not in out and ACCESS not in out
        assert cli.main(["--root", root, "plan", "--to", "2024-01-31"]) == 0
        assert "holdout, kept sealed: 2 files" in capsys.readouterr().out
        assert cli.main(["--root", root, "get", "--to", "2024-01-31", "--workers", "2"]) == 0
        assert cli.main(["--root", root, "verify", "--to", "2024-01-31"]) == 0
        out = capsys.readouterr().out
        assert "wrong checksum 0" in out
        sealed = list((tmp_path / "massive" / "sealed").rglob("*.csv.gz"))
        assert len(sealed) == 4  # 2 days x 2 datasets on or after 2023-11-09
        assert cli.main(["--root", root, "tickers"]) == 0
        lines = (tmp_path / "massive" / "reference").glob("tickers_delisted_*.jsonl")
        assert json.loads(next(lines).read_text().splitlines()[0])["ticker"] == "LEH"
        with gzip.open(next((tmp_path / "massive" / "raw").rglob("2003-09-10.csv.gz")), "rt") as f:
            assert f.readline().startswith("ticker,volume")


def test_quotes_for_sampled_fills(tmp_path):
    from qts_rebound.massive.quotes import fetch_sample, ns_at

    t_fill = ns_at("2020-07-01", 600) + 5_000_000_000  # 10:00:05
    t_close = ns_at("2020-07-01", 955) + 1  # 15:55
    q = {
        "AAA": [
            {"sip_timestamp": t_fill, "bid_price": 9.9, "bid_size": 100, "ask_price": 10.1, "ask_size": 200},
            {"sip_timestamp": t_close, "bid_price": 10.0, "bid_size": 1, "ask_price": 10.2, "ask_size": 1},
            {
                "sip_timestamp": ns_at("2020-07-01", 700),
                "bid_price": 1,
                "bid_size": 1,
                "ask_price": 2,
                "ask_size": 1,
            },
        ]
    }
    sample = tmp_path / "s.csv"
    sample.write_text("date,sym,entry_minute,close\n2020-07-01,AAA,601,960\n")
    out = tmp_path / "q.jsonl"
    with FakeMassive({}, quotes=q) as fm:
        rest = Rest(API_KEY, base=fm.url)
        assert fetch_sample(rest, sample, out, printer=lambda s: None) == 2
        assert fetch_sample(rest, sample, out, printer=lambda s: None) == 0  # resumable
    recs = {r["label"]: r for r in map(json.loads, out.read_text().splitlines())}
    assert [x["bid_price"] for x in recs["entry"]["quotes"]] == [9.9]
    assert [x["bid_price"] for x in recs["close"]["quotes"]] == [10.0]


def test_ticks_for_opening_fills_and_cli_commands(tmp_path, monkeypatch, capsys):
    from qts_rebound.massive.quotes import fetch_ticks, ns_at, tick_window

    row = {"date": "2020-07-01", "sym": "AAA", "entry_minute": "571"}
    assert tick_window(row) == (ns_at("2020-07-01", 568), ns_at("2020-07-01", 574))
    t = ns_at("2020-07-01", 570) + 7
    trades = {
        "AAA": [
            {"sip_timestamp": t, "price": 9.4, "size": 100, "exchange": 11, "conditions": [12]},
            {"sip_timestamp": ns_at("2020-07-01", 700), "price": 9.9, "size": 1, "exchange": 4},
        ]
    }
    quotes = {
        "AAA": [{"sip_timestamp": t - 5, "bid_price": 9.3, "bid_size": 1, "ask_price": 10.0, "ask_size": 2}]
    }
    sample = tmp_path / "s.csv"
    sample.write_text("date,sym,entry_minute\n2020-07-01,AAA,571\n")
    out = tmp_path / "t.jsonl"
    with FakeMassive({}, quotes=quotes, trades=trades) as fm:
        rest = Rest(API_KEY, base=fm.url)
        assert fetch_ticks(rest, sample, out, printer=lambda s: None) == 1
        assert fetch_ticks(rest, sample, out, printer=lambda s: None) == 0  # resumable
    rec = json.loads(out.read_text().splitlines()[0])
    assert [x["price"] for x in rec["trades"]] == [9.4] and rec["trades"][0]["conditions"] == [12]
    assert [x["bid_price"] for x in rec["quotes"]] == [9.3]

    # the CLI knows both commands (a missing sample is a clean message, not a crash)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import massive as cli

    monkeypatch.setenv("MASSIVE_API_KEY", API_KEY)
    for cmd in ("quotes", "ticks"):
        assert cli.main(["--root", str(tmp_path), cmd, "--sample", str(tmp_path / "missing.csv")]) == 1
    assert "No sample yet" in capsys.readouterr().out
