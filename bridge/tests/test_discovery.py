from handy_bridge.discovery import should_readvertise


# --- mudanca de rede ---------------------------------------------------------


def test_the_same_address_needs_no_new_announcement():
    assert should_readvertise("192.168.0.113", "192.168.0.113") is False


def test_a_new_address_needs_a_new_announcement():
    # Carrying the laptop to another house is the case this exists for: the
    # advertisement still names the old router's address, and the device looks
    # for a bridge that is no longer there.
    assert should_readvertise("192.168.0.113", "10.0.0.42") is True


def test_losing_the_network_does_not_withdraw_the_announcement():
    # An address that cannot be read is not the same as a changed one. Tearing
    # down mDNS on a blip would take the bridge off the network for no reason.
    assert should_readvertise("192.168.0.113", "") is False
    assert should_readvertise("192.168.0.113", None) is False


def test_coming_up_before_the_network_does_announces_once_it_arrives():
    assert should_readvertise("", "192.168.0.113") is True
