"""Offline checks for the direct Wi-Fi TCP observer; no sockets are opened."""

from scripts.diagnostics import diagnose_kudavpn_path_v5 as probe


def packet(category, flow, flags, length=0, **extra):
    return {
        "kind": "packet",
        "category": category,
        "flow": flow,
        "flags": flags,
        "length": length,
        **extra,
    }


def test_correlates_public_tcp_when_ssh_address_and_nat_port_differ():
    mac = [
        packet("mac_to_front", 1, "S", seq_start=123456, client_port=50100),
        packet("mac_to_front", 1, "P.", 517, seq_start=123457, seq_end=123974,
               client_port=50100),
        packet("front_to_mac", 1, "S.", ack_num=123457, client_port=50100),
        packet("front_to_mac", 1, ".", ack_num=123974, client_port=50100),
    ]
    server = [
        packet("client_to_front", 9, "S", seq_start=123456, client_port=62000,
               peer_match=False),
        packet("client_to_front", 9, "P.", 517, seq_start=123457,
               seq_end=123974, client_port=62000, peer_match=False),
        packet("front_to_client", 9, "S.", ack_num=123457, client_port=62000,
               peer_match=False),
        packet("front_to_client", 9, ".", ack_num=123974, client_port=62000,
               peer_match=False),
    ]

    result = probe.public_tcp_evidence(mac, server)

    assert result["server_syn_candidates"] == 1
    assert result["matched_flow"] == 9
    assert result["matched_incoming_payload"] == 517
    assert result["server_peer_matches_ssh"] is False
    assert result["source_port_preserved"] is False
    assert result["server_ack_sent_bytes"] == 517
    assert result["mac_ack_received_bytes"] == 517


def test_does_not_attribute_unmatched_server_flow_to_mac():
    mac = [packet("mac_to_front", 1, "S", seq_start=123456)]
    server = [
        packet("client_to_front", 9, "S", seq_start=999999, peer_match=False),
        packet("client_to_front", 9, "P.", 200, peer_match=False),
    ]

    result = probe.public_tcp_evidence(mac, server)

    assert result["server_syn_candidates"] == 0
    assert result["matched_flow"] is None
    assert result["matched_incoming_payload"] == 0
    assert result["unmatched_incoming_payload"] == 200


def test_parses_syn_ack_and_sack_without_packet_contents():
    syn = ("1760270000.111 IP 192.0.2.4.54321 > 203.0.113.4.443: "
           "Flags [S], seq 123456, win 65535, length 0")
    ack = ("1760270000.222 IP 203.0.113.4.443 > 192.0.2.4.54321: "
           "Flags [.], ack 123974, win 65535, options [sack 1 {123457:123974}], length 0")

    assert probe.parse_mac_packet(syn)["seq_start"] == 123456
    assert probe.parse_mac_packet(ack)["ack_num"] == 123974
    assert probe.parse_mac_packet(ack)["sack_blocks"] is True


def test_duplicate_segment_counts_as_retransmission():
    events = [
        packet("mac_to_front", 1, "P.", 517, seq_start=100, seq_end=617),
        packet("mac_to_front", 1, "P.", 517, seq_start=100, seq_end=617),
    ]

    measured = probe.path_bytes(probe.packet_summary(events), "mac_to_front")

    assert measured["unique"] == 517
    assert measured["wire"] == 1034
    assert measured["retransmit"] == 517
