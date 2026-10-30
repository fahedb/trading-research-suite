"""The public preview must work without private data and disclose its provenance."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from explorer.demo import RUN_IDS, app

client = TestClient(app)


def test_demo_import_does_not_load_research_readers() -> None:
    code = (
        'import sys, json; import explorer.demo; '
        'print(json.dumps([m for m in sys.modules if m.startswith("trading_research") '
        'or m in {"explorer.api", "explorer.config", "explorer.index_read", '
        '"explorer.bars", "explorer.trades", "explorer.signals"}]))'
    )
    loaded = subprocess.check_output([sys.executable, '-c', code], text=True)
    assert json.loads(loaded) == []


@pytest.mark.parametrize('page', ['/', '/family', '/run', '/chart'])
def test_every_demo_screen_labels_generated_data(page: str) -> None:
    response = client.get(page)
    assert response.status_code == 200
    assert 'Synthetic demo' in response.text
    assert 'Made-up prices and trades' in response.text
    assert 'window.SYNTHETIC_DEMO = true' in response.text


def test_browsing_reconciles_the_illustrative_trades_and_overview() -> None:
    groups = client.get('/api/experiments').json()
    assert groups['synthetic'] is True
    assert groups['run_count'] == len(RUN_IDS)
    for run in RUN_IDS:
        detail = client.get(f'/api/runs/{run}').json()
        assert detail['canonical'] is False
        assert detail['state'] == 'DEMO'
        log = client.get(f'/api/runs/{run}/trades').json()
        overview = client.get(f'/api/runs/{run}/overview').json()
        assert log['synthetic'] is overview['synthetic'] is True
        assert len(log['trades']) == overview['trade_count']
        assert overview['net_pnl_sum'] == pytest.approx(
            sum(t['net_pnl'] for t in log['trades']))
        assert overview['curve'][-1]['v'] == overview['net_pnl_sum']
        assert sum(f['trade_count'] for f in overview['folds']) == log['count']


@pytest.mark.parametrize('timeframe', ['M1', 'M5', 'M15', 'H1'])
def test_chart_candles_contain_the_snapped_trade_anchors(timeframe: str) -> None:
    run = RUN_IDS[0]
    trade = client.get(f'/api/runs/{run}/trades').json()['trades'][0]
    response = client.get(f'/api/runs/{run}/bars', params={
        'entry_key': trade['entry_key'], 'exit_key': trade['exit_key'],
        'timeframe': timeframe,
    })
    assert response.status_code == 200
    data = response.json()
    assert data['synthetic'] is True
    keys = [bar['t'] for bar in data['bars']]
    assert keys == sorted(set(keys))
    assert data['entry_bar_key'] in keys
    assert data['exit_bar_key'] in keys
    for bar in data['bars']:
        assert bar['l'] <= min(bar['o'], bar['c']) <= max(bar['o'], bar['c']) <= bar['h']


def test_unknown_run_and_invalid_windows_are_refused() -> None:
    assert client.get('/api/runs/not-a-demo').status_code == 404
    assert client.get(f'/api/runs/{RUN_IDS[0]}/bars', params={
        'entry_key': 0, 'exit_key': 1, 'timeframe': 'M1',
    }).status_code == 400
