"""Deployment gates for a second node that must never share the live pilot."""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _environment(path: Path) -> dict[str, str]:
    return dict(line.split('=', 1) for line in path.read_text().splitlines()
                if line and not line.startswith('#'))


def test_second_node_has_independent_network_and_state():
    first = ROOT / 'deploy/pilot'
    second = ROOT / 'deploy/pilot-second'
    current = _environment(first / 'agent.env.template')
    candidate = _environment(second / 'agent.env.template')
    xray = json.loads((second / 'xray-10443.json.template').read_text())

    assert candidate['NODE_AGENT_MAX_USERS'] == '1'
    assert candidate['NODE_AGENT_LISTEN'] == '127.0.0.1'
    assert candidate['NODE_AGENT_PORT'] != current['NODE_AGENT_PORT']
    assert candidate['XRAY_API_ADDRESS'] != current['XRAY_API_ADDRESS']
    assert candidate['XRAY_INBOUND_PORT'] != current['XRAY_INBOUND_PORT']
    assert {443, 8443, 9443}.issubset(set(map(int, candidate['XRAY_RESERVED_PORTS'].split(','))))
    assert 10443 not in set(map(int, candidate['XRAY_RESERVED_PORTS'].split(',')))
    assert candidate['NODE_AGENT_STATE'] != current['NODE_AGENT_STATE']
    assert candidate['XRAY_CONFIG'] != current['XRAY_CONFIG']
    assert candidate['NODE_AGENT_KEY'] != current['NODE_AGENT_KEY']
    assert candidate['NODE_AGENT_CLIENT_CA'] != current['NODE_AGENT_CLIENT_CA']
    assert candidate['XRAY_INBOUND_TAG'] == xray['inbounds'][0]['tag']
    assert xray['inbounds'][0]['port'] == int(candidate['XRAY_INBOUND_PORT'])
    assert xray['inbounds'][0]['settings']['clients'] == []
    assert xray['api']['listen'] == candidate['XRAY_API_ADDRESS']


def test_second_node_unit_cannot_manage_first_node_files():
    unit = (ROOT / 'deploy/pilot-second/kudavpn-pilot-two-node.service').read_text()
    assert 'User=kudavpn-pilot-two\n' in unit
    assert 'KillMode=control-group\n' in unit
    assert 'ProtectSystem=strict\n' in unit
    assert 'ReadWritePaths=/var/lib/kudavpn-pilot-two\n' in unit
    assert 'EnvironmentFile=/etc/kudavpn-pilot-two/agent.env\n' in unit
    assert 'TMPDIR=/var/lib/kudavpn-pilot-two\n' in unit
    assert 'kudavpn-pilot-node.service' not in unit
    assert 'ExecStart=/opt/kudavpn-pilot-two/' in unit
